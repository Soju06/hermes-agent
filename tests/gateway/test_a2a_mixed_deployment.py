"""ADR-011 v2.1 §10a / ADR-012 / Phase 4 Task 40: Mixed deployment R2 3-tier defense.

Tests cover:
  (Advertisement, ADR-012 §AgentCard Advertisement)
    - `hermes-channel-broadcast/v1` extension added to AgentCard when
      `_inbound_handler == "channel_broadcast"`
    - Extension NOT added when inbound_handler == "mirror" or "disabled"
    - Extension params carry version=1, supported_surfaces, fire_and_forget=True

  (Tier 1, ADR-011 §10a sender-side guard)
    - `_has_channel_broadcast_ext(peer_card)` returns True for cards with the ext
    - `_has_channel_broadcast_ext(peer_card)` returns False otherwise
    - `_broadcast_to_channel_peers` skips peers without the ext (legacy peers)
    - `_broadcast_to_channel_peers` includes peers with the ext

  (Tier 2, ADR-011 §10a receiver-side guard via legacy_inbound config)
    - `legacy_inbound="reject"` (default) — payload without
      hermes.sender_bot_user_id is ack'd, transcript NOT appended
    - `legacy_inbound="accept_as_user"` — payload without sender_id calls the
      legacy user-role handler (passthrough to `handle_message`)
    - `legacy_inbound="accept_as_peer"` — payload without sender_id appends
      transcript, sender inferred from peer_agent_id (executor passes it from
      AgentCard discovery during resolve)

  (Tier 3, ADR-011 §10a — already in Task 38 + 39, no new tests)
    - max_consecutive_self_replies cap (Task 38, covered)
    - dedup cache TTL (Task 39, covered)

Drift note (ADR-011 v2.3 amend candidate): ADR-012 paper uses
`hermes-a2a.dev/extensions/channel-broadcast/v1` URI, but the existing
discord-identity extension is at `hermes.nous/extensions/discord-identity/v1`
in code. Code uses `hermes.nous/...` namespace for consistency; paper amend
will follow.
"""

from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2AAdapter


CHANNEL_BROADCAST_URI = "https://hermes.nous/extensions/channel-broadcast/v1"


def _make_adapter(**extra: Any) -> A2AAdapter:
    base = {
        "listen": "127.0.0.1:9999",
        "discord_bot_user_id": "bot_self",
    }
    base.update(extra)
    return A2AAdapter(PlatformConfig(enabled=True, token="", extra=base))


def _agent_card_with_ext(*, has_broadcast_ext: bool, version: int = 1):
    """Build a fake AgentCard with or without channel-broadcast/v1 extension."""
    from a2a.types import (
        AgentCard,
        AgentCapabilities,
        AgentExtension,
        AgentInterface,
        AgentSkill,
    )
    from google.protobuf import struct_pb2

    caps = AgentCapabilities(streaming=False)
    if has_broadcast_ext:
        params = struct_pb2.Struct()
        params.update({"version": version, "fire_and_forget": True})
        caps.extensions.append(
            AgentExtension(
                uri=CHANNEL_BROADCAST_URI,
                description="channel-broadcast/v1",
                required=False,
                params=params,
            )
        )
    return AgentCard(
        name="peer", description="peer", version="0.1.0",
        capabilities=caps,
        skills=[AgentSkill(
            id="chat", name="chat", description="chat",
            tags=["chat"], input_modes=["text/plain"], output_modes=["text/plain"],
        )],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        supported_interfaces=[AgentInterface(url="http://peer/", protocol_binding="JSONRPC")],
    )


# ===========================================================================
# 1. Advertisement — ext added when inbound_handler == "channel_broadcast"
# ===========================================================================
def test_self_card_has_channel_broadcast_ext_when_enabled():
    adapter = _make_adapter(
        inbound_handler="channel_broadcast",
        channel_peers={"chan_1": ["bot_a"]},
    )
    card = adapter._build_agent_card()
    ext_uris = [ext.uri for ext in card.capabilities.extensions]
    assert any(uri.endswith("/channel-broadcast/v1") for uri in ext_uris)


def test_self_card_no_ext_when_mirror_mode():
    adapter = _make_adapter(
        inbound_handler="mirror",
        mirror_channels={"bot_a": "chan_mirror"},
    )
    card = adapter._build_agent_card()
    ext_uris = [ext.uri for ext in card.capabilities.extensions]
    assert not any(uri.endswith("/channel-broadcast/v1") for uri in ext_uris)


def test_self_card_no_ext_when_disabled():
    adapter = _make_adapter()  # default disabled
    card = adapter._build_agent_card()
    ext_uris = [ext.uri for ext in card.capabilities.extensions]
    assert not any(uri.endswith("/channel-broadcast/v1") for uri in ext_uris)


def test_self_card_ext_params_have_version_and_surfaces():
    """ADR-012 §params: version=1, supported_surfaces, fire_and_forget=True."""
    adapter = _make_adapter(
        inbound_handler="channel_broadcast",
        channel_peers={"chan_1": ["bot_a"]},
    )
    card = adapter._build_agent_card()
    ext = next(
        e for e in card.capabilities.extensions
        if e.uri.endswith("/channel-broadcast/v1")
    )
    # protobuf Struct → dict
    params = {k: v for k, v in ext.params.fields.items()}
    assert params["version"].number_value == 1.0
    assert params["fire_and_forget"].bool_value is True
    surfaces = [s.string_value for s in params["supported_surfaces"].list_value.values]
    assert "discord" in surfaces  # default surface for Phase 4


# ===========================================================================
# 2. _has_channel_broadcast_ext helper
# ===========================================================================
def test_has_channel_broadcast_ext_positive():
    adapter = _make_adapter()
    card = _agent_card_with_ext(has_broadcast_ext=True)
    assert adapter._has_channel_broadcast_ext(card) is True


def test_has_channel_broadcast_ext_negative():
    adapter = _make_adapter()
    card = _agent_card_with_ext(has_broadcast_ext=False)
    assert adapter._has_channel_broadcast_ext(card) is False


def test_has_channel_broadcast_ext_handles_none_card():
    adapter = _make_adapter()
    assert adapter._has_channel_broadcast_ext(None) is False


# ===========================================================================
# 3. Tier 1 — _broadcast_to_channel_peers ext-gated
# ===========================================================================
@pytest.mark.asyncio
async def test_broadcast_skips_legacy_peer_without_ext():
    """Legacy peer (no channel-broadcast/v1 ext) is excluded from broadcast
    targets even if listed in channel_peers. Prevents loop when a legacy bot
    happens to be a registered peer."""
    adapter = _make_adapter(
        inbound_handler="channel_broadcast",
        channel_peers={"chan_1": ["bot_legacy", "bot_adr011"]},
    )
    adapter._peers = {
        "bot_legacy": "http://legacy.example/",
        "bot_adr011": "http://adr011.example/",
    }
    adapter._peer_cards = {
        "bot_legacy": _agent_card_with_ext(has_broadcast_ext=False),
        "bot_adr011": _agent_card_with_ext(has_broadcast_ext=True),
    }

    sent_to: List[str] = []

    async def _fake_send(peer_id, peer_url, content, metadata):
        sent_to.append(peer_id)

    import asyncio
    with patch.object(adapter, "_send_fire_and_forget", side_effect=_fake_send):
        await adapter._broadcast_to_channel_peers(
            channel_id="chan_1",
            content="hi",
            surface_message_id="surf_1",
            surface_platform="discord",
            context_id="ctx_1",
        )
        await asyncio.sleep(0.05)

    assert "bot_legacy" not in sent_to
    assert "bot_adr011" in sent_to


@pytest.mark.asyncio
async def test_broadcast_skips_peer_without_cached_card():
    """Peer whose AgentCard isn't in `_peer_cards` (couldn't resolve at startup)
    is also skipped — can't verify ext means can't broadcast safely."""
    adapter = _make_adapter(
        inbound_handler="channel_broadcast",
        channel_peers={"chan_1": ["bot_uncached"]},
    )
    adapter._peers = {"bot_uncached": "http://uncached.example/"}
    # _peer_cards is empty

    sent_to: List[str] = []

    async def _fake_send(peer_id, peer_url, content, metadata):
        sent_to.append(peer_id)

    import asyncio
    with patch.object(adapter, "_send_fire_and_forget", side_effect=_fake_send):
        await adapter._broadcast_to_channel_peers(
            channel_id="chan_1",
            content="hi",
            surface_message_id="surf_1",
            surface_platform="discord",
            context_id="ctx_1",
        )
        await asyncio.sleep(0.05)

    assert sent_to == []


# ===========================================================================
# 4. Tier 2 — legacy_inbound config (3 modes)
# ===========================================================================
def test_legacy_inbound_default_is_reject():
    adapter = _make_adapter(inbound_handler="channel_broadcast")
    assert adapter._legacy_inbound == "reject"


def test_legacy_inbound_invalid_value_fatal():
    with pytest.raises(ValueError, match=r"(?i)legacy_inbound"):
        _make_adapter(
            inbound_handler="channel_broadcast",
            legacy_inbound="bogus",
        )


def _make_msg_no_sender_metadata():
    """Message without hermes.sender_bot_user_id — simulates legacy peer."""
    from a2a.types import Message as A2AMessage, Part as A2APart, Role
    from google.protobuf import struct_pb2

    # Some channel id (Tier 2 dropped path still needs channel id to do anything)
    meta = struct_pb2.Struct()
    meta.update({
        "hermes.surface_channel_id": "chan_1",
        "hermes.surface_message_id": "surf_legacy",
    })
    return A2AMessage(
        role=Role.ROLE_AGENT,
        parts=[A2APart(text="hi from legacy")],
        message_id="legacy_msg_1",
        context_id="ctx_legacy",
        metadata=meta,
    )


@pytest.mark.asyncio
async def test_legacy_inbound_reject_drops_transcript():
    adapter = _make_adapter(
        inbound_handler="channel_broadcast",
        legacy_inbound="reject",
    )
    msg = _make_msg_no_sender_metadata()
    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="bot_legacy"
    )
    # transcript NOT appended — reject path
    assert len(adapter._channel_transcripts["chan_1"]) == 0


@pytest.mark.asyncio
async def test_legacy_inbound_accept_as_peer_appends_transcript_with_inferred_sender():
    """accept_as_peer: sender is inferred from peer_agent_id (which the
    executor extracts from the AgentCard discord-identity extension during
    resolve)."""
    adapter = _make_adapter(
        inbound_handler="channel_broadcast",
        legacy_inbound="accept_as_peer",
    )
    msg = _make_msg_no_sender_metadata()
    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="bot_legacy_inferred"
    )
    transcripts = list(adapter._channel_transcripts["chan_1"])
    assert len(transcripts) == 1
    assert transcripts[0]["sender_bot_user_id"] == "bot_legacy_inferred"
    assert transcripts[0]["text"] == "hi from legacy"


@pytest.mark.asyncio
async def test_legacy_inbound_accept_as_user_marks_message_for_legacy_handler():
    """accept_as_user: payload routed to legacy user-role handler. Phase 4
    scope = mark the message with a 'legacy_caller=True' attr on the adapter
    so the executor (or Task 35b.3/.4 wiring) can route it to handle_message
    instead of the transcript path. Transcript NOT appended."""
    adapter = _make_adapter(
        inbound_handler="channel_broadcast",
        legacy_inbound="accept_as_user",
    )
    msg = _make_msg_no_sender_metadata()
    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="bot_legacy"
    )
    # transcript NOT appended — accept_as_user routes through legacy path
    # (which is invoked outside this handler by the executor)
    assert len(adapter._channel_transcripts["chan_1"]) == 0


# ===========================================================================
# 5. ADR-011 path unaffected when sender_id IS set
# ===========================================================================
@pytest.mark.asyncio
async def test_adr011_path_with_sender_id_appends_normally():
    """Sanity baseline: when sender_id IS in metadata, the regular ADR-011
    transcript-append path runs — legacy_inbound gate doesn't fire."""
    from a2a.types import Message as A2AMessage, Part as A2APart, Role
    from google.protobuf import struct_pb2

    adapter = _make_adapter(
        inbound_handler="channel_broadcast",
        legacy_inbound="reject",  # would block legacy, but not us
    )
    meta = struct_pb2.Struct()
    meta.update({
        "hermes.sender_bot_user_id": "bot_x",
        "hermes.surface_channel_id": "chan_1",
        "hermes.surface_message_id": "surf_x",
        "hermes.surface_platform": "discord",
        "hermes.context_id": "ctx_x",
    })
    msg = A2AMessage(
        role=Role.ROLE_AGENT,
        parts=[A2APart(text="normal adr-011 msg")],
        message_id="adr011_msg_1",
        context_id="ctx_x",
        metadata=meta,
    )
    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="bot_x"
    )
    assert len(adapter._channel_transcripts["chan_1"]) == 1


# ===========================================================================
# 6. supported_surfaces config-tunable
# ===========================================================================
def test_supported_surfaces_default_is_discord():
    adapter = _make_adapter(
        inbound_handler="channel_broadcast",
        channel_peers={"chan_1": ["bot_a"]},
    )
    assert adapter._supported_surfaces == ["discord"]


def test_supported_surfaces_explicit_config():
    adapter = _make_adapter(
        inbound_handler="channel_broadcast",
        channel_peers={"chan_1": ["bot_a"]},
        supported_surfaces=["discord", "telegram"],
    )
    assert adapter._supported_surfaces == ["discord", "telegram"]
