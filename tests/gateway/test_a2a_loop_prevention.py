"""ADR-011 v2.1 §9 / Phase 4 Task 39: Loop / echo prevention.

Tests that the channel_broadcast inbound handler skips:
  1. Self-echo — payload's hermes.sender_bot_user_id == self._self_bot_user_id
  2. Message-id dedup — same A2A Message.message_id seen twice within 5min TTL
  3. Surface dedup — same hermes.surface_message_id seen twice within 5min TTL
     (Discord natural-read + A2A inbound 같은 메시지 두 번 도착하는 race)
  4. TTL expiry — entries older than 5min are pruned and no longer block

Plus a small helper:
  - `_mark_surface_outbound(surface_message_id)` — Task 35b will call this
    when a Discord/Telegram reply is sent by THIS bot, so the same id never
    matches against an A2A inbound (which would be a self-echo via a
    different route). Phase 4 ships the helper + adapter-side primitive;
    actual wiring lives in Task 35b once the reply hook is wired.

Pure adapter-side tests with mocked time for TTL determinism.
"""

import time
from typing import Any
from unittest.mock import patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2AAdapter


def _make_config(**extra: Any) -> PlatformConfig:
    return PlatformConfig(enabled=True, token="", extra=extra)


def _make_msg(
    *,
    sender_bot_user_id: str,
    surface_channel_id: str = "chan_1",
    surface_message_id: str = "surf_1",
    message_id: str = "a2a_msg_1",
    context_id: str = "ctx_1",
    text: str = "hi",
):
    """Build a fake A2A Message with hermes.* metadata Struct."""
    from a2a.types import Message as A2AMessage, Part as A2APart, Role
    from google.protobuf import struct_pb2

    meta = struct_pb2.Struct()
    meta.update(
        {
            "hermes.sender_bot_user_id": sender_bot_user_id,
            "hermes.surface_channel_id": surface_channel_id,
            "hermes.surface_message_id": surface_message_id,
            "hermes.surface_platform": "discord",
            "hermes.context_id": context_id,
        }
    )
    return A2AMessage(
        role=Role.ROLE_AGENT,
        parts=[A2APart(text=text)],
        message_id=message_id,
        context_id=context_id,
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# 1. _recently_seen + _recently_seen_surface fields exist + empty default
# ---------------------------------------------------------------------------
def test_recently_seen_fields_default_empty():
    adapter = A2AAdapter(
        _make_config(
            listen="127.0.0.1:9999",
            inbound_handler="channel_broadcast",
            discord_bot_user_id="bot_self",
        )
    )
    assert hasattr(adapter, "_recently_seen")
    assert hasattr(adapter, "_recently_seen_surface")
    assert adapter._recently_seen == {}
    assert adapter._recently_seen_surface == {}
    # TTL constant is configurable but default 300s (5 min)
    assert hasattr(adapter, "_loop_prevention_ttl_seconds")
    assert adapter._loop_prevention_ttl_seconds == 300


# ---------------------------------------------------------------------------
# 2. Self-echo skip — sender == self silently drops
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inbound_self_echo_silently_skipped():
    """ADR-011 §9: if hermes.sender_bot_user_id == self._self_bot_user_id,
    transcript append SKIPPED. Caller's broadcast that echoed back to itself
    (via a peer or via Discord read) must not appear in our own transcript."""
    adapter = A2AAdapter(
        _make_config(
            listen="127.0.0.1:9999",
            inbound_handler="channel_broadcast",
            discord_bot_user_id="bot_self",
        )
    )
    msg = _make_msg(
        sender_bot_user_id="bot_self",
        surface_channel_id="chan_1",
        message_id="echo_1",
    )

    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="bot_self"
    )

    assert len(adapter._channel_transcripts["chan_1"]) == 0


# ---------------------------------------------------------------------------
# 3. Message-id dedup — same message_id twice → second one skipped
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inbound_message_id_dedup():
    adapter = A2AAdapter(
        _make_config(
            listen="127.0.0.1:9999",
            inbound_handler="channel_broadcast",
            discord_bot_user_id="bot_self",
        )
    )
    msg1 = _make_msg(
        sender_bot_user_id="bot_x", message_id="dup_1", surface_message_id="surf_1"
    )
    msg2 = _make_msg(
        sender_bot_user_id="bot_x", message_id="dup_1", surface_message_id="surf_2"
    )

    await adapter._handle_a2a_inbound_channel_broadcast(message=msg1, peer_agent_id="bot_x")
    await adapter._handle_a2a_inbound_channel_broadcast(message=msg2, peer_agent_id="bot_x")

    assert len(adapter._channel_transcripts["chan_1"]) == 1
    assert "dup_1" in adapter._recently_seen


# ---------------------------------------------------------------------------
# 4. Surface message-id dedup — same surface_message_id twice → second skipped
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inbound_surface_message_id_dedup():
    """ADR-011 §9 surface dedup: 같은 hermes.surface_message_id로 두 번
    inbound가 도착하면 (Discord natural-read + A2A wire 둘 다) 두 번째는
    transcript에 안 박음. A2A message_id는 다를 수 있음 — surface ID가 진짜
    유니크한 dedup key."""
    adapter = A2AAdapter(
        _make_config(
            listen="127.0.0.1:9999",
            inbound_handler="channel_broadcast",
            discord_bot_user_id="bot_self",
        )
    )
    msg1 = _make_msg(
        sender_bot_user_id="bot_x", message_id="a2a_1", surface_message_id="surf_X"
    )
    msg2 = _make_msg(
        sender_bot_user_id="bot_x", message_id="a2a_2", surface_message_id="surf_X"
    )

    await adapter._handle_a2a_inbound_channel_broadcast(message=msg1, peer_agent_id="bot_x")
    await adapter._handle_a2a_inbound_channel_broadcast(message=msg2, peer_agent_id="bot_x")

    assert len(adapter._channel_transcripts["chan_1"]) == 1
    assert "surf_X" in adapter._recently_seen_surface


# ---------------------------------------------------------------------------
# 5. TTL expiry — entries older than ttl_seconds get pruned
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_loop_prevention_ttl_expiry():
    """5-minute TTL: same message_id arriving 6 min later DOES land in
    transcript again (defensive — peer may legitimately resend after a long
    network glitch). Mock time.time so we don't actually sleep 5 min."""
    adapter = A2AAdapter(
        _make_config(
            listen="127.0.0.1:9999",
            inbound_handler="channel_broadcast",
            discord_bot_user_id="bot_self",
            loop_prevention_ttl_seconds=60,  # 60s for test brevity
        )
    )
    msg1 = _make_msg(
        sender_bot_user_id="bot_x", message_id="ttl_1", surface_message_id="surf_A"
    )
    msg2 = _make_msg(
        sender_bot_user_id="bot_x", message_id="ttl_1", surface_message_id="surf_A"
    )

    # Patch time inside the adapter module so we control "now"
    t0 = 1_000_000.0
    with patch("gateway.platforms.a2a.time.time", return_value=t0):
        await adapter._handle_a2a_inbound_channel_broadcast(message=msg1, peer_agent_id="bot_x")
    assert len(adapter._channel_transcripts["chan_1"]) == 1

    # Advance 120s — beyond the 60s TTL — and the same message comes again
    with patch("gateway.platforms.a2a.time.time", return_value=t0 + 120.0):
        await adapter._handle_a2a_inbound_channel_broadcast(message=msg2, peer_agent_id="bot_x")

    assert len(adapter._channel_transcripts["chan_1"]) == 2, (
        "after TTL expiry, the same message_id should be allowed through again"
    )


# ---------------------------------------------------------------------------
# 6. _mark_surface_outbound helper records a surface id (Task 35b prep)
# ---------------------------------------------------------------------------
def test_mark_surface_outbound_records_id():
    """Task 35b will call _mark_surface_outbound from the Discord reply hook
    so the bot's own surface message id is in _recently_seen_surface
    BEFORE any A2A wire echo could arrive. Phase 4 ships the primitive — the
    actual reply-hook wiring lands in Task 35b."""
    adapter = A2AAdapter(
        _make_config(
            listen="127.0.0.1:9999",
            inbound_handler="channel_broadcast",
            discord_bot_user_id="bot_self",
        )
    )
    adapter._mark_surface_outbound("surf_outgoing_1")
    assert "surf_outgoing_1" in adapter._recently_seen_surface


@pytest.mark.asyncio
async def test_mark_surface_outbound_blocks_subsequent_a2a_inbound():
    """Round-trip: _mark_surface_outbound(id) is called first (Discord reply
    fired by THIS bot) → A2A inbound with the same surface_message_id
    arrives later → handler skips it (we already saw our own message)."""
    adapter = A2AAdapter(
        _make_config(
            listen="127.0.0.1:9999",
            inbound_handler="channel_broadcast",
            discord_bot_user_id="bot_self",
        )
    )
    adapter._mark_surface_outbound("surf_self_777")

    # A2A inbound carrying the same surface id (echo via peer)
    msg = _make_msg(
        sender_bot_user_id="bot_other",  # NOT self — sender check won't catch this
        message_id="a2a_echo_1",
        surface_message_id="surf_self_777",
    )
    await adapter._handle_a2a_inbound_channel_broadcast(message=msg, peer_agent_id="bot_other")

    assert len(adapter._channel_transcripts["chan_1"]) == 0, (
        "A2A inbound matching a previously-marked surface id must be skipped"
    )


# ---------------------------------------------------------------------------
# 7. Non-self, non-dup message → transcript appends (sanity baseline)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inbound_non_self_non_dup_passes_through():
    """Sanity: prevention checks don't accidentally block normal inbound."""
    adapter = A2AAdapter(
        _make_config(
            listen="127.0.0.1:9999",
            inbound_handler="channel_broadcast",
            discord_bot_user_id="bot_self",
        )
    )
    msg = _make_msg(
        sender_bot_user_id="bot_other",
        message_id="fresh_1",
        surface_message_id="surf_fresh_1",
    )
    await adapter._handle_a2a_inbound_channel_broadcast(message=msg, peer_agent_id="bot_other")
    assert len(adapter._channel_transcripts["chan_1"]) == 1
