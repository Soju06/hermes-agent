"""E2E gap fix — _send_via_adapter must surface raw_response.

Found during E2E-A: A2AAdapter.send() blocks until the peer's
terminal Message arrives and stuffs the reply text into
SendResult.raw_response. But _send_via_adapter discarded it,
returning only {"success": True, "message_id": ...}. The whole
point of A2A — the calling agent reads the peer's reply in its
own turn — was being silently dropped.

Fix: when raw_response is non-None on a successful send, include
it in the dict returned by _send_via_adapter.
"""

import asyncio
import types
from unittest.mock import MagicMock, patch

import pytest

from tools.send_message_tool import _send_via_adapter
from gateway.platforms.base import SendResult


class _StubAdapter:
    """Minimal adapter that records the call and returns a stub result."""

    def __init__(self, result):
        self._result = result
        self.calls = []

    async def send(self, *, chat_id, content):
        self.calls.append((chat_id, content))
        return self._result


def _make_runner(platform_name, adapter):
    runner = types.SimpleNamespace()
    runner.adapters = {platform_name: adapter}
    return runner


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) \
        if False else asyncio.run(coro)


def test_send_via_adapter_propagates_raw_response_when_present():
    """A2A success path: raw_response carries the peer's reply text."""
    from gateway.config import Platform
    adapter = _StubAdapter(
        SendResult(success=True, message_id="msg-42",
                   raw_response="hi i'm bot-b!")
    )
    runner = _make_runner(Platform.A2A, adapter)
    pconfig = MagicMock()

    with patch("gateway.run._gateway_runner_ref", lambda: runner):
        out = _run(_send_via_adapter(Platform.A2A, pconfig, "bot-b", "ping"))

    assert out["success"] is True
    assert out["message_id"] == "msg-42"
    assert out["raw_response"] == "hi i'm bot-b!"
    assert adapter.calls == [("bot-b", "ping")]


def test_send_via_adapter_omits_raw_response_when_none():
    """Telegram-shape send: no peer reply expected → no raw_response key."""
    from gateway.config import Platform
    adapter = _StubAdapter(
        SendResult(success=True, message_id="tg-99", raw_response=None)
    )
    runner = _make_runner(Platform.TELEGRAM, adapter)
    pconfig = MagicMock()

    with patch("gateway.run._gateway_runner_ref", lambda: runner):
        out = _run(_send_via_adapter(Platform.TELEGRAM, pconfig, "12345", "hi"))

    assert out == {"success": True, "message_id": "tg-99"}
    assert "raw_response" not in out


def test_send_via_adapter_failure_path_unchanged():
    """Failure: error string returned, no raw_response leakage."""
    from gateway.config import Platform
    adapter = _StubAdapter(
        SendResult(success=False, error="peer offline",
                   raw_response="should not surface")
    )
    runner = _make_runner(Platform.A2A, adapter)
    pconfig = MagicMock()

    with patch("gateway.run._gateway_runner_ref", lambda: runner):
        out = _run(_send_via_adapter(Platform.A2A, pconfig, "bot-b", "ping"))

    assert "success" not in out
    assert "raw_response" not in out
    assert "peer offline" in out["error"]
