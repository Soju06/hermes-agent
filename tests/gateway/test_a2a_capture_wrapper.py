"""Task 7 — Reply-capture via set_message_handler wrapping.

Verifies the wrapper installed by A2AAdapter.set_message_handler():
- A real (string-returning) handler installed via set_message_handler
  has its return value captured and forwarded to the per-message-id
  callback registered by HermesA2AExecutor.
- An EphemeralReply is unwrapped to its text portion.
- A handler returning None (e.g. simulated streaming already-delivered)
  results in no callback fire and the executor times out gracefully —
  but for the non-streaming PoC path, real handlers always return a
  string.
- A handler raising still captures an error reply (avoids 120s timeout).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2A_AVAILABLE, A2AAdapter
from gateway.platforms.base import EphemeralReply


pytestmark = [
    pytest.mark.skipif(
        not A2A_AVAILABLE, reason="a2a-sdk not installed (extras: a2a)"
    ),
    pytest.mark.asyncio,
]


def _adapter(port: int, *, name: str = "WrapBot") -> A2AAdapter:
    return A2AAdapter(
        PlatformConfig(
            enabled=True,
            extra={
                "listen": f"127.0.0.1:{port}",
                "agent_card": {"name": name, "discord_bot_user_id": "1"},
            },
        )
    )


async def _send_and_collect(port: int, text_in: str) -> str | None:
    """Drive a real A2A roundtrip on localhost and return the agent's reply text."""
    import httpx
    from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
    from a2a.types import Message, Part, Role, SendMessageRequest

    async with httpx.AsyncClient(timeout=5.0) as http:
        resolver = A2ACardResolver(http, f"http://127.0.0.1:{port}/")
        card = await resolver.get_agent_card()
        client = ClientFactory(
            ClientConfig(httpx_client=http, streaming=False)
        ).create(card)
        req = SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER,
                parts=[Part(text=text_in)],
                message_id=uuid.uuid4().hex,
            )
        )
        async for resp in client.send_message(req):
            if resp.HasField("message"):
                for p in resp.message.parts:
                    if p.HasField("text"):
                        return p.text
    return None


async def test_wrapper_captures_string_return():
    """Standard path: handler returns a string → wrapper fires capture cb → reply seen by client."""
    adapter = _adapter(8771)
    captured_events = []

    async def real_handler(event):
        captured_events.append(event)
        return f"replied: {event.text}"

    adapter.set_message_handler(real_handler)

    assert await adapter.connect() is True
    try:
        reply = await _send_and_collect(8771, "ping")
        assert reply == "replied: ping"
        assert len(captured_events) == 1
        assert captured_events[0].text == "ping"
        # Capture-callback registry must be drained between requests
        assert adapter._post_response_callbacks == {}
    finally:
        await adapter.disconnect()


async def test_wrapper_unwraps_ephemeral_reply():
    """EphemeralReply (for /new etc. confirmations) is unwrapped to text."""
    adapter = _adapter(8772)

    async def real_handler(event):
        return EphemeralReply(text="ephemeral text", ttl_seconds=30)

    adapter.set_message_handler(real_handler)

    assert await adapter.connect() is True
    try:
        reply = await _send_and_collect(8772, "x")
        assert reply == "ephemeral text"
    finally:
        await adapter.disconnect()


async def test_wrapper_handler_exception_yields_error_reply():
    """A handler that raises still produces a reply within the executor's timeout."""
    adapter = _adapter(8773)

    async def failing_handler(event):
        raise RuntimeError("boom")

    adapter.set_message_handler(failing_handler)

    assert await adapter.connect() is True
    try:
        reply = await _send_and_collect(8773, "ping")
        # The executor catches the exception via its own try/except
        # ("[A2A error: ...]") because our wrapper re-raises after
        # firing the capture callback.
        assert reply is not None
        assert "A2A error" in reply or "exception" in reply
    finally:
        await adapter.disconnect()


async def test_wrapper_returns_none_does_not_break_base_send_path():
    """Returning None must NOT trigger base.py _send_with_retry on this adapter
    (which would log 'not implemented' and could mask real failures).

    We verify by counting send() calls — there should be zero, because
    base.py gates outbound delivery behind `if text_content:` and the
    wrapper always returns None. (The reply still flows through the
    A2A executor → event_queue path.)
    """
    adapter = _adapter(8774)
    send_calls = 0

    original_send = adapter.send

    async def counting_send(*args, **kwargs):
        nonlocal send_calls
        send_calls += 1
        return await original_send(*args, **kwargs)

    adapter.send = counting_send  # type: ignore[method-assign]

    async def real_handler(event):
        return f"got: {event.text}"

    adapter.set_message_handler(real_handler)

    assert await adapter.connect() is True
    try:
        reply = await _send_and_collect(8774, "hello")
        assert reply == "got: hello"
        # The whole point: base.py never called .send() because our wrapper
        # returned None. The reply went out via HermesA2AExecutor →
        # event_queue.enqueue_event, not via the adapter.send pathway.
        assert send_calls == 0
    finally:
        await adapter.disconnect()
