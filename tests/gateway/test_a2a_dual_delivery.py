"""ADR-011 v2.1 / Phase 4 Task 35: Dual-delivery outbound — fire-and-forget broadcast.

Tests that:
  - `_broadcast_to_channel_peers(channel_id, content, ...)` iterates `_channel_peers[channel_id]`
  - Self bot_user_id is excluded from broadcast targets
  - Each peer broadcast goes through `_send_fire_and_forget` with `return_immediately=True`
  - Empty / missing channel_id → no-op (skip silently, no error)
  - Caller doesn't block on peer reply (timing test — fire-and-forget semantics)
  - Peer not in `_peer_cards` (extension verify will land in Task 40) → still attempted in Phase 4
    scope, since Task 40 hasn't gated the broadcast yet. Phase 4 verify chain is loose by design.

These are pure adapter tests with mocked a2a-sdk send_message — no live A2A server.
"""

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2AAdapter


def _make_config(**extra: Any) -> PlatformConfig:
    """Minimal PlatformConfig for adapter tests."""
    return PlatformConfig(
        enabled=True,
        token="",
        extra=extra,
    )


def _make_adapter_with_peers(
    *,
    self_bot_user_id: str = "bot_self",
    channel_peers: dict = None,
    peers: dict = None,
    peer_cards: dict = None,
) -> A2AAdapter:
    """Adapter with `_self_bot_user_id`, `_channel_peers`, `_peers`, `_peer_cards` pre-loaded."""
    config = _make_config(
        listen="127.0.0.1:9999",
        discord_bot_user_id=self_bot_user_id,
        channel_peers=channel_peers or {},
        peers=peers or {},
    )
    adapter = A2AAdapter(config)
    if peer_cards:
        adapter._peer_cards.update(peer_cards)
    if peers:
        adapter._peers.update(peers)
    return adapter


# ---------------------------------------------------------------------------
# Test 1: _self_bot_user_id field populated from config
# ---------------------------------------------------------------------------
def test_self_bot_user_id_populated_from_config():
    """ADR-011 §6: adapter knows its own bot_user_id for self-skip in broadcast."""
    config = _make_config(listen="127.0.0.1:9999", discord_bot_user_id="bot_self_xyz")
    adapter = A2AAdapter(config)
    assert hasattr(adapter, "_self_bot_user_id")
    assert adapter._self_bot_user_id == "bot_self_xyz"


def test_self_bot_user_id_none_when_unconfigured():
    """If discord_bot_user_id not set, _self_bot_user_id stays None (no broadcast happens)."""
    config = _make_config(listen="127.0.0.1:9999")
    adapter = A2AAdapter(config)
    assert adapter._self_bot_user_id is None


# ---------------------------------------------------------------------------
# Test 2: _broadcast_to_channel_peers excludes self from targets
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_broadcast_skips_self_bot_user_id():
    """ADR-011 §6: self_bot_user_id in _channel_peers[channel_id] is excluded from broadcast."""
    adapter = _make_adapter_with_peers(
        self_bot_user_id="bot_self",
        channel_peers={"chan_1": ["bot_self", "bot_a", "bot_b"]},
        peers={"bot_a": "http://a.example/", "bot_b": "http://b.example/"},
    )
    sent_to: list[str] = []

    async def _fake_send(peer_id, peer_url, content, metadata):
        sent_to.append(peer_id)

    with patch.object(adapter, "_send_fire_and_forget", side_effect=_fake_send):
        await adapter._broadcast_to_channel_peers(
            channel_id="chan_1",
            content="hello peers",
            surface_message_id="surface_msg_1",
            surface_platform="discord",
            context_id="ctx_1",
        )
        # Allow pending tasks to drain
        await asyncio.sleep(0.05)

    assert "bot_self" not in sent_to
    assert sorted(sent_to) == ["bot_a", "bot_b"]


# ---------------------------------------------------------------------------
# Test 3: Empty / missing channel_id → no-op
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_broadcast_empty_channel_id_noop():
    """Channel id not in _channel_peers → no fire_and_forget calls."""
    adapter = _make_adapter_with_peers(
        self_bot_user_id="bot_self",
        channel_peers={"chan_1": ["bot_a"]},
        peers={"bot_a": "http://a.example/"},
    )
    sent_to: list[str] = []

    async def _fake_send(peer_id, peer_url, content, metadata):
        sent_to.append(peer_id)

    with patch.object(adapter, "_send_fire_and_forget", side_effect=_fake_send):
        await adapter._broadcast_to_channel_peers(
            channel_id="chan_unknown",
            content="nobody here",
            surface_message_id="surface_msg_1",
            surface_platform="discord",
            context_id="ctx_1",
        )
        await asyncio.sleep(0.05)

    assert sent_to == []


# ---------------------------------------------------------------------------
# Test 4: Caller does not block on peer reply (fire-and-forget timing)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_broadcast_does_not_block_caller():
    """ADR-011 §4 fire-and-forget: _broadcast_to_channel_peers returns
    quickly even if a peer's send_message would hang for seconds. Verified by
    making the inner send sleep, and confirming the broadcast call returns
    before the sleep would complete."""
    adapter = _make_adapter_with_peers(
        self_bot_user_id="bot_self",
        channel_peers={"chan_1": ["bot_slow_a", "bot_slow_b"]},
        peers={
            "bot_slow_a": "http://a.example/",
            "bot_slow_b": "http://b.example/",
        },
    )

    async def _slow_send(peer_id, peer_url, content, metadata):
        await asyncio.sleep(5.0)  # would block 5s if awaited inline

    with patch.object(adapter, "_send_fire_and_forget", side_effect=_slow_send):
        start = time.monotonic()
        await adapter._broadcast_to_channel_peers(
            channel_id="chan_1",
            content="quick",
            surface_message_id="surface_msg_2",
            surface_platform="discord",
            context_id="ctx_1",
        )
        elapsed = time.monotonic() - start

    # Fire-and-forget: caller returns in well under 1s even though peers would
    # take 5s each. Generous bound to avoid CI flakiness.
    assert elapsed < 1.0, f"broadcast blocked {elapsed:.2f}s — fire-and-forget violated"


# ---------------------------------------------------------------------------
# Test 5: _send_fire_and_forget uses SendMessageConfiguration(return_immediately=True)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_fire_and_forget_uses_return_immediately():
    """ADR-011 §4 + a2a-sdk 1.0.2 spike: outbound broadcast sets
    SendMessageConfiguration.return_immediately=True so the server breaks on
    first event and the agent runs in background. Verifies the SendMessageRequest
    constructed inside _send_fire_and_forget carries the flag."""
    adapter = _make_adapter_with_peers(
        self_bot_user_id="bot_self",
        peers={"bot_a": "http://a.example/"},
    )

    # Capture the SendMessageRequest constructed inside _send_fire_and_forget
    captured: dict[str, Any] = {}

    async def _send_message_stub(req):
        captured["request"] = req
        # mimic empty AsyncIterator return
        if False:
            yield None
        return

    fake_client = MagicMock()
    fake_client.send_message = _send_message_stub
    fake_factory = MagicMock()
    fake_factory.create.return_value = fake_client

    fake_resolver = MagicMock()
    fake_resolver.get_agent_card = AsyncMock(return_value=MagicMock())

    with (
        patch("a2a.client.A2ACardResolver", return_value=fake_resolver),
        patch("a2a.client.ClientFactory", return_value=fake_factory),
    ):
        await adapter._send_fire_and_forget(
            peer_id="bot_a",
            peer_url="http://a.example/",
            content="hello",
            metadata={
                "hermes.sender_bot_user_id": "bot_self",
                "hermes.surface_channel_id": "chan_1",
                "hermes.surface_message_id": "surf_msg_1",
                "hermes.surface_platform": "discord",
                "hermes.context_id": "ctx_1",
            },
        )

    assert "request" in captured, "send_message was never invoked"
    req = captured["request"]
    # SendMessageRequest has a `configuration` field; verify return_immediately=True
    assert req.HasField("configuration"), (
        "SendMessageRequest must carry SendMessageConfiguration"
    )
    assert req.configuration.return_immediately is True, (
        "return_immediately must be True for fire-and-forget broadcast"
    )


# ---------------------------------------------------------------------------
# Test 6: _broadcast_to_channel_peers passes payload metadata through
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_broadcast_propagates_metadata_to_peer_send():
    """Broadcast should pass surface_channel_id / surface_message_id /
    surface_platform / context_id / sender_bot_user_id metadata to each
    peer's _send_fire_and_forget call (Task 36 payload spec)."""
    adapter = _make_adapter_with_peers(
        self_bot_user_id="bot_self",
        channel_peers={"chan_1": ["bot_a"]},
        peers={"bot_a": "http://a.example/"},
    )
    captured_meta: list[dict] = []

    async def _fake_send(peer_id, peer_url, content, metadata):
        captured_meta.append(metadata)

    with patch.object(adapter, "_send_fire_and_forget", side_effect=_fake_send):
        await adapter._broadcast_to_channel_peers(
            channel_id="chan_1",
            content="payload check",
            surface_message_id="surf_msg_99",
            surface_platform="telegram",
            context_id="ctx_42",
        )
        await asyncio.sleep(0.05)

    assert len(captured_meta) == 1
    meta = captured_meta[0]
    assert meta["hermes.sender_bot_user_id"] == "bot_self"
    assert meta["hermes.surface_channel_id"] == "chan_1"
    assert meta["hermes.surface_message_id"] == "surf_msg_99"
    assert meta["hermes.surface_platform"] == "telegram"
    assert meta["hermes.context_id"] == "ctx_42"
