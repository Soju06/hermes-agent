"""ADR-011 v2.1 / Phase 4 Task 35b.1 + 35b.2: gateway/run.py wiring smoke tests.

Tests cover (35b.1 — config wiring):
  - display.platforms.a2a.channel_peers → adapter._channel_peers
  - display.platforms.a2a.inbound_handler → adapter._inbound_handler
  - display.platforms.a2a.reply_policy → adapter._reply_policy
  - display.platforms.a2a.loop_prevention_ttl_seconds → adapter._loop_prevention_ttl_seconds
  - No config → all defaults preserved (production isolation)

Tests cover (35b.2 — outbound broadcast hook):
  - When channel_broadcast mode + Discord platform + channel in _channel_peers,
    a reply sent via the gateway hooks fires `_broadcast_to_channel_peers` +
    `_mark_surface_outbound` + `mark_self_reply`
  - When inbound_handler != "channel_broadcast" → hook is a no-op
  - When channel_id not in _channel_peers → hook is a no-op
  - When platform is not Discord/Telegram → hook is a no-op

The wiring helpers we test are:
  - `gateway.run._wire_a2a_display_config(adapter, display_cfg)` — config wire
  - `gateway.run._maybe_broadcast_a2a_reply(adapter, source, reply_text,
     message_id)` — outbound hook

Both helpers live in gateway/run.py and are pulled out as module-level
functions specifically so they can be tested without standing up a full
GatewayRunner.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2AAdapter


def _make_adapter(**extra: Any) -> A2AAdapter:
    base = {
        "listen": "127.0.0.1:9999",
        "discord_bot_user_id": "bot_self",
    }
    base.update(extra)
    return A2AAdapter(PlatformConfig(enabled=True, token="", extra=base))


# ===========================================================================
# 35b.1 — config wiring
# ===========================================================================
def test_config_wire_channel_peers():
    """display.platforms.a2a.channel_peers → adapter._channel_peers."""
    from gateway.run import _wire_a2a_display_config

    adapter = _make_adapter()
    assert adapter._channel_peers == {}
    _wire_a2a_display_config(
        adapter,
        {"channel_peers": {"chan_1": ["bot_a", "bot_b"]}},
    )
    assert adapter._channel_peers == {"chan_1": ["bot_a", "bot_b"]}


def test_config_wire_inbound_handler():
    """display.platforms.a2a.inbound_handler → adapter._inbound_handler.
    Also forces re-resolution after the wire — channel_peers wired AFTER
    __init__ would otherwise leave _inbound_handler='disabled'."""
    from gateway.run import _wire_a2a_display_config

    adapter = _make_adapter()
    assert adapter._inbound_handler == "disabled"
    _wire_a2a_display_config(
        adapter,
        {
            "channel_peers": {"chan_1": ["bot_a"]},
            "inbound_handler": "channel_broadcast",
        },
    )
    assert adapter._inbound_handler == "channel_broadcast"


def test_config_wire_inbound_handler_auto_resolves_from_channel_peers():
    """If only channel_peers is set (no explicit inbound_handler), the wire
    re-resolves _inbound_handler to 'channel_broadcast' since that's the
    auto-decision Task 37 makes at __init__ when channel_peers is non-empty."""
    from gateway.run import _wire_a2a_display_config

    adapter = _make_adapter()
    _wire_a2a_display_config(adapter, {"channel_peers": {"chan_1": ["bot_a"]}})
    assert adapter._inbound_handler == "channel_broadcast"


def test_config_wire_reply_policy():
    """display.platforms.a2a.reply_policy → adapter._reply_policy."""
    from gateway.run import _wire_a2a_display_config

    adapter = _make_adapter()
    _wire_a2a_display_config(
        adapter,
        {
            "reply_policy": {
                "mode": "mention_only",
                "max_consecutive_self_replies": 5,
                "cooldown_after_silent_decision": 90,
                "channel_hints": ["help"],
            }
        },
    )
    p = adapter._reply_policy
    assert p["mode"] == "mention_only"
    assert p["max_consecutive_self_replies"] == 5
    assert p["cooldown_after_silent_decision"] == 90
    assert p["channel_hints"] == ["help"]


def test_config_wire_loop_prevention_ttl():
    from gateway.run import _wire_a2a_display_config

    adapter = _make_adapter()
    _wire_a2a_display_config(adapter, {"loop_prevention_ttl_seconds": 600})
    assert adapter._loop_prevention_ttl_seconds == 600


def test_config_wire_empty_dict_is_noop():
    """Production isolation: empty/missing display config → all defaults
    preserved (no field mutated). This is the critical safety property —
    nachoneko/mymel don't set channel_peers, so their adapter must remain in
    'disabled' or 'mirror' mode after the wire returns."""
    from gateway.run import _wire_a2a_display_config

    adapter = _make_adapter()
    before_inbound = adapter._inbound_handler
    before_peers = dict(adapter._channel_peers)
    before_policy_mode = adapter._reply_policy["mode"]
    before_ttl = adapter._loop_prevention_ttl_seconds

    _wire_a2a_display_config(adapter, {})

    assert adapter._inbound_handler == before_inbound
    assert adapter._channel_peers == before_peers
    assert adapter._reply_policy["mode"] == before_policy_mode
    assert adapter._loop_prevention_ttl_seconds == before_ttl


def test_config_wire_invalid_reply_policy_mode_raises():
    """Bad mode in display config should raise — operator typo, not silent
    drift."""
    from gateway.run import _wire_a2a_display_config

    adapter = _make_adapter()
    with pytest.raises(ValueError, match=r"(?i)invalid|mode"):
        _wire_a2a_display_config(adapter, {"reply_policy": {"mode": "bogus"}})


def test_config_wire_does_not_clobber_mirror_mode():
    """If mirror_channels is already wired (Phase 3a path) and no channel_peers
    in display config, the wire must NOT downgrade _inbound_handler. This is
    the production safety property for nachoneko/mymel — their adapter starts
    in 'mirror' mode and stays there."""
    from gateway.run import _wire_a2a_display_config

    adapter = _make_adapter(mirror_channels={"bot_x": "chan_mirror"})
    assert adapter._inbound_handler == "mirror"

    _wire_a2a_display_config(adapter, {})  # empty ADR-011 config
    assert adapter._inbound_handler == "mirror"


# ===========================================================================
# 35b.2 — outbound broadcast hook
# ===========================================================================
def _make_source(platform_value="discord", chat_id="chan_1", session_id="ctx_1"):
    """Build a minimal MessageSource stub for _maybe_broadcast_a2a_reply."""
    platform = SimpleNamespace(value=platform_value)
    return SimpleNamespace(
        platform=platform,
        chat_id=chat_id,
        session_id=session_id,
        user_id="user_1",
    )


@pytest.mark.asyncio
async def test_outbound_hook_fires_when_channel_broadcast_mode():
    """ADR-011 path: bot reply sent on Discord → broadcast + mark_self_reply
    + _mark_surface_outbound all called when channel matches _channel_peers."""
    from gateway.run import _maybe_broadcast_a2a_reply

    adapter = _make_adapter(
        channel_peers={"chan_1": ["bot_a", "bot_b"]},
        inbound_handler="channel_broadcast",
    )

    source = _make_source(platform_value="discord", chat_id="chan_1")

    with (
        patch.object(
            adapter, "_broadcast_to_channel_peers", new=AsyncMock()
        ) as broadcast_spy,
        patch.object(adapter, "_mark_surface_outbound") as mark_surface_spy,
        patch.object(adapter, "mark_self_reply") as mark_self_spy,
    ):
        await _maybe_broadcast_a2a_reply(
            adapter,
            source=source,
            reply_text="hi peers",
            surface_message_id="surf_msg_42",
        )
        # asyncio.create_task scheduled the broadcast coroutine — yield to
        # the event loop so it actually executes before we check the spy.
        import asyncio as _aio
        await _aio.sleep(0)

    broadcast_spy.assert_awaited_once()
    call = broadcast_spy.await_args
    assert call.kwargs["channel_id"] == "chan_1"
    assert call.kwargs["content"] == "hi peers"
    assert call.kwargs["surface_message_id"] == "surf_msg_42"
    assert call.kwargs["surface_platform"] == "discord"

    mark_surface_spy.assert_called_once_with("surf_msg_42")
    mark_self_spy.assert_called_once_with("chan_1")


@pytest.mark.asyncio
async def test_outbound_hook_noop_when_inbound_handler_not_channel_broadcast():
    """nachoneko/mymel path: inbound_handler='mirror' → hook is a no-op.
    Production safety property."""
    from gateway.run import _maybe_broadcast_a2a_reply

    adapter = _make_adapter(
        mirror_channels={"bot_x": "chan_mirror"},
        channel_peers={"chan_1": ["bot_a"]},  # would normally broadcast
        inbound_handler="mirror",  # but explicit override → mirror mode
    )
    source = _make_source(platform_value="discord", chat_id="chan_1")

    with (
        patch.object(
            adapter, "_broadcast_to_channel_peers", new=AsyncMock()
        ) as broadcast_spy,
        patch.object(adapter, "_mark_surface_outbound") as mark_surface_spy,
        patch.object(adapter, "mark_self_reply") as mark_self_spy,
    ):
        await _maybe_broadcast_a2a_reply(
            adapter,
            source=source,
            reply_text="hi",
            surface_message_id="surf_1",
        )

    broadcast_spy.assert_not_called()
    mark_surface_spy.assert_not_called()
    mark_self_spy.assert_not_called()


@pytest.mark.asyncio
async def test_outbound_hook_noop_when_channel_not_in_peers():
    from gateway.run import _maybe_broadcast_a2a_reply

    adapter = _make_adapter(
        channel_peers={"chan_1": ["bot_a"]},
        inbound_handler="channel_broadcast",
    )
    source = _make_source(platform_value="discord", chat_id="chan_unknown")

    with (
        patch.object(
            adapter, "_broadcast_to_channel_peers", new=AsyncMock()
        ) as broadcast_spy,
        patch.object(adapter, "_mark_surface_outbound") as mark_surface_spy,
        patch.object(adapter, "mark_self_reply") as mark_self_spy,
    ):
        await _maybe_broadcast_a2a_reply(
            adapter,
            source=source,
            reply_text="hi",
            surface_message_id="surf_1",
        )

    broadcast_spy.assert_not_called()
    mark_surface_spy.assert_not_called()
    mark_self_spy.assert_not_called()


@pytest.mark.asyncio
async def test_outbound_hook_noop_when_platform_is_not_discord_telegram():
    """Slack/WhatsApp/etc messages don't broadcast — only Discord/Telegram are
    in scope for ADR-011 channel-broadcast in Phase 4."""
    from gateway.run import _maybe_broadcast_a2a_reply

    adapter = _make_adapter(
        channel_peers={"chan_1": ["bot_a"]},
        inbound_handler="channel_broadcast",
    )
    source = _make_source(platform_value="slack", chat_id="chan_1")

    with (
        patch.object(
            adapter, "_broadcast_to_channel_peers", new=AsyncMock()
        ) as broadcast_spy,
        patch.object(adapter, "_mark_surface_outbound") as mark_surface_spy,
        patch.object(adapter, "mark_self_reply") as mark_self_spy,
    ):
        await _maybe_broadcast_a2a_reply(
            adapter,
            source=source,
            reply_text="hi",
            surface_message_id="surf_1",
        )

    broadcast_spy.assert_not_called()
    mark_surface_spy.assert_not_called()
    mark_self_spy.assert_not_called()


@pytest.mark.asyncio
async def test_outbound_hook_noop_when_adapter_none():
    """_maybe_broadcast_a2a_reply(None, ...) → no error, no-op. Gateway
    might not have an A2A adapter wired at all."""
    from gateway.run import _maybe_broadcast_a2a_reply

    source = _make_source()
    await _maybe_broadcast_a2a_reply(
        None, source=source, reply_text="hi", surface_message_id="surf_1"
    )
    # No exception — pass
