"""Fail-fast on instant transport failure streaks.

A consecutive run of sub-2s transport failures (connection refused/reset
before any bytes) means the endpoint is down: with no fallback available the
turn must end with an actionable error after HERMES_FAST_CONN_FAIL_LIMIT
attempts instead of burning the full max_retries backoff cycle in silence.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.turn_retry_state import TurnRetryState


class _Clock:
    """Wall clock the test moves by hand: production reads ``time.time()``, and the patched
    waits and connects advance it, so a slow connect or a backoff costs no real time."""

    def __init__(self, monkeypatch):
        self._real, self.offset = time.time, 0.0
        monkeypatch.setattr(time, "time", lambda: self._real() + self.offset)

    def advance(self, seconds: float) -> None:
        self.offset += seconds


def _make_agent(monkeypatch, **agent_kwargs):
    from run_agent import AIAgent

    with (
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI", return_value=MagicMock()),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="http://127.0.0.1:1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            **agent_kwargs,
        )
    agent.client = MagicMock()
    agent.compression_enabled = False
    return agent


def _chat_response(content="", finish_reason="stop", tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)], model="test/model", usage=None,
    )


def _instant_refusals(n=12):
    import openai

    return [openai.APIConnectionError(request=MagicMock()) for _ in range(n)]


def _make_anthropic_stream_agent(connect):
    """A real agent on the anthropic_messages wire with a real client, so ``_should_stream``
    keeps streaming and every loop attempt runs ``_StreamingCall`` (its connect retries and
    reconnect backoff); ``connect`` is each physical ``messages.stream`` open."""
    from run_agent import AIAgent

    with (
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="http://127.0.0.1:1",
            provider="anthropic",
            api_mode="anthropic_messages",
            model="claude-opus-4-7",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    request_client = MagicMock()
    request_client.messages.stream.side_effect = connect
    agent._create_request_anthropic_client = lambda *a, **k: request_client
    # Exhaustion's one-shot client rebuild waits 8s for real; it is not what these tests pin.
    agent._try_recover_primary_transport = lambda *a, **k: False
    agent.compression_enabled = False
    agent._auto_recovery_cycles = 0
    return agent


def _anthropic_refusal():
    import anthropic
    import httpx

    return anthropic.APIConnectionError(request=httpx.Request("POST", "http://127.0.0.1:1/v1/messages"))


class TestRetryStateField:
    def test_streak_field_defaults_to_zero(self):
        assert TurnRetryState().fast_transport_failures == 0


class TestFailFastLoop:
    def test_fail_fast_terminates_after_streak(self, monkeypatch, tmp_path):
        """3 instant APIConnectionErrors with no fallback -> failed turn, ~3 attempts."""
        import openai

        calls = {"n": 0}

        def _boom(*a, **k):
            calls["n"] += 1
            raise openai.APIConnectionError(request=MagicMock())

        monkeypatch.setenv("HERMES_FAST_CONN_FAIL_LIMIT", "3")
        agent = _make_agent(monkeypatch)
        agent._api_max_retries = 12
        monkeypatch.setattr(agent, "_interruptible_streaming_api_call", _boom)
        monkeypatch.setattr(agent, "_interruptible_api_call", _boom)
        monkeypatch.setattr(agent, "_has_pending_fallback", lambda: False)
        monkeypatch.setattr(agent, "_try_activate_fallback", lambda *a, **k: False)
        # No real sleeps during backoff.
        monkeypatch.setattr("agent.retry_utils.jittered_backoff", lambda *a, **k: 0.0)

        result = agent.run_conversation(user_message="hi")

        assert result["failed"] is True
        assert "unreachable" in result["error"].lower()
        assert calls["n"] <= 4, f"expected fail-fast after ~3 attempts, made {calls['n']}"

    def test_env_zero_disables_fail_fast(self, monkeypatch):
        import openai

        calls = {"n": 0}

        def _boom(*a, **k):
            calls["n"] += 1
            raise openai.APIConnectionError(request=MagicMock())

        monkeypatch.setenv("HERMES_FAST_CONN_FAIL_LIMIT", "0")
        agent = _make_agent(monkeypatch)
        agent._api_max_retries = 5
        monkeypatch.setattr(agent, "_interruptible_streaming_api_call", _boom)
        monkeypatch.setattr(agent, "_interruptible_api_call", _boom)
        monkeypatch.setattr(agent, "_has_pending_fallback", lambda: False)
        monkeypatch.setattr(agent, "_try_activate_fallback", lambda *a, **k: False)
        monkeypatch.setattr("agent.retry_utils.jittered_backoff", lambda *a, **k: 0.0)

        result = agent.run_conversation(user_message="hi")

        assert result.get("failed") or result.get("error")
        assert calls["n"] >= 5, "disabled fail-fast must exhaust normal retries"

    def test_slow_failures_do_not_trip_fail_fast(self, monkeypatch):
        """Failures slower than 2s keep the normal retry budget."""
        import openai

        clock = _Clock(monkeypatch)
        calls = {"n": 0}

        def _slow_boom(*a, **k):
            calls["n"] += 1
            clock.advance(3.0)  # the connect hangs 3s before the endpoint gives up on it
            raise openai.APIConnectionError(request=MagicMock())

        monkeypatch.setenv("HERMES_FAST_CONN_FAIL_LIMIT", "3")
        agent = _make_agent(monkeypatch)
        agent._api_max_retries = 5
        agent._auto_recovery_cycles = 0
        monkeypatch.setattr(agent, "_interruptible_streaming_api_call", _slow_boom)
        monkeypatch.setattr(agent, "_interruptible_api_call", _slow_boom)
        monkeypatch.setattr(agent, "_has_pending_fallback", lambda: False)
        monkeypatch.setattr(agent, "_try_activate_fallback", lambda *a, **k: False)

        result = agent.run_conversation(user_message="hi")

        assert "unreachable" not in str(result.get("error")).lower()
        assert calls["n"] >= 5, "slow failures must use the full retry budget"

    def test_pending_fallback_suppresses_fail_fast(self, monkeypatch):
        """A fallback still to try gets its chance: the instant streak alone never ends the turn."""
        import openai

        calls = {"n": 0}

        def _boom(*a, **k):
            calls["n"] += 1
            raise openai.APIConnectionError(request=MagicMock())

        monkeypatch.setenv("HERMES_FAST_CONN_FAIL_LIMIT", "3")
        agent = _make_agent(monkeypatch)
        agent._api_max_retries = 5
        agent._auto_recovery_cycles = 0
        monkeypatch.setattr(agent, "_interruptible_api_call", _boom)
        monkeypatch.setattr(agent, "_has_pending_fallback", lambda: True)
        monkeypatch.setattr(agent, "_try_activate_fallback", lambda *a, **k: False)

        result = agent.run_conversation(user_message="hi")

        assert result["failed"] is True
        assert "unreachable" not in result["error"].lower()
        assert calls["n"] >= 5


class TestTerminalResultContract:
    """The fail-fast is a terminal result like retry exhaustion: same verdict fields, same
    status rail, so kanban recovery and the warning opt-out treat it the same way."""

    def _fail_fast(self, monkeypatch):
        import openai

        def _boom(*a, **k):
            raise openai.APIConnectionError(request=MagicMock())

        monkeypatch.setenv("HERMES_FAST_CONN_FAIL_LIMIT", "3")
        agent = _make_agent(monkeypatch)
        agent._api_max_retries = 12
        statuses = []
        agent.status_callback = lambda kind, message: statuses.append((kind, message))
        monkeypatch.setattr(agent, "_interruptible_api_call", _boom)
        result = agent.run_conversation(user_message="hi")
        assert "unreachable" in result["error"].lower()
        return result, statuses

    def test_result_carries_the_retryable_verdict_kanban_recovery_reads(self, monkeypatch):
        from agent.kanban_turn_recovery import should_recover_turn

        monkeypatch.delenv("HERMES_KANBAN_TURN_RECOVERY", raising=False)
        result, _ = self._fail_fast(monkeypatch)

        assert (result["failure_reason"], result["failure_retryable"]) == ("timeout", True)
        # A briefly refused endpoint is retried in place, not cold-respawned.
        assert should_recover_turn(result, attempt=0, task_id="t_probe") is True

    def test_terminal_status_line_rides_the_diagnostic_rail(self, monkeypatch):
        from gateway.warning_notifications import is_warning_status

        _, statuses = self._fail_fast(monkeypatch)

        terminal = [(k, m) for k, m in statuses if "unreachable" in str(m).lower()]
        assert terminal, statuses
        # display.suppress_warning_notifications hides exactly these.
        assert all(is_warning_status(k, m) for k, m in terminal)


class TestTranscriptSettlement:
    """The fail-fast persists the transcript the way retry exhaustion does: the continuation
    trail collapses into the delivered partial, and an open tail is left to the failed-turn
    boundary closers instead of a model-visible error row."""

    def test_tool_round_tail_is_closed_by_the_failed_turn_boundary(self, monkeypatch, tmp_path):
        from hermes_state import SessionDB
        from agent.turn_failure_copy import FAILED_TURN_DISPLAY_KIND, PARTIAL_FAILED_TURN_NOTICE

        monkeypatch.setenv("HERMES_FAST_CONN_FAIL_LIMIT", "3")
        db = SessionDB(db_path=tmp_path / "state.db")
        agent = _make_agent(monkeypatch, session_db=db, session_id="fail-fast-tool-tail")
        agent._api_max_retries = 12
        tool_call = SimpleNamespace(
            id="call_1", type="function", function=SimpleNamespace(name="web_search", arguments="{}"),
        )
        agent.client.chat.completions.create.side_effect = [
            _chat_response(finish_reason="tool_calls", tool_calls=[tool_call]), *_instant_refusals(),
        ]

        with patch("model_tools.handle_function_call", return_value="ok"):
            result = agent.run_conversation(user_message="look it up")

        assert "unreachable" in result["error"].lower()
        tail = result["messages"][-1]
        # Display-only boundary with the side-effect warning; stripped before provider requests.
        assert (tail["role"], tail.get("display_kind"), tail["content"]) == (
            "assistant", FAILED_TURN_DISPLAY_KIND, PARTIAL_FAILED_TURN_NOTICE)
        assert not any("API call aborted" in str(m.get("content")) for m in result["messages"])
        assert db.latest_conversation_role("fail-fast-tool-tail") == "assistant"

    def test_continuation_trail_collapses_into_the_delivered_partial(self, monkeypatch):
        partial = "Here is the first half of the report, already shown to the user."
        monkeypatch.setenv("HERMES_FAST_CONN_FAIL_LIMIT", "3")
        agent = _make_agent(monkeypatch)
        agent._api_max_retries = 12
        agent.client.chat.completions.create.side_effect = [
            _chat_response(content=partial, finish_reason="length"), *_instant_refusals(),
        ]

        result = agent.run_conversation(user_message="write a long report")

        assert "unreachable" in result["error"].lower()
        assert result.get("partial") is True and result["failed"] is True
        assert partial in result["final_response"]
        # Gateway retention contract: the reply differs from the error string.
        assert result["final_response"].strip() != result["error"].strip()
        # No dangling synthetic nudge: the turn persists as user -> one assistant row.
        assert not any(
            m.get("_length_continuation_nudge") or m.get("_length_continuation_fragment")
            for m in result["messages"]
        )
        assert [m["role"] for m in result["messages"]][-2:] == ["user", "assistant"]
        assert (result["messages"][-1]["content"], result["messages"][-1]["finish_reason"]) == (partial, "error")


class TestStreamingPath:
    """The streaming call reconnects inside one loop attempt (``HERMES_STREAM_RETRIES``,
    default 2) with a 1s/2s backoff between connects: the instant window is per connect."""

    @pytest.mark.real_retry_backoff
    def test_anthropic_refusals_fail_fast_through_reconnect_backoff(self, monkeypatch):
        from agent import chat_completion_helpers as cch

        clock = _Clock(monkeypatch)
        connects, waits = {"n": 0}, []

        def _refuse(**_kw):
            connects["n"] += 1
            raise _anthropic_refusal()

        monkeypatch.setenv("HERMES_FAST_CONN_FAIL_LIMIT", "3")
        monkeypatch.delenv("HERMES_STREAM_RETRIES", raising=False)
        agent = _make_anthropic_stream_agent(_refuse)
        agent._api_max_retries = 6
        assert agent._has_pending_fallback() is False
        # The real reconnect schedule elapses on the clock instead of sleeping; so does the
        # loop's own retry backoff.
        monkeypatch.setattr(cch, "_wait_stream_retry_backoff",
                            lambda _agent, delay: (waits.append(delay), clock.advance(delay)))
        monkeypatch.setattr("agent.turn_api_error.interruptible_backoff_sleep",
                            lambda _agent, wait, *_a, **_k: clock.advance(wait))

        result = agent.run_conversation(user_message="hi")

        assert waits[:2] == [1.0, 2.0], "the stream layer must really back off between connects"
        assert result["failed"] is True
        assert "unreachable" in result["error"].lower()
        assert connects["n"] == 9, f"3 loop attempts x 3 connects expected, made {connects['n']}"

    @pytest.mark.real_retry_backoff
    def test_slow_stream_connects_keep_the_full_retry_budget(self, monkeypatch):
        from agent import chat_completion_helpers as cch

        clock = _Clock(monkeypatch)
        connects = {"n": 0}

        def _slow_refuse(**_kw):
            connects["n"] += 1
            clock.advance(3.0)
            raise _anthropic_refusal()

        monkeypatch.setenv("HERMES_FAST_CONN_FAIL_LIMIT", "3")
        monkeypatch.delenv("HERMES_STREAM_RETRIES", raising=False)
        agent = _make_anthropic_stream_agent(_slow_refuse)
        agent._api_max_retries = 4
        monkeypatch.setattr(cch, "_wait_stream_retry_backoff", lambda _agent, delay: clock.advance(delay))
        monkeypatch.setattr("agent.turn_api_error.interruptible_backoff_sleep",
                            lambda _agent, wait, *_a, **_k: clock.advance(wait))

        result = agent.run_conversation(user_message="hi")

        assert result["failed"] is True
        assert "unreachable" not in result["error"].lower()
        assert connects["n"] >= 4 * 3, "slow connects must use the full retry budget"
