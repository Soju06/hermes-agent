"""LLM activity recap for long-running notifications (mode: recap)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

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

    def test_context_error_returns_none(self):
        runner = _runner()
        bad = MagicMock()
        bad.get_activity_recap_context.side_effect = RuntimeError
        assert asyncio.run(runner._llm_activity_recap(bad, "sess")) is None
