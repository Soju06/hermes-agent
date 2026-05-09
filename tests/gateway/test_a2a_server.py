"""Integration tests for the A2A inbound server.

Exercises A2AAdapter.connect() / disconnect() server lifecycle and the
HermesA2AExecutor → handle_message() bridge end-to-end through real
HTTP+JSONRPC transport on localhost.
"""
from __future__ import annotations

import uuid

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2A_AVAILABLE, A2AAdapter


pytestmark = [
    pytest.mark.skipif(
        not A2A_AVAILABLE, reason="a2a-sdk not installed (extras: a2a)"
    ),
    pytest.mark.asyncio,
]


def _adapter(port: int, *, name: str = "TestBot", bot_id: str = "1") -> A2AAdapter:
    return A2AAdapter(
        PlatformConfig(
            enabled=True,
            extra={
                "listen": f"127.0.0.1:{port}",
                "agent_card": {
                    "name": name,
                    "discord_bot_user_id": bot_id,
                },
            },
        )
    )


async def test_well_known_agent_card_served():
    """Server exposes /.well-known/agent-card.json with the configured name."""
    import httpx

    adapter = _adapter(8761)
    assert await adapter.connect() is True
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            r = await http.get("http://127.0.0.1:8761/.well-known/agent-card.json")
            assert r.status_code == 200
            data = r.json()
            assert data["name"] == "TestBot"
            # 1.0.2 surfaces URL inside supportedInterfaces (camelCase JSON
            # because protobuf-to-JSON normalizes field names)
            ifaces = data.get("supportedInterfaces") or data.get("supported_interfaces") or []
            assert any(
                str(it.get("url", "")).startswith("http://127.0.0.1:8761")
                for it in ifaces
            ), f"no listen URL in supportedInterfaces: {ifaces!r}"
    finally:
        await adapter.disconnect()


async def test_executor_dispatches_through_handle_message():
    """End-to-end: send an A2A message → handle_message receives MessageEvent →
    capture callback fires → reply emitted to the client."""
    import httpx
    from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
    from a2a.types import Message, Part, Role, SendMessageRequest

    adapter = _adapter(8762, name="Echo", bot_id="42")

    received: list = []

    async def fake_handle_message(event):
        # Simulate the gateway's eventual final-response delivery.
        received.append(event)
        cb = adapter._post_response_callbacks.get(event.message_id)
        assert cb is not None, "executor did not register capture callback"
        await cb(f"echoed: {event.text}")

    adapter.handle_message = fake_handle_message  # type: ignore[method-assign]

    assert await adapter.connect() is True
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resolver = A2ACardResolver(http, "http://127.0.0.1:8762/")
            card = await resolver.get_agent_card()
            client = ClientFactory(
                ClientConfig(httpx_client=http, streaming=False)
            ).create(card)

            req = SendMessageRequest(
                message=Message(
                    role=Role.ROLE_USER,
                    parts=[Part(text="ping")],
                    message_id=uuid.uuid4().hex,
                )
            )

            reply_text = None
            async for resp in client.send_message(req):
                if resp.HasField("message"):
                    for p in resp.message.parts:
                        if p.HasField("text"):
                            reply_text = p.text
                            break
                    if reply_text:
                        break

            assert reply_text == "echoed: ping"

        # Verify the executor delivered a proper Hermes MessageEvent
        assert len(received) == 1
        event = received[0]
        assert event.text == "ping"
        assert event.source.chat_type == "a2a_peer"
    finally:
        await adapter.disconnect()


async def test_disconnect_idempotent():
    """connect()/disconnect()/disconnect() must not raise."""
    adapter = _adapter(8763)
    assert await adapter.connect() is True
    await adapter.disconnect()
    # Second disconnect on already-stopped server is a no-op
    await adapter.disconnect()
    assert adapter._server is None
    assert adapter._server_task is None
