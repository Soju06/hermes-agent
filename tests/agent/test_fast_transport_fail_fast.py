"""Fail-fast on instant transport failure streaks.

A consecutive run of sub-2s transport failures (connection refused/reset
before any bytes) means the endpoint is down: with no fallback available the
turn must end with an actionable error after HERMES_FAST_CONN_FAIL_LIMIT
attempts instead of burning the full max_retries backoff cycle in silence.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.turn_retry_state import TurnRetryState


def _make_agent(monkeypatch):
    import run_agent
    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="http://127.0.0.1:1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
    agent.client = MagicMock()
    agent.compression_enabled = False
    return agent


class TestRetryStateField:
    def test_streak_field_defaults_to_zero(self):
        assert TurnRetryState().fast_transport_failures == 0


class TestFailFastLoop:
    def _make_agent(self, monkeypatch, tmp_path):
        from run_agent import AIAgent

        agent = AIAgent.__new__(AIAgent)
        return agent

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
        monkeypatch.setattr("agent.conversation_loop.jittered_backoff", lambda *a, **k: 0.0)

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
        monkeypatch.setattr("agent.conversation_loop.jittered_backoff", lambda *a, **k: 0.0)

        result = agent.run_conversation(user_message="hi")

        assert result.get("failed") or result.get("error")
        assert calls["n"] >= 5, "disabled fail-fast must exhaust normal retries"

    def test_slow_failures_do_not_trip_fail_fast(self, monkeypatch):
        """Failures slower than 2s keep the normal retry budget."""
        import time as _time
        import openai

        calls = {"n": 0}

        def _slow_boom(*a, **k):
            calls["n"] += 1
            raise openai.APIConnectionError(request=MagicMock())

        monkeypatch.setenv("HERMES_FAST_CONN_FAIL_LIMIT", "3")
        agent = _make_agent(monkeypatch)
        agent._api_max_retries = 5
        monkeypatch.setattr(agent, "_interruptible_streaming_api_call", _slow_boom)
        monkeypatch.setattr(agent, "_interruptible_api_call", _slow_boom)
        monkeypatch.setattr(agent, "_has_pending_fallback", lambda: False)
        monkeypatch.setattr(agent, "_try_activate_fallback", lambda *a, **k: False)
        monkeypatch.setattr("agent.conversation_loop.jittered_backoff", lambda *a, **k: 0.0)
        # Make every attempt LOOK slow: advance the clock 3s per time() call
        # pair via a monotonic offset injected around the attempt timer.
        _real_time = _time.time
        _state = {"offset": 0.0}

        def _ticking_time():
            _state["offset"] += 1.6
            return _real_time() + _state["offset"]

        monkeypatch.setattr("agent.conversation_loop.time.time", _ticking_time)

        result = agent.run_conversation(user_message="hi")
        assert calls["n"] >= 5, "slow failures must use the full retry budget"
