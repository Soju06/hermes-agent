"""LLM activity recap for long-running notifications (mode: recap)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.run import GatewayRunner


class _Agent:
    model = "m"
    provider = "p"
    base_url = "http://x"
    api_key = "k"
    api_mode = "chat_completions"

    def __init__(self, ctx):
        self._ctx = ctx

    def get_activity_recap_context(self):
        return dict(self._ctx)


def _runner():
    return GatewayRunner.__new__(GatewayRunner)


def _resp(text):
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    r = MagicMock()
    r.choices = [choice]
    return r


CTX = {
    "goal": "apifuse provider 10개 추가",
    "recent_tools": ["terminal: pytest ...", "patch: providers.py"],
    "voice_samples": ["테스트 돌려놓고 결과 보는 중이야"],
    "last_tool_result": "3 passed",
    "current_tool": "terminal",
    "seconds_since_activity": 45,
    "last_activity_desc": "running pytest",
    "iteration": 12,
    "max_iterations": 300,
}


class TestLLMActivityRecap:
    def test_generates_and_caches(self):
        runner = _runner()
        agent = _Agent(CTX)
        with patch("agent.auxiliary_client.call_llm", return_value=_resp("pytest 실행 결과 확인 중")) as mock_llm:
            line1 = asyncio.run(runner._llm_activity_recap(agent, "sess"))
            line2 = asyncio.run(runner._llm_activity_recap(agent, "sess"))
        assert line1 == "pytest 실행 결과 확인 중"
        assert line2 == line1
        assert mock_llm.call_count == 1, "unchanged context must reuse the cached line"

    def test_regenerates_on_context_change(self):
        runner = _runner()
        agent = _Agent(CTX)
        with patch("agent.auxiliary_client.call_llm", return_value=_resp("A")) as mock_llm:
            asyncio.run(runner._llm_activity_recap(agent, "sess"))
            agent._ctx = dict(CTX, current_tool="patch")
            asyncio.run(runner._llm_activity_recap(agent, "sess"))
        assert mock_llm.call_count == 2

    def test_failure_returns_none(self):
        runner = _runner()
        agent = _Agent(CTX)
        with patch("agent.auxiliary_client.call_llm", side_effect=RuntimeError("aux down")):
            assert asyncio.run(runner._llm_activity_recap(agent, "sess")) is None

    def test_multiline_and_length_clamped(self):
        runner = _runner()
        agent = _Agent(CTX)
        with patch("agent.auxiliary_client.call_llm", return_value=_resp("x" * 300 + "\nsecond line")):
            line = asyncio.run(runner._llm_activity_recap(agent, "sess"))
        assert line == "x" * 140

    def test_aux_call_carries_the_agents_live_main_runtime(self):
        # Same runtime the compression rail passes (AIAgent._current_main_runtime): auth_mode and
        # session_id ride along for aux session affinity and prompt-cache scope.
        from run_agent import AIAgent

        class _LiveAgent(_Agent):
            auth_mode = "oauth"
            session_id = "sess-123"
            _current_main_runtime = AIAgent._current_main_runtime

        runner = _runner()
        with patch("agent.auxiliary_client.call_llm", return_value=_resp("A")) as mock_llm:
            asyncio.run(runner._llm_activity_recap(_LiveAgent(CTX), "sess"))
        assert mock_llm.call_args.kwargs["main_runtime"] == {
            "model": "m", "provider": "p", "base_url": "http://x", "api_key": "k",
            "api_mode": "chat_completions", "auth_mode": "oauth", "session_id": "sess-123",
        }

    def test_agent_without_runtime_helper_passes_route_attributes(self):
        runner = _runner()
        with patch("agent.auxiliary_client.call_llm", return_value=_resp("A")) as mock_llm:
            asyncio.run(runner._llm_activity_recap(_Agent(CTX), "sess"))
        assert mock_llm.call_args.kwargs["main_runtime"] == {
            "model": "m", "provider": "p", "base_url": "http://x", "api_key": "k",
            "api_mode": "chat_completions",
        }

    def test_context_error_returns_none(self):
        runner = _runner()
        bad = MagicMock()
        bad.get_activity_recap_context.side_effect = RuntimeError
        assert asyncio.run(runner._llm_activity_recap(bad, "sess")) is None

    def test_recap_mode_survives_user_config_normalisation(self):
        # display.long_running_notifications: "recap" in user config must
        # reach _display_surface_mode as "recap", not be coerced to bool.
        from gateway.display_config import resolve_display_setting

        config = {
            "display": {
                "platforms": {"whatsapp": {"long_running_notifications": "recap"}}
            }
        }
        assert (
            resolve_display_setting(config, "whatsapp", "long_running_notifications")
            == "recap"
        )
        # Other visibility settings must NOT accept the recap mode.
        other = {"display": {"platforms": {"whatsapp": {"thinking_progress": "recap"}}}}
        assert resolve_display_setting(other, "whatsapp", "thinking_progress") is False


class _HeartbeatAdapter:
    """Delivery adapter WITHOUT ``delete_message``: recap mode must edit in place, not crash."""

    def __init__(self, runner, stop_after_sends=None):
        self.runner = runner
        self.stop_after_sends = stop_after_sends
        self.sent: list[str] = []
        self.edits: list[tuple[str, str]] = []

    async def send(self, chat_id, text, metadata=None):
        self.sent.append(text)
        if self.stop_after_sends and len(self.sent) >= self.stop_after_sends:
            self.runner._restart_requested = True
        return SimpleNamespace(success=True, message_id=f"hb-{len(self.sent)}")

    async def edit_message(self, chat_id, message_id, text):
        self.edits.append((message_id, text))
        self.runner._restart_requested = True  # second tick done: stop the loop
        return SimpleNamespace(success=True)


class _DeletingHeartbeatAdapter(_HeartbeatAdapter):
    def __init__(self, runner, stop_after_sends=None):
        super().__init__(runner, stop_after_sends)
        self.deleted: list[str] = []

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)
        return True


def _notify_loop(monkeypatch, adapter_cls, **adapter_kwargs):
    """Drive ``_run_agent_notify_long_running`` in recap mode until the adapter flags a stop."""
    from gateway.turn_context import TurnContext

    monkeypatch.setenv("HERMES_AGENT_NOTIFY_INTERVAL", "0.01")
    runner = _runner()
    runner._running_agents = {}
    runner._draining = runner._restart_requested = False
    adapter = adapter_cls(runner, **adapter_kwargs)
    runner._delivery_adapter_for = lambda source: adapter
    runner._agent_activity_summary = lambda agent: None
    runner._llm_activity_recap = AsyncMock(return_value="pytest 결과 확인 중")
    agent = MagicMock()
    runner._running_agents["sess"] = agent
    disp = MagicMock()
    disp._display_surface_mode.return_value = "recap"
    disp.resolve_display_setting.return_value = False
    ctx = TurnContext(source=SimpleNamespace(chat_id="c", platform="telegram"), session_key="sess")
    ctx.agent_holder[0] = agent
    asyncio.run(asyncio.wait_for(runner._run_agent_notify_long_running(disp, ctx, [None]), 5))
    return runner, adapter


class TestRecapHeartbeatLoop:
    def test_adapter_without_delete_support_edits_in_place(self, monkeypatch):
        runner, adapter = _notify_loop(monkeypatch, _HeartbeatAdapter)
        assert adapter.sent == ["⏳ 0 min — pytest 결과 확인 중"]
        assert adapter.edits == [("hb-1", "⏳ 0 min — pytest 결과 확인 중")]
        assert runner._llm_activity_recap.await_count == 2

    def test_delete_capable_adapter_resends_at_thread_bottom(self, monkeypatch):
        _, adapter = _notify_loop(monkeypatch, _DeletingHeartbeatAdapter, stop_after_sends=2)
        assert adapter.deleted == ["hb-1"]
        assert adapter.edits == []
        assert adapter.sent == ["⏳ 0 min — pytest 결과 확인 중"] * 2
