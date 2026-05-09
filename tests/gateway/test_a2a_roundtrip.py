"""Task 8 — Outbound A2AAdapter.send() roundtrip.

Two A2AAdapter instances run in the same process on different localhost
ports. Adapter A's send() resolves Adapter B's agent card, opens a
JSON-RPC client via a2a-sdk's ClientFactory, sends a message, and reads
the terminal reply from the StreamResponse iterator. Adapter B's
``handle_message`` is mocked to fire the post-response capture callback
directly (bypasses the gateway agent loop, which isn't under test here).

Pytestmark gates this whole file on a2a-sdk being importable; A2A_AVAILABLE
flips False when the optional `[a2a]` extra wasn't installed.
"""

from __future__ import annotations

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2A_AVAILABLE, A2AAdapter


pytestmark = [
    pytest.mark.skipif(
        not A2A_AVAILABLE, reason="a2a-sdk not installed (extras: a2a)"
    ),
    pytest.mark.asyncio,
]


def _adapter(
    port: int,
    *,
    name: str,
    bot_id: str,
    peers: dict | None = None,
) -> A2AAdapter:
    return A2AAdapter(
        PlatformConfig(
            enabled=True,
            extra={
                "listen": f"127.0.0.1:{port}",
                "agent_card": {
                    "name": name,
                    "discord_bot_user_id": bot_id,
                },
                **({"peers": peers} if peers else {}),
            },
        )
    )


async def test_two_adapters_roundtrip():
    """Adapter A → Adapter B → reply text reaches A.send().raw_response."""
    b = _adapter(8781, name="Bot-B", bot_id="2")

    # Bypass the gateway/agent dispatch path. handle_message normally
    # routes through MessageHandler → AIAgent.run_conversation → reply
    # capture. For the outbound-send test we only need to verify that
    # send() drives a real A2A client through to the executor and gets
    # the terminal reply back, so we short-circuit handle_message to
    # invoke the capture callback directly.
    async def fake_handle(event):
        cb = b._post_response_callbacks.get(event.message_id)
        if cb is not None:
            await cb(f"hi from B, you said: {event.text}")

    b.handle_message = fake_handle  # type: ignore[method-assign]
    assert await b.connect() is True

    a = _adapter(
        8782,
        name="Bot-A",
        bot_id="1",
        peers={"2": "http://127.0.0.1:8781/"},
    )
    assert await a.connect() is True

    try:
        # Resolve B by discord_bot_user_id="2" via the peers map.
        result = await a.send(chat_id="2", content="ping")
        assert result.success is True, f"send failed: {result.error}"
        assert result.message_id, "missing message_id on success"
        assert "hi from B" in str(result.raw_response)
        assert "ping" in str(result.raw_response)
    finally:
        await a.disconnect()
        await b.disconnect()


async def test_send_to_direct_url():
    """chat_id can be a raw http(s):// URL when no peers entry is present."""
    b = _adapter(8783, name="Bot-B-direct", bot_id="2")

    async def fake_handle(event):
        cb = b._post_response_callbacks.get(event.message_id)
        if cb is not None:
            await cb("direct-ack")

    b.handle_message = fake_handle  # type: ignore[method-assign]
    assert await b.connect() is True

    # No peers map — A doesn't even need a peer registered, the URL
    # passed directly is recognized as such.
    a = _adapter(8784, name="Bot-A-direct", bot_id="1")
    assert await a.connect() is True

    try:
        result = await a.send(chat_id="http://127.0.0.1:8783/", content="ping2")
        assert result.success is True, f"send failed: {result.error}"
        assert "direct-ack" in str(result.raw_response)
    finally:
        await a.disconnect()
        await b.disconnect()


async def test_send_unknown_peer_returns_error():
    """chat_id that's neither a known peer nor a URL fails fast — no network."""
    a = _adapter(8785, name="Bot-A", bot_id="1")
    assert await a.connect() is True
    try:
        result = await a.send(chat_id="nonexistent-discord-id", content="ping")
        assert result.success is False
        assert "unknown peer" in (result.error or "")
    finally:
        await a.disconnect()


async def test_send_a2a_unavailable(monkeypatch):
    """When a2a-sdk is missing, send() returns a clean error rather than
    raising an ImportError."""
    a = _adapter(8786, name="Bot-A", bot_id="1")
    # Don't connect — flag-flip test only.
    monkeypatch.setattr("gateway.platforms.a2a.A2A_AVAILABLE", False)
    result = await a.send(chat_id="anything", content="ping")
    assert result.success is False
    assert "a2a-sdk not available" in (result.error or "")
