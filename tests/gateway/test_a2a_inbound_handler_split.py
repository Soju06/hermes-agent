"""ADR-011 v2.1 §8a / Phase 4 Task 37: Inbound handler split + transcript append (R1).

Tests that:
  - `_channel_transcripts` field is a defaultdict(deque(maxlen=100)) keyed by surface
    channel id; `_channel_transcripts[channel_id]` is auto-created on first access.
  - `_inbound_handler` resolves correctly:
      * Explicit `"channel_broadcast"` → channel_broadcast path
      * Explicit `"mirror"` → ADR-007 v3 mirror path
      * Auto (config not set) + `channel_peers` 박힘 → channel_broadcast
      * Auto + `mirror_channels` 박힘 → mirror
      * Auto + neither → disabled (no-op inbound)
      * Mixed (`channel_peers` AND `mirror_channels` both set) + auto → FATAL at __init__
        (explicit `inbound_handler` overrides the mixed-config fatal)
  - `_handle_a2a_inbound_channel_broadcast(message, peer_agent_id)` appends a
    TranscriptEntry to `_channel_transcripts[channel_id]` and does NOT trigger
    the legacy mirror or handle_message paths.
  - TranscriptEntry shape: {sender_bot_user_id, text, timestamp, message_id,
    surface_message_id, surface_channel_id}.
  - ADR-007 v3 mirror path is fully preserved when `_inbound_handler == "mirror"`
    (regression — Task 26 sandbox-verified behavior unchanged).

These are pure adapter-side tests with mocked executor inputs. The executor
branch (Task 37 §3) is exercised through a dedicated `_dispatch_a2a_inbound`
shim that the executor calls; the shim picks the right path based on
`_inbound_handler`.
"""

from collections import deque
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


# ---------------------------------------------------------------------------
# 1. _channel_transcripts default + per-channel autocreate
# ---------------------------------------------------------------------------
def test_channel_transcripts_default_empty_and_autocreates():
    """ADR-011 §3: _channel_transcripts is a defaultdict-like, deque-backed
    per-channel store. Accessing a missing channel auto-creates an empty
    deque with maxlen=100 (R7 timing race + memory bound)."""
    adapter = A2AAdapter(_make_config(listen="127.0.0.1:9999"))
    assert hasattr(adapter, "_channel_transcripts")
    # Accessing a fresh channel id should yield an empty deque, not KeyError
    deq = adapter._channel_transcripts["chan_fresh"]
    assert isinstance(deq, deque)
    assert deq.maxlen == 100
    assert len(deq) == 0


# ---------------------------------------------------------------------------
# 2. _inbound_handler resolution — explicit channel_broadcast
# ---------------------------------------------------------------------------
def test_inbound_handler_explicit_channel_broadcast():
    config = _make_config(
        listen="127.0.0.1:9999", inbound_handler="channel_broadcast"
    )
    adapter = A2AAdapter(config)
    assert adapter._inbound_handler == "channel_broadcast"


# ---------------------------------------------------------------------------
# 3. _inbound_handler resolution — explicit mirror
# ---------------------------------------------------------------------------
def test_inbound_handler_explicit_mirror():
    config = _make_config(listen="127.0.0.1:9999", inbound_handler="mirror")
    adapter = A2AAdapter(config)
    assert adapter._inbound_handler == "mirror"


# ---------------------------------------------------------------------------
# 4. _inbound_handler auto-resolution — channel_peers → channel_broadcast
# ---------------------------------------------------------------------------
def test_inbound_handler_auto_channel_peers_routes_channel_broadcast():
    config = _make_config(
        listen="127.0.0.1:9999",
        channel_peers={"chan_1": ["bot_a", "bot_b"]},
    )
    adapter = A2AAdapter(config)
    assert adapter._inbound_handler == "channel_broadcast"


# ---------------------------------------------------------------------------
# 5. _inbound_handler auto-resolution — mirror_channels → mirror
# ---------------------------------------------------------------------------
def test_inbound_handler_auto_mirror_channels_routes_mirror():
    config = _make_config(
        listen="127.0.0.1:9999",
        mirror_channels={"bot_a": "chan_mirror"},
    )
    adapter = A2AAdapter(config)
    assert adapter._inbound_handler == "mirror"


# ---------------------------------------------------------------------------
# 6. _inbound_handler auto-resolution — neither → disabled
# ---------------------------------------------------------------------------
def test_inbound_handler_auto_neither_routes_disabled():
    """Neither channel_peers nor mirror_channels set + no explicit
    inbound_handler → 'disabled' (inbound is a no-op, executor still routes
    handle_message but mirror+transcript paths both skip)."""
    adapter = A2AAdapter(_make_config(listen="127.0.0.1:9999"))
    assert adapter._inbound_handler == "disabled"


# ---------------------------------------------------------------------------
# 7. _inbound_handler mixed-config fatal at __init__
# ---------------------------------------------------------------------------
def test_inbound_handler_mixed_config_fatal_at_init():
    """ADR-011 v2.1 §8a + plan §Task 37: `channel_peers` AND `mirror_channels`
    both set with no explicit `inbound_handler` → fatal at __init__. Operator
    must pick one explicitly."""
    config = _make_config(
        listen="127.0.0.1:9999",
        channel_peers={"chan_1": ["bot_a"]},
        mirror_channels={"bot_a": "chan_mirror"},
    )
    with pytest.raises(ValueError, match=r"(?i)mixed.*config|inbound_handler"):
        A2AAdapter(config)


# ---------------------------------------------------------------------------
# 8. Explicit inbound_handler overrides mixed-config fatal
# ---------------------------------------------------------------------------
def test_inbound_handler_explicit_overrides_mixed_fatal():
    """If operator explicitly sets `inbound_handler`, mixed-config no longer
    fatal — they've made the call. Both data structures stay loaded so the
    operator can migrate from one to the other gradually."""
    config = _make_config(
        listen="127.0.0.1:9999",
        inbound_handler="channel_broadcast",
        channel_peers={"chan_1": ["bot_a"]},
        mirror_channels={"bot_a": "chan_mirror"},
    )
    adapter = A2AAdapter(config)  # must NOT raise
    assert adapter._inbound_handler == "channel_broadcast"
    assert "chan_1" in adapter._channel_peers
    assert "bot_a" in adapter._mirror_channels


# ---------------------------------------------------------------------------
# 9. _handle_a2a_inbound_channel_broadcast appends transcript entry
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inbound_channel_broadcast_appends_transcript():
    """ADR-011 §3 / §4: channel_broadcast path appends a TranscriptEntry to
    _channel_transcripts[channel_id], where channel_id is sourced from the
    payload's hermes.surface_channel_id metadata."""
    config = _make_config(
        listen="127.0.0.1:9999",
        inbound_handler="channel_broadcast",
    )
    adapter = A2AAdapter(config)

    # Build a fake A2A Message with hermes.* metadata
    from a2a.types import Message as A2AMessage, Part as A2APart, Role
    from google.protobuf import struct_pb2

    meta = struct_pb2.Struct()
    meta.update(
        {
            "hermes.sender_bot_user_id": "bot_x",
            "hermes.surface_channel_id": "chan_42",
            "hermes.surface_message_id": "surf_msg_777",
            "hermes.surface_platform": "discord",
            "hermes.context_id": "ctx_999",
        }
    )
    msg = A2AMessage(
        role=Role.ROLE_AGENT,
        parts=[A2APart(text="peer reply text")],
        message_id="a2a_msg_001",
        context_id="ctx_999",
        metadata=meta,
    )

    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="bot_x"
    )

    deq = adapter._channel_transcripts["chan_42"]
    assert len(deq) == 1
    entry = deq[0]
    assert entry["sender_bot_user_id"] == "bot_x"
    assert entry["text"] == "peer reply text"
    assert entry["message_id"] == "a2a_msg_001"
    assert entry["surface_message_id"] == "surf_msg_777"
    assert entry["surface_channel_id"] == "chan_42"
    assert "timestamp" in entry  # float epoch — exact value not pinned


# ---------------------------------------------------------------------------
# 10. _dispatch_a2a_inbound routes channel_broadcast → no mirror call
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dispatch_inbound_channel_broadcast_skips_mirror():
    """Executor shim: when _inbound_handler='channel_broadcast', the legacy
    ADR-007 v3 _mirror_a2a_inbound_to_discord path is NOT invoked. Only the
    channel_broadcast handler runs."""
    config = _make_config(
        listen="127.0.0.1:9999",
        inbound_handler="channel_broadcast",
    )
    adapter = A2AAdapter(config)

    from a2a.types import Message as A2AMessage, Part as A2APart, Role
    from google.protobuf import struct_pb2

    meta = struct_pb2.Struct()
    meta.update(
        {
            "hermes.sender_bot_user_id": "bot_x",
            "hermes.surface_channel_id": "chan_1",
            "hermes.surface_message_id": "surf_1",
            "hermes.surface_platform": "discord",
            "hermes.context_id": "ctx_1",
        }
    )
    msg = A2AMessage(
        role=Role.ROLE_AGENT,
        parts=[A2APart(text="hi")],
        message_id="m1",
        context_id="ctx_1",
        metadata=meta,
    )

    with (
        patch.object(
            adapter,
            "_mirror_a2a_inbound_to_discord",
            new=AsyncMock(),
        ) as mirror_spy,
        patch.object(
            adapter,
            "_handle_a2a_inbound_channel_broadcast",
            new=AsyncMock(),
        ) as broadcast_spy,
    ):
        await adapter._dispatch_a2a_inbound(
            message=msg, peer_agent_id="bot_x", text="hi"
        )

    mirror_spy.assert_not_called()
    broadcast_spy.assert_awaited_once()


# ---------------------------------------------------------------------------
# 11. _dispatch_a2a_inbound routes mirror → no channel_broadcast call
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dispatch_inbound_mirror_path_preserved():
    """Regression: when _inbound_handler='mirror' (ADR-007 v3 / Phase 3a
    behavior), the legacy mirror path runs and the channel_broadcast handler
    does NOT. This pins Task 26 sandbox-verified flow."""
    config = _make_config(
        listen="127.0.0.1:9999",
        inbound_handler="mirror",
    )
    adapter = A2AAdapter(config)

    from a2a.types import Message as A2AMessage, Part as A2APart, Role

    msg = A2AMessage(
        role=Role.ROLE_AGENT,
        parts=[A2APart(text="legacy hi")],
        message_id="m2",
        context_id="ctx_2",
    )

    with (
        patch.object(
            adapter,
            "_mirror_a2a_inbound_to_discord",
            new=AsyncMock(),
        ) as mirror_spy,
        patch.object(
            adapter,
            "_handle_a2a_inbound_channel_broadcast",
            new=AsyncMock(),
        ) as broadcast_spy,
    ):
        await adapter._dispatch_a2a_inbound(
            message=msg, peer_agent_id="bot_x", text="legacy hi"
        )

    mirror_spy.assert_awaited_once_with("bot_x", "legacy hi")
    broadcast_spy.assert_not_called()


# ---------------------------------------------------------------------------
# 12. _dispatch_a2a_inbound disabled path → neither called
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dispatch_inbound_disabled_skips_both():
    """No config → 'disabled' → both legacy mirror AND channel_broadcast
    handlers skipped. handle_message dispatch still happens in the executor
    (that's the existing test_a2a_roundtrip path); _dispatch_a2a_inbound just
    routes the side-effect hooks."""
    adapter = A2AAdapter(_make_config(listen="127.0.0.1:9999"))
    assert adapter._inbound_handler == "disabled"

    from a2a.types import Message as A2AMessage, Part as A2APart, Role

    msg = A2AMessage(
        role=Role.ROLE_AGENT,
        parts=[A2APart(text="no-op")],
        message_id="m3",
        context_id="ctx_3",
    )

    with (
        patch.object(
            adapter,
            "_mirror_a2a_inbound_to_discord",
            new=AsyncMock(),
        ) as mirror_spy,
        patch.object(
            adapter,
            "_handle_a2a_inbound_channel_broadcast",
            new=AsyncMock(),
        ) as broadcast_spy,
    ):
        await adapter._dispatch_a2a_inbound(
            message=msg, peer_agent_id="bot_x", text="no-op"
        )

    mirror_spy.assert_not_called()
    broadcast_spy.assert_not_called()
