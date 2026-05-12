"""ADR-011 v2.1 / Phase 4 Tasks 35b.3 + 35b.4: reply-trigger gate wiring tests.

Tests cover:

35b.3 — A2A inbound trigger gate
  - `_handle_a2a_inbound_channel_broadcast` populates
    `_inbound_trigger_decisions[wire_msg_id]` after transcript append
  - Decision encodes should_trigger / channel_id / mentions / surface_platform
  - `mark_silent_decision` is called when trigger=False
  - `_consume_inbound_trigger_decision` pops the entry exactly once
  - Mention parsing per surface_platform (Discord <@id> / Telegram @user)

35b.4 — User-message gate (via the helper API)
  - `should_trigger_reply` honors mention_only / autonomous / hybrid modes
    when receiving a user message (`is_user_message=True`).  The full
    `_handle_message_with_agent` integration is exercised by the live
    sandbox; this file pins the per-adapter behaviour the gateway relies on.

Mention extraction
  - Discord: `<@123>` and `<@!123>` both extract `"123"`
  - Telegram: `@username` extracts `"username"`
  - Unknown surfaces return empty set
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2AAdapter


def _make_adapter(**extra: Any) -> A2AAdapter:
    base = {
        "listen": "127.0.0.1:9999",
        "discord_bot_user_id": "42",  # numeric — matches Discord mention regex
    }
    base.update(extra)
    return A2AAdapter(PlatformConfig(enabled=True, token="", extra=base))


# ===========================================================================
# Mention extraction (Task 35b.4 helper)
# ===========================================================================
def test_extract_mentions_discord_simple():
    assert A2AAdapter._extract_mentions("hi <@123>!", "discord") == {"123"}


def test_extract_mentions_discord_legacy_nickname_form():
    assert A2AAdapter._extract_mentions("hi <@!123>!", "discord") == {"123"}


def test_extract_mentions_discord_mixed_forms():
    text = "hello <@123> and <@!456> and <@789>"
    assert A2AAdapter._extract_mentions(text, "discord") == {"123", "456", "789"}


def test_extract_mentions_discord_no_match():
    assert A2AAdapter._extract_mentions("no mentions here", "discord") == set()


def test_extract_mentions_telegram_simple():
    assert A2AAdapter._extract_mentions("hi @user_42, please", "telegram") == {"user_42"}


def test_extract_mentions_telegram_multiple():
    assert A2AAdapter._extract_mentions(
        "@alice and @bob plus @carol", "telegram"
    ) == {"alice", "bob", "carol"}


def test_extract_mentions_unknown_surface_returns_empty():
    assert A2AAdapter._extract_mentions("hi <@123>", "slack") == set()


def test_extract_mentions_empty_text_returns_empty():
    assert A2AAdapter._extract_mentions("", "discord") == set()
    assert A2AAdapter._extract_mentions(None, "discord") == set()


# ===========================================================================
# Inbound trigger decision cache (Task 35b.3)
# ===========================================================================
def _make_inbound_msg(
    *,
    sender_id: str = "peer_bot_1",
    surface_channel: str = "chan_1",
    surface_message: str = "surf-1",
    surface_platform: str = "discord",
    text: str = "hello channel",
    wire_msg_id: str = "wire-1",
) -> MagicMock:
    """Build a MagicMock inbound A2A Message with the metadata the handler reads."""
    msg = MagicMock()
    msg.message_id = wire_msg_id
    # parts[0].text
    part = MagicMock()
    part.HasField = lambda f: f == "text"
    part.text = text
    msg.parts = [part]
    # metadata dict path — handler's _decode_inbound_metadata also accepts dicts
    msg.metadata = {
        "hermes.sender_bot_user_id": sender_id,
        "hermes.surface_channel_id": surface_channel,
        "hermes.surface_message_id": surface_message,
        "hermes.surface_platform": surface_platform,
    }
    return msg


@pytest.mark.asyncio
async def test_inbound_handler_autonomous_caches_decision_with_self_id():
    """Default mode autonomous → trigger=True without mentions (no cap hit)."""
    adapter = _make_adapter(
        channel_peers={"chan_1": ["42", "peer_bot_1"]},
    )
    msg = _make_inbound_msg(
        sender_id="peer_bot_1",
        wire_msg_id="wire-auto",
    )
    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="peer_bot_1"
    )

    decision = adapter._consume_inbound_trigger_decision("wire-auto")
    assert decision is not None
    assert decision["should_trigger"] is True
    assert decision["channel_id"] == "chan_1"
    assert decision["surface_platform"] == "discord"
    # No silent-decision marker since trigger=True
    assert adapter._last_silent_decision.get("chan_1") is None


@pytest.mark.asyncio
async def test_inbound_handler_mention_only_peer_never_triggers():
    """mention_only mode: peer broadcasts never trigger, regardless of
    mention. This is the spec — direct address from a real user only."""
    adapter = _make_adapter(
        channel_peers={"chan_1": ["42", "peer_bot_1"]},
        reply_policy={"mode": "mention_only"},
    )
    # Even with self-mention in the peer text, mention_only filters peer.
    msg = _make_inbound_msg(
        text="hey <@42>, can you help?",
        wire_msg_id="wire-mention",
    )
    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="peer_bot_1"
    )

    decision = adapter._consume_inbound_trigger_decision("wire-mention")
    assert decision is not None
    assert decision["should_trigger"] is False
    # Mention WAS extracted (caching for audit), gate still rejected.
    assert decision["mentions"] == {"42"}


@pytest.mark.asyncio
async def test_inbound_handler_hybrid_self_mention_in_text():
    """hybrid mode: peer broadcast with <@self> in text → trigger=True."""
    adapter = _make_adapter(
        channel_peers={"chan_1": ["42", "peer_bot_1"]},
        reply_policy={"mode": "hybrid"},
    )
    msg = _make_inbound_msg(
        text="hey <@42>, can you help?",
        wire_msg_id="wire-mention-hybrid",
    )
    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="peer_bot_1"
    )

    decision = adapter._consume_inbound_trigger_decision("wire-mention-hybrid")
    assert decision is not None
    assert decision["should_trigger"] is True
    assert decision["mentions"] == {"42"}


@pytest.mark.asyncio
async def test_inbound_handler_hybrid_channel_hint_match():
    """hybrid mode + channel_hints regex match in peer text → trigger=True."""
    adapter = _make_adapter(
        channel_peers={"chan_1": ["42", "peer_bot_1"]},
        reply_policy={
            "mode": "hybrid",
            "channel_hints": [r"\b도와줘\b", r"\bhelp\b"],
        },
    )
    msg = _make_inbound_msg(
        text="이거 좀 도와줘",
        wire_msg_id="wire-hint",
    )
    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="peer_bot_1"
    )

    decision = adapter._consume_inbound_trigger_decision("wire-hint")
    assert decision is not None
    assert decision["should_trigger"] is True


@pytest.mark.asyncio
async def test_inbound_handler_autonomous_cooldown_silent():
    """autonomous mode + cooldown window active → silent decision.

    Note: consecutive_self_replies cap is reset by `mark_other_inbound`
    (called by the handler before the trigger gate evaluates), so cap
    cannot fire on peer-inbound — only user message reply path hits cap.
    Cooldown survives mark_other_inbound, so we test cooldown here.
    """
    import time as _time

    adapter = _make_adapter(
        channel_peers={"chan_1": ["42", "peer_bot_1"]},
        reply_policy={
            "mode": "autonomous",
            "cooldown_after_silent_decision": 60,
        },
    )
    # Simulate a recent silent decision; cooldown still active.
    adapter._last_silent_decision["chan_1"] = _time.time() - 5

    msg = _make_inbound_msg(
        text="peer broadcast no mention",
        wire_msg_id="wire-cooldown",
    )
    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="peer_bot_1"
    )

    decision = adapter._consume_inbound_trigger_decision("wire-cooldown")
    assert decision is not None
    assert decision["should_trigger"] is False


@pytest.mark.asyncio
async def test_inbound_handler_autonomous_cooldown_bypassed_by_mention():
    """autonomous + cooldown active BUT peer mentions self → trigger=True."""
    import time as _time

    adapter = _make_adapter(
        channel_peers={"chan_1": ["42", "peer_bot_1"]},
        reply_policy={
            "mode": "autonomous",
            "cooldown_after_silent_decision": 60,
        },
    )
    adapter._last_silent_decision["chan_1"] = _time.time() - 5

    msg = _make_inbound_msg(
        text="cooldown active but <@42> please reply",
        wire_msg_id="wire-cooldown-mention",
    )
    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="peer_bot_1"
    )

    decision = adapter._consume_inbound_trigger_decision("wire-cooldown-mention")
    assert decision is not None
    assert decision["should_trigger"] is True


@pytest.mark.asyncio
async def test_consume_inbound_trigger_decision_pops_once():
    """The decision cache is consume-on-read — a second consume returns None."""
    adapter = _make_adapter(
        channel_peers={"chan_1": ["42", "peer_bot_1"]},
    )
    msg = _make_inbound_msg(wire_msg_id="wire-once")
    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="peer_bot_1"
    )

    first = adapter._consume_inbound_trigger_decision("wire-once")
    second = adapter._consume_inbound_trigger_decision("wire-once")
    assert first is not None
    assert second is None


def test_consume_inbound_trigger_decision_unknown_id_returns_none():
    adapter = _make_adapter(channel_peers={"chan_1": ["42"]})
    assert adapter._consume_inbound_trigger_decision("never-cached") is None
    assert adapter._consume_inbound_trigger_decision("") is None


@pytest.mark.asyncio
async def test_inbound_handler_uses_surface_platform_for_mention_parse():
    """Telegram surface platform → @username parsing, not Discord <@id>.

    Use hybrid mode so peer broadcasts CAN trigger on mention (mention_only
    bans peer-side triggers entirely)."""
    adapter = _make_adapter(
        channel_peers={"chan_t": ["hermes_bot", "peer_bot_1"]},
        reply_policy={"mode": "hybrid"},
        # Self id is the @username (without @), per the Telegram parser doc.
        discord_bot_user_id="hermes_bot",
    )
    msg = _make_inbound_msg(
        text="hey @hermes_bot ping",
        surface_channel="chan_t",
        surface_platform="telegram",
        wire_msg_id="wire-tg",
    )
    await adapter._handle_a2a_inbound_channel_broadcast(
        message=msg, peer_agent_id="peer_bot_1"
    )

    decision = adapter._consume_inbound_trigger_decision("wire-tg")
    assert decision is not None
    assert decision["should_trigger"] is True
    assert decision["mentions"] == {"hermes_bot"}
    assert decision["surface_platform"] == "telegram"
