"""Tests for passive provider health wiring (passive-provider-health patch).

Real completion traffic feeds the model-routes health cache:

1. ``try_activate_fallback`` files an unhealthy verdict for the runtime being
   abandoned — but only for outage-shaped FailoverReasons, and only when the
   runtime maps to a ``providers:`` config key
2. ``note_provider_success`` clears an unhealthy verdict after a live
   completion succeeds, gated on ``has_unhealthy_verdicts`` so the steady
   state stays cheap
3. Recording is best-effort: recorder failures never break the fallback walk
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.passive_provider_health import (
    PASSIVE_UNHEALTHY_REASONS,
    note_provider_success,
    record_passive_provider_outcome,
)
from agent.error_classifier import FailoverReason
from run_agent import AIAgent


def _make_agent(fallback_model=None):
    with (
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="http://10.0.0.114:2455",
            provider="claude-lb",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            fallback_model=fallback_model,
        )
        agent.client = MagicMock()
        return agent


def _mock_client(base_url="https://api.example.com/v1", api_key="fb-key"):
    mock = MagicMock()
    mock.base_url = base_url
    mock.api_key = api_key
    return mock


_FB = {"provider": "openai", "model": "gpt-4o"}

# FailoverReasons that never file an unhealthy verdict: the failure is scoped to our
# credentials or account, this request, or one model behind the provider, so other
# traffic on the provider can still succeed. upstream_blocked (a WAF/CDN 403) stays
# out for probe parity, because the recovery probe counts every 403 healthy.
_NOT_A_PROVIDER_OUTAGE = frozenset({
    # credentials / account / entitlement
    FailoverReason.auth,
    FailoverReason.auth_permanent,
    FailoverReason.model_entitlement,
    FailoverReason.long_context_tier,
    FailoverReason.oauth_long_context_beta_forbidden,
    FailoverReason.provider_policy_blocked,
    FailoverReason.data_policy_denied,
    FailoverReason.upstream_blocked,
    FailoverReason.ssl_cert_verification,
    # one model at an aggregator, or a model the provider does not serve
    FailoverReason.upstream_rate_limit,
    FailoverReason.model_not_found,
    # this request's content or shape
    FailoverReason.content_policy_blocked,
    FailoverReason.context_overflow,
    FailoverReason.payload_too_large,
    FailoverReason.image_too_large,
    FailoverReason.image_corrupt,
    FailoverReason.format_error,
    FailoverReason.role_alternation,
    FailoverReason.invalid_encrypted_content,
    FailoverReason.multimodal_tool_content_unsupported,
    FailoverReason.reasoning_mandatory,
    FailoverReason.thinking_signature,
    FailoverReason.llama_cpp_grammar_pattern,
    FailoverReason.incomplete_response,
    # unclassifiable: never guess an outage
    FailoverReason.unknown,
})


# ── Reason classification ──────────────────────────────────────────────────


class TestUnhealthyReasonSet:
    def test_outage_shaped_reasons_included(self):
        assert {
            FailoverReason.billing,
            FailoverReason.rate_limit,
            FailoverReason.overloaded,
            FailoverReason.server_error,
            FailoverReason.timeout,
        } == set(PASSIVE_UNHEALTHY_REASONS)

    def test_request_scoped_reasons_excluded(self):
        for reason in (*_NOT_A_PROVIDER_OUTAGE, None):
            assert reason not in PASSIVE_UNHEALTHY_REASONS

    def test_every_failover_reason_has_a_passive_health_decision(self):
        """Each FailoverReason either files an unhealthy verdict or is excluded on purpose.
        A member upstream adds lands in neither set and fails here until it is classified
        (v0.22.5 added upstream_blocked, model_entitlement, incomplete_response,
        data_policy_denied and role_alternation; all excluded)."""
        assert not (_NOT_A_PROVIDER_OUTAGE & PASSIVE_UNHEALTHY_REASONS)
        assert _NOT_A_PROVIDER_OUTAGE | PASSIVE_UNHEALTHY_REASONS == set(FailoverReason)


# ── Failure hook on fallback activation ────────────────────────────────────


class TestFallbackActivationRecordsUnhealthy:
    def test_outage_reason_records_current_runtime(self):
        agent = _make_agent(fallback_model=[_FB])
        with (
            patch("agent.auxiliary_client.resolve_provider_client",
                  return_value=(_mock_client(), "gpt-4o")),
            patch("hermes_cli.model_routes.provider_key_for_runtime",
                  return_value="claude-lb") as key_mock,
            patch("hermes_cli.model_routes.record_provider_outcome") as rec_mock,
        ):
            assert agent._try_activate_fallback(reason=FailoverReason.server_error) is True
        assert key_mock.call_args.kwargs["provider"] == "claude-lb"
        assert key_mock.call_args.kwargs["base_url"] == "http://10.0.0.114:2455"
        rec_mock.assert_called_once_with("claude-lb", False, "server_error")

    def test_refusal_reason_does_not_record(self):
        agent = _make_agent(fallback_model=[_FB])
        with (
            patch("agent.auxiliary_client.resolve_provider_client",
                  return_value=(_mock_client(), "gpt-4o")),
            patch("hermes_cli.model_routes.record_provider_outcome") as rec_mock,
        ):
            assert agent._try_activate_fallback(
                reason=FailoverReason.content_policy_blocked
            ) is True
        rec_mock.assert_not_called()

    def test_reasonless_activation_does_not_record(self):
        agent = _make_agent(fallback_model=[_FB])
        with (
            patch("agent.auxiliary_client.resolve_provider_client",
                  return_value=(_mock_client(), "gpt-4o")),
            patch("hermes_cli.model_routes.record_provider_outcome") as rec_mock,
        ):
            assert agent._try_activate_fallback() is True
        rec_mock.assert_not_called()

    def test_chain_exhausted_still_records(self):
        agent = _make_agent(fallback_model=None)  # empty chain
        with patch("hermes_cli.model_routes.record_provider_outcome") as rec_mock:
            with patch("hermes_cli.model_routes.provider_key_for_runtime",
                       return_value="claude-lb"):
                assert agent._try_activate_fallback(reason=FailoverReason.overloaded) is False
        rec_mock.assert_called_once_with("claude-lb", False, "overloaded")

    def _deferred_switch(self, **kwargs):
        """One reset-deferred switch: fallback.min_switch_reset_seconds=120 and a rate
        limit that reopens in 30 s keep the turn on the primary (upstream #117484)."""
        agent = _make_agent(fallback_model=[_FB])
        with (
            patch("agent.auxiliary_client.resolve_provider_client",
                  return_value=(_mock_client(), "gpt-4o")),
            patch("hermes_cli.config.load_config",
                  return_value={"fallback": {"min_switch_reset_seconds": 120}}),
            patch("agent.fallback_cooldown.time.time", return_value=1_700_000_000),
            patch("hermes_cli.model_routes.provider_key_for_runtime",
                  return_value="claude-lb"),
            patch("hermes_cli.model_routes.record_provider_outcome") as rec_mock,
        ):
            assert agent._try_activate_fallback(
                reason=FailoverReason.rate_limit, reset_at=1_700_000_030, **kwargs
            ) is False
        assert agent.provider == "claude-lb"  # deferred: the primary stays bound
        return rec_mock

    def test_reset_deferred_switch_with_retry_pending_does_not_record(self):
        """The in-loop eager switch passes retry_pending=True: the retry loop rides the
        reset window out on the primary, so the runtime is not abandoned and no
        unhealthy verdict is filed."""
        self._deferred_switch(retry_pending=True).assert_not_called()

    def test_reset_deferred_switch_with_no_retry_left_records(self):
        """Retry exhaustion and the non-retryable exit call without retry_pending: a
        deferral there ends the turn on the primary, so the verdict is still filed."""
        self._deferred_switch().assert_called_once_with("claude-lb", False, "rate_limit")

    def test_unmapped_runtime_skips_recording(self):
        agent = _make_agent(fallback_model=[_FB])
        with (
            patch("agent.auxiliary_client.resolve_provider_client",
                  return_value=(_mock_client(), "gpt-4o")),
            patch("hermes_cli.model_routes.provider_key_for_runtime", return_value=""),
            patch("hermes_cli.model_routes.record_provider_outcome") as rec_mock,
        ):
            assert agent._try_activate_fallback(reason=FailoverReason.timeout) is True
        rec_mock.assert_not_called()

    def test_recorder_failure_never_breaks_activation(self):
        agent = _make_agent(fallback_model=[_FB])
        with (
            patch("agent.auxiliary_client.resolve_provider_client",
                  return_value=(_mock_client(), "gpt-4o")),
            patch("hermes_cli.model_routes.provider_key_for_runtime",
                  side_effect=RuntimeError("config exploded")),
        ):
            assert agent._try_activate_fallback(reason=FailoverReason.server_error) is True


class _RateLimited(Exception):
    """Primary 429 as the conversation loop sees it (classified rate_limit)."""

    status_code = 429

    def __init__(self):
        super().__init__("Error code: 429 - rate limit exceeded")
        self.response = SimpleNamespace(headers={})
        self.body = {"error": {"message": "rate limit exceeded"}}


class TestResetDeferredSwitchAcrossTheRetryLoop:
    """fallback.min_switch_reset_seconds through the real retry loop: the eager in-loop
    deferrals file nothing while retries remain, and once retries are spent the
    exhaustion call (still deferred, so the turn ends on the primary) files exactly
    one unhealthy verdict for it."""

    def test_verdict_filed_once_when_retries_are_spent(self):
        import time

        from hermes_cli.config import load_config
        from hermes_constants import get_hermes_home

        (get_hermes_home() / "config.yaml").write_text(
            "fallback:\n  min_switch_reset_seconds: 120\n", encoding="utf-8")
        assert load_config()["fallback"]["min_switch_reset_seconds"] == 120

        agent = _make_agent(fallback_model=[_FB])
        agent._api_max_retries = 2
        attempts = []
        recorded_after = []

        def _primary_429(*_args, **_kwargs):
            attempts.append(agent.provider)
            raise _RateLimited()

        def _record(key, healthy, reason):
            recorded_after.append((len(attempts), key, healthy, reason))

        with (
            patch.object(agent, "_interruptible_api_call", side_effect=_primary_429),
            patch.object(agent, "_interruptible_streaming_api_call", side_effect=_primary_429),
            patch.object(agent, "_extract_api_error_context",
                         side_effect=lambda _err: {"reset_at": time.time() + 30}),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch("agent.auxiliary_client.resolve_provider_client",
                  return_value=(_mock_client(), "gpt-4o")),
            patch("agent.model_metadata.get_model_context_length", return_value=200000),
            patch("hermes_cli.model_routes.provider_key_for_runtime",
                  return_value="claude-lb"),
            patch("hermes_cli.model_routes.record_provider_outcome", side_effect=_record),
        ):
            result = agent.run_conversation("hello")

        assert result.get("failed") or result.get("error")
        assert attempts and set(attempts) == {"claude-lb"}  # never switched
        assert recorded_after == [(len(attempts), "claude-lb", False, "rate_limit")]


# ── Success hook ───────────────────────────────────────────────────────────


class TestSuccessHook:
    def test_gated_off_when_no_unhealthy_verdicts(self):
        agent = SimpleNamespace(provider="claude-lb", base_url="http://10.0.0.114:2455")
        with (
            patch("hermes_cli.model_routes.has_unhealthy_verdicts", return_value=False),
            patch("hermes_cli.model_routes.record_provider_outcome") as rec_mock,
        ):
            note_provider_success(agent)
        rec_mock.assert_not_called()

    def test_records_recovery_when_unhealthy_verdicts_exist(self):
        agent = SimpleNamespace(provider="claude-lb", base_url="http://10.0.0.114:2455")
        with (
            patch("hermes_cli.model_routes.has_unhealthy_verdicts", return_value=True),
            patch("hermes_cli.model_routes.provider_key_for_runtime",
                  return_value="claude-lb"),
            patch("hermes_cli.model_routes.record_provider_outcome") as rec_mock,
        ):
            note_provider_success(agent)
        rec_mock.assert_called_once()
        args = rec_mock.call_args.args
        assert args[0] == "claude-lb"
        assert args[1] is True

    def test_gate_failure_is_swallowed(self):
        agent = SimpleNamespace(provider="claude-lb", base_url="")
        with patch("hermes_cli.model_routes.has_unhealthy_verdicts",
                   side_effect=RuntimeError("boom")):
            note_provider_success(agent)  # must not raise


# ── Helper robustness ──────────────────────────────────────────────────────


class TestRecordHelper:
    def test_missing_agent_attrs_default_to_empty(self):
        with (
            patch("hermes_cli.model_routes.provider_key_for_runtime",
                  return_value="") as key_mock,
            patch("hermes_cli.model_routes.record_provider_outcome") as rec_mock,
        ):
            record_passive_provider_outcome(SimpleNamespace(), False, "timeout")
        assert key_mock.call_args.kwargs == {"provider": "", "base_url": ""}
        rec_mock.assert_not_called()
