"""Providers flagged ``anthropic_signature_passthrough`` keep the
direct-Anthropic thinking replay contract even though their base_url is
classified third-party for auth purposes.

Motivation: a self-hosted Claude LB forwards content blocks to real Anthropic
verbatim, so signatures generated through it validate on replay. Stripping
thinking there (the default third-party behaviour) breaks interleaved-thinking
reasoning continuity mid tool-loop on the production Claude route.

The signature-400 recovery must strip both replay channels: a flagged LB
shares the native route's durable suppression, and every other route's
one-request repair drops the ordered carrier with ``reasoning_details``.
"""

import copy
from types import SimpleNamespace

import pytest

import agent.anthropic_thinking_policy as policy
from agent.anthropic_adapter import convert_messages_to_anthropic
from agent.anthropic_thinking_replay import tracks_rejected_thinking
from agent.transports import get_transport
from hermes_constants import hermes_home_key, reset_hermes_home_override, set_hermes_home_override

SIG = "sig-lb"
LB = "http://10.0.0.114:2455"
OTHER_PROXY = "https://foundry.example.com/anthropic"


@pytest.fixture(autouse=True)
def _passthrough_urls(_isolate_hermes_home, monkeypatch):
    """Pin the active profile's trusted-passthrough set to the LB URL (no config.yaml I/O)."""
    monkeypatch.setattr(
        policy, "_signature_passthrough_urls_cache", {hermes_home_key(): frozenset({LB.lower()})}
    )


def _tool_loop_messages():
    """One in-flight tool loop: signed thinking interleaved with tool_use,
    then the tool result — the exact shape whose replay continuity matters."""
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="check a.py first", signature=SIG),
            SimpleNamespace(type="tool_use", id="toolu_1", name="read_file", input={"path": "a.py"}),
        ],
        stop_reason="tool_use",
        usage=None,
    )
    normalized = get_transport("anthropic_messages").normalize_response(response)
    provider_data = normalized.provider_data or {}
    stored = {
        "role": "assistant",
        "content": normalized.content or "",
        "reasoning_details": provider_data.get("reasoning_details"),
        "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
            for tc in (normalized.tool_calls or [])
        ],
    }
    if provider_data.get("anthropic_content_blocks"):
        stored["anthropic_content_blocks"] = provider_data["anthropic_content_blocks"]
    return [
        {"role": "user", "content": "inspect a.py"},
        stored,
        {"role": "tool", "tool_call_id": "toolu_1", "content": "a.py: ok"},
    ]


def _latest_assistant_thinking(base_url, messages=None):
    _sys, out = convert_messages_to_anthropic(
        messages or _tool_loop_messages(), base_url=base_url, model="claude-fable-5"
    )
    assistants = [m for m in out if m.get("role") == "assistant"]
    return [
        b for b in assistants[-1]["content"]
        if isinstance(b, dict) and b.get("type") == "thinking"
    ]


def test_passthrough_lb_keeps_signed_thinking_in_tool_loop():
    thinking = _latest_assistant_thinking(LB)
    assert thinking and thinking[0].get("signature") == SIG


def test_passthrough_matches_with_trailing_slash_and_case():
    thinking = _latest_assistant_thinking("HTTP://10.0.0.114:2455/")
    assert thinking and thinking[0].get("signature") == SIG


def test_unflagged_third_party_still_strips():
    assert not _latest_assistant_thinking(OTHER_PROXY)


@pytest.mark.parametrize("model", ["claude-fable-5", "claude-sonnet-4-5"])
def test_passthrough_matches_direct_anthropic_on_earlier_turns(model):
    """Parity with direct Anthropic on assistant turns before the in-flight
    tool loop: a model that keeps prior thinking replays them signed, a
    last-turn-only generation strips them, exactly as api.anthropic.com."""

    def first_turn_thinking(base_url):
        messages = _tool_loop_messages() + [
            {"role": "assistant", "content": "done: a.py is fine"},
            {"role": "user", "content": "now check b.py"},
        ]
        _sys, out = convert_messages_to_anthropic(messages, base_url=base_url, model=model)
        assistants = [m for m in out if m.get("role") == "assistant"]
        assert len(assistants) == 2
        return [
            b for b in assistants[0]["content"]
            if isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking")
        ]

    assert first_turn_thinking(LB) == first_turn_thinking(None)
    assert bool(first_turn_thinking(LB)) == policy.model_preserves_prior_thinking(model)


def test_passthrough_shares_the_native_route_with_accounting_and_signature_recovery():
    """The flag selects the ``native`` route itself, so context accounting and the
    signature-400 recovery (which suppresses every replay carrier) follow the
    converter instead of treating the LB as an unknown relay."""
    assert policy.anthropic_thinking_route(LB, "claude-fable-5") == "native"
    assert policy.native_anthropic_preserves_prior_thinking(LB, "claude-fable-5")
    assert tracks_rejected_thinking(
        SimpleNamespace(api_mode="anthropic_messages", base_url=LB, model="claude-fable-5")
    )
    assert policy.anthropic_thinking_route(OTHER_PROXY, "claude-fable-5") == "third_party"
    assert not tracks_rejected_thinking(
        SimpleNamespace(api_mode="anthropic_messages", base_url=OTHER_PROXY, model="claude-fable-5")
    )


def test_config_loader_normalizes_urls(monkeypatch, tmp_path):
    """The config reader picks up flagged providers and normalizes base URLs. The flag is read
    through the config loader (``load_config_readonly``), so it is set in the profile's own
    config.yaml rather than handed to a patched path helper."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
providers:
  claude-lb:
    base_url: HTTP://10.0.0.114:2455/
    anthropic_signature_passthrough: true
  minimax:
    base_url: https://api.minimax.io/anthropic
  broken: not-a-dict
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(policy, "_signature_passthrough_urls_cache", {})
    token = set_hermes_home_override(tmp_path)
    try:
        urls = policy._get_signature_passthrough_urls()
        assert urls == frozenset({"http://10.0.0.114:2455"})
        assert policy._is_signature_passthrough_endpoint("http://10.0.0.114:2455/")
        assert not policy._is_signature_passthrough_endpoint(
            "https://api.minimax.io/anthropic"
        )
    finally:
        reset_hermes_home_override(token)


def test_unreadable_config_trusts_no_endpoint(monkeypatch):
    """Fail closed: a config the loader cannot read, including a refused backend read (a
    ``SystemExit`` subclass), trusts no endpoint and never ends the turn."""
    from hermes_cli.config_backend import ConfigBackendUnavailable

    for exc in (OSError("unreadable"), ConfigBackendUnavailable("backend refused")):
        def boom(exc=exc):
            raise exc

        monkeypatch.setattr("hermes_cli.config.load_config_readonly", boom)
        monkeypatch.setattr(policy, "_signature_passthrough_urls_cache", {})
        assert policy._read_signature_passthrough_urls() == frozenset()
        assert policy.anthropic_thinking_route(LB, "claude-fable-5") == "third_party"


def test_flag_is_scoped_to_the_profile_that_set_it(monkeypatch, tmp_path):
    """One process serves many profiles: a flag in one profile's config.yaml
    never makes the same URL trusted for another profile."""
    monkeypatch.setattr(policy, "_signature_passthrough_urls_cache", {})
    flagged, plain = tmp_path / "profiles" / "flagged", tmp_path / "profiles" / "plain"
    for home, flag in ((flagged, "true"), (plain, "false")):
        home.mkdir(parents=True)
        (home / "config.yaml").write_text(
            f"providers:\n  claude-lb:\n    base_url: {LB}\n    anthropic_signature_passthrough: {flag}\n",
            encoding="utf-8",
        )

    def route_under(home):
        token = set_hermes_home_override(home)
        try:
            return policy.anthropic_thinking_route(LB, "claude-fable-5")
        finally:
            reset_hermes_home_override(token)

    assert route_under(flagged) == "native"
    assert route_under(plain) == "third_party"
    assert route_under(flagged) == "native"


def _signature_400_retry(agent, history):
    """Drive production signature-400 recovery; return the repaired request copy."""
    from agent.error_classifier import FailoverReason
    from agent.turn_recovery import _recover_format_errors
    from agent.turn_retry_state import TurnRetryState

    before, request, retry = copy.deepcopy(history), copy.deepcopy(history), TurnRetryState()
    assert _recover_format_errors(
        agent, RuntimeError("Invalid signature in thinking block"),
        SimpleNamespace(reason=FailoverReason.thinking_signature), retry, history, request,
    )
    assert retry.thinking_sig_retry_attempted and history == before
    return request


def _recovery_agent(api_mode, base_url, model):
    return SimpleNamespace(
        api_mode=api_mode, base_url=base_url, model=model, log_prefix="",
        _vprint=lambda *args, **kwargs: None,
    )


def test_kimi_signature_retry_drops_the_ordered_carrier_and_keeps_its_contract():
    """Kimi replays carriers as-is and keeps the one-request repair. The ordered
    carrier wins over ``reasoning_details`` in the converter, so a repair that
    left it would resend the same signed blocks into the same 400."""
    kimi, model = "https://api.kimi.com/coding", "kimi-k2.5"
    history = _tool_loop_messages()
    history[1]["reasoning_content"] = "check a.py first"
    stash = [{"type": "text", "text": "stashed tool output"}]
    history[2]["_anthropic_content_blocks"] = stash
    agent = _recovery_agent("anthropic_messages", kimi, model)
    assert policy.anthropic_thinking_route(kimi, model) == "kimi"
    assert not tracks_rejected_thinking(agent)

    def assistant_blocks(messages):
        _sys, out = convert_messages_to_anthropic(messages, base_url=kimi, model=model)
        return next(m for m in out if m["role"] == "assistant")["content"]

    assert [b.get("signature") for b in assistant_blocks(history) if b["type"] == "thinking"] == [SIG]
    retry = _signature_400_retry(agent, history)
    blocks = assistant_blocks(retry)
    # No signature goes back; Kimi still gets its readable reasoning and the tool call.
    assert [b for b in blocks if b["type"] == "thinking"] == [
        {"type": "thinking", "thinking": "check a.py first"}
    ]
    assert [b["id"] for b in blocks if b["type"] == "tool_use"] == ["toolu_1"]
    # The tool row's private stash is not a thinking carrier and survives the repair.
    assert retry[2]["_anthropic_content_blocks"] == stash


def test_converse_signature_retry_drops_the_anthropic_carrier_after_stream_denied_fallback():
    """After the sticky stream-denied fallback a Claude session runs on Converse
    (``bedrock_converse``), whose replay reads ``anthropic_content_blocks`` for turns
    the AnthropicBedrock path produced; the one-request repair must drop it."""
    from agent.bedrock_adapter import convert_messages_to_converse

    model = "anthropic.claude-fable-5-v1:0"
    agent = _recovery_agent(
        "bedrock_converse", "https://bedrock-runtime.us-east-1.amazonaws.com", model
    )
    assert not tracks_rejected_thinking(agent)

    def converse_assistant_blocks(messages):
        _system, out = convert_messages_to_converse(messages, model=model)
        return next(m for m in out if m["role"] == "assistant")["content"]

    def signatures(blocks):
        return [
            b["reasoningContent"]["reasoningText"].get("signature")
            for b in blocks if "reasoningContent" in b
        ]

    history = _tool_loop_messages()
    assert signatures(converse_assistant_blocks(history)) == [SIG]
    blocks = converse_assistant_blocks(_signature_400_retry(agent, history))
    assert not signatures(blocks)
    assert [b["toolUse"]["toolUseId"] for b in blocks if "toolUse" in b] == ["toolu_1"]
