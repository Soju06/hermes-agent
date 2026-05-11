"""ADR-011 v2.1 §6 / Phase 4 Task 38: Reply trigger policy (Q3 directive).

Tests cover:
  - reply_policy config load + 3 mode values (mention_only / autonomous / hybrid)
  - default values: mode='autonomous', max_consecutive=3, cooldown=30s, hints=[]
  - `should_trigger_reply(channel_id, *, user_message, mentioned_user_ids,
    is_user_message)` decision logic per mode
  - max_consecutive_self_replies cap (channel-scoped counter)
  - cooldown_after_silent_decision skip window
  - counter reset semantics:
      * mark_self_reply increments
      * mark_other_inbound (other bot or user) resets the consecutive counter
      * mark_silent_decision starts the cooldown window
  - build_transcript_context returns last N entries with self-flag for the
    LLM history inject (Task 35b will decide where to splice it in)

Pure adapter-side. Gateway-level wiring (Task 35b) is separate.
"""

import time
from typing import Any
from unittest.mock import patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2AAdapter


def _make_adapter(**extra: Any) -> A2AAdapter:
    base = {
        "listen": "127.0.0.1:9999",
        "inbound_handler": "channel_broadcast",
        "discord_bot_user_id": "bot_self",
    }
    base.update(extra)
    return A2AAdapter(PlatformConfig(enabled=True, token="", extra=base))


# ---------------------------------------------------------------------------
# 1. _reply_policy defaults
# ---------------------------------------------------------------------------
def test_reply_policy_defaults():
    """No reply_policy config → mode=autonomous, max_consecutive=3, cooldown=30,
    hints=[]. Matches ADR-011 §6 + plan §Task 38."""
    adapter = _make_adapter()
    assert hasattr(adapter, "_reply_policy")
    p = adapter._reply_policy
    assert p["mode"] == "autonomous"
    assert p["max_consecutive_self_replies"] == 3
    assert p["cooldown_after_silent_decision"] == 30
    assert p["channel_hints"] == []


# ---------------------------------------------------------------------------
# 2. _reply_policy explicit config
# ---------------------------------------------------------------------------
def test_reply_policy_explicit_config():
    adapter = _make_adapter(
        reply_policy={
            "mode": "mention_only",
            "max_consecutive_self_replies": 5,
            "cooldown_after_silent_decision": 90,
            "channel_hints": ["내 turn", "도와줘"],
        }
    )
    p = adapter._reply_policy
    assert p["mode"] == "mention_only"
    assert p["max_consecutive_self_replies"] == 5
    assert p["cooldown_after_silent_decision"] == 90
    assert p["channel_hints"] == ["내 turn", "도와줘"]


# ---------------------------------------------------------------------------
# 3. Invalid mode → ValueError at __init__
# ---------------------------------------------------------------------------
def test_reply_policy_invalid_mode_fatal():
    with pytest.raises(ValueError, match=r"reply_policy.mode|invalid"):
        _make_adapter(reply_policy={"mode": "bogus"})


# ---------------------------------------------------------------------------
# 4. autonomous mode triggers on every user message
# ---------------------------------------------------------------------------
def test_should_trigger_autonomous_user_message():
    adapter = _make_adapter(reply_policy={"mode": "autonomous"})
    assert adapter.should_trigger_reply(
        channel_id="chan_1",
        user_message="hello",
        mentioned_user_ids=set(),
        is_user_message=True,
    ) is True


# ---------------------------------------------------------------------------
# 5. mention_only requires self in mentioned_user_ids
# ---------------------------------------------------------------------------
def test_should_trigger_mention_only_no_mention_skips():
    adapter = _make_adapter(reply_policy={"mode": "mention_only"})
    # No mentions → skip
    assert adapter.should_trigger_reply(
        channel_id="chan_1",
        user_message="hello",
        mentioned_user_ids=set(),
        is_user_message=True,
    ) is False
    # Other bot mentioned → still skip
    assert adapter.should_trigger_reply(
        channel_id="chan_1",
        user_message="hey bot_other",
        mentioned_user_ids={"bot_other"},
        is_user_message=True,
    ) is False
    # Self mentioned → trigger
    assert adapter.should_trigger_reply(
        channel_id="chan_1",
        user_message="hey bot_self",
        mentioned_user_ids={"bot_self"},
        is_user_message=True,
    ) is True


# ---------------------------------------------------------------------------
# 6. hybrid mode — mention OR channel hint
# ---------------------------------------------------------------------------
def test_should_trigger_hybrid_mention_or_hint():
    adapter = _make_adapter(
        reply_policy={"mode": "hybrid", "channel_hints": ["내 turn", r"\bhelp\b"]}
    )
    # Mention → trigger
    assert adapter.should_trigger_reply(
        channel_id="chan_1",
        user_message="hello",
        mentioned_user_ids={"bot_self"},
        is_user_message=True,
    ) is True
    # Hint regex match → trigger
    assert adapter.should_trigger_reply(
        channel_id="chan_1",
        user_message="이건 내 turn 인가?",
        mentioned_user_ids=set(),
        is_user_message=True,
    ) is True
    assert adapter.should_trigger_reply(
        channel_id="chan_1",
        user_message="anyone, help me out",
        mentioned_user_ids=set(),
        is_user_message=True,
    ) is True
    # Neither → skip
    assert adapter.should_trigger_reply(
        channel_id="chan_1",
        user_message="weather is nice",
        mentioned_user_ids=set(),
        is_user_message=True,
    ) is False


# ---------------------------------------------------------------------------
# 7. max_consecutive_self_replies caps autonomous chain
# ---------------------------------------------------------------------------
def test_max_consecutive_self_replies_caps_chain():
    """autonomous mode triggers replies, but after `max_consecutive_self_replies`
    in a row without anyone else speaking, should_trigger_reply returns False
    until the counter is reset (by mark_other_inbound or user message)."""
    adapter = _make_adapter(
        reply_policy={"mode": "autonomous", "max_consecutive_self_replies": 2}
    )

    # 1st reply allowed
    assert adapter.should_trigger_reply(
        channel_id="chan_1", user_message="m1", mentioned_user_ids=set(),
        is_user_message=False,  # not a user msg — peer broadcast triggered consideration
    ) is True
    adapter.mark_self_reply("chan_1")

    # 2nd allowed
    assert adapter.should_trigger_reply(
        channel_id="chan_1", user_message="m2", mentioned_user_ids=set(),
        is_user_message=False,
    ) is True
    adapter.mark_self_reply("chan_1")

    # 3rd capped (max=2 → 2nd was the last allowed)
    assert adapter.should_trigger_reply(
        channel_id="chan_1", user_message="m3", mentioned_user_ids=set(),
        is_user_message=False,
    ) is False


# ---------------------------------------------------------------------------
# 8. mark_other_inbound resets the consecutive counter
# ---------------------------------------------------------------------------
def test_mark_other_inbound_resets_counter():
    adapter = _make_adapter(
        reply_policy={"mode": "autonomous", "max_consecutive_self_replies": 2}
    )
    adapter.mark_self_reply("chan_1")
    adapter.mark_self_reply("chan_1")
    # capped now
    assert adapter.should_trigger_reply(
        channel_id="chan_1", user_message="x", mentioned_user_ids=set(),
        is_user_message=False,
    ) is False
    # Another bot speaks → reset
    adapter.mark_other_inbound("chan_1")
    assert adapter.should_trigger_reply(
        channel_id="chan_1", user_message="x", mentioned_user_ids=set(),
        is_user_message=False,
    ) is True


# ---------------------------------------------------------------------------
# 9. cooldown_after_silent_decision blocks within window
# ---------------------------------------------------------------------------
def test_cooldown_after_silent_decision_blocks_then_lifts():
    adapter = _make_adapter(
        reply_policy={"mode": "autonomous", "cooldown_after_silent_decision": 30}
    )
    t0 = 1_000_000.0

    # User msg → would normally trigger, but we mark a silent decision first
    with patch("gateway.platforms.a2a.time.time", return_value=t0):
        adapter.mark_silent_decision("chan_1")

    # 10s later → still in 30s cooldown → skip
    with patch("gateway.platforms.a2a.time.time", return_value=t0 + 10):
        assert adapter.should_trigger_reply(
            channel_id="chan_1", user_message="hi", mentioned_user_ids=set(),
            is_user_message=True,
        ) is False

    # 45s later → cooldown lifted → trigger again
    with patch("gateway.platforms.a2a.time.time", return_value=t0 + 45):
        assert adapter.should_trigger_reply(
            channel_id="chan_1", user_message="hi", mentioned_user_ids=set(),
            is_user_message=True,
        ) is True


# ---------------------------------------------------------------------------
# 10. cooldown does NOT block when caller is mentioned (mention overrides)
# ---------------------------------------------------------------------------
def test_cooldown_does_not_block_explicit_mention():
    """If the user explicitly mentions the bot during the cooldown window, we
    still trigger — explicit mention is a stronger signal than the bot's
    silent self-evaluation. This matches the hermes-agent reply policy convention
    where direct address overrides quiet-mode."""
    adapter = _make_adapter(
        reply_policy={"mode": "autonomous", "cooldown_after_silent_decision": 30}
    )
    t0 = 1_000_000.0
    with patch("gateway.platforms.a2a.time.time", return_value=t0):
        adapter.mark_silent_decision("chan_1")
    with patch("gateway.platforms.a2a.time.time", return_value=t0 + 10):
        assert adapter.should_trigger_reply(
            channel_id="chan_1",
            user_message="@bot_self help",
            mentioned_user_ids={"bot_self"},
            is_user_message=True,
        ) is True


# ---------------------------------------------------------------------------
# 11. build_transcript_context returns last N entries with self-flag
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_build_transcript_context_returns_entries_with_self_flag():
    """Helper for Task 35b LLM history inject. Returns last N TranscriptEntry
    dicts plus a derived `is_self` flag so the splice site can decide whether
    to use 'assistant' or 'user' role per entry."""
    adapter = _make_adapter()
    # Append two entries: one from self (echo would be skipped by Task 39, but
    # build mainly walks the deque — populate directly)
    from collections import deque
    adapter._channel_transcripts["chan_1"] = deque(maxlen=100)
    adapter._channel_transcripts["chan_1"].append(
        {
            "sender_bot_user_id": "bot_other",
            "text": "hi from other",
            "timestamp": 1.0,
            "message_id": "m1",
            "surface_message_id": "s1",
            "surface_channel_id": "chan_1",
        }
    )
    adapter._channel_transcripts["chan_1"].append(
        {
            "sender_bot_user_id": "bot_self",
            "text": "hi from me",
            "timestamp": 2.0,
            "message_id": "m2",
            "surface_message_id": "s2",
            "surface_channel_id": "chan_1",
        }
    )

    ctx = adapter.build_transcript_context("chan_1", max_entries=10)
    assert isinstance(ctx, list)
    assert len(ctx) == 2
    assert ctx[0]["sender_bot_user_id"] == "bot_other"
    assert ctx[0]["is_self"] is False
    assert ctx[1]["sender_bot_user_id"] == "bot_self"
    assert ctx[1]["is_self"] is True


# ---------------------------------------------------------------------------
# 12. build_transcript_context max_entries trims oldest first
# ---------------------------------------------------------------------------
def test_build_transcript_context_max_entries_keeps_newest():
    adapter = _make_adapter()
    from collections import deque
    adapter._channel_transcripts["chan_1"] = deque(maxlen=100)
    for i in range(5):
        adapter._channel_transcripts["chan_1"].append(
            {
                "sender_bot_user_id": "bot_other",
                "text": f"msg {i}",
                "timestamp": float(i),
                "message_id": f"m{i}",
                "surface_message_id": f"s{i}",
                "surface_channel_id": "chan_1",
            }
        )
    ctx = adapter.build_transcript_context("chan_1", max_entries=2)
    assert [e["text"] for e in ctx] == ["msg 3", "msg 4"]


# ---------------------------------------------------------------------------
# 13. Non-user-message (peer broadcast) + mention_only mode → never triggers
# ---------------------------------------------------------------------------
def test_mention_only_ignores_peer_broadcasts():
    """mention_only mode only reacts to user messages — peer A2A broadcasts
    never trigger (otherwise other bots could mention us indirectly)."""
    adapter = _make_adapter(reply_policy={"mode": "mention_only"})
    assert adapter.should_trigger_reply(
        channel_id="chan_1",
        user_message="peer's reply text mentions @bot_self",
        mentioned_user_ids={"bot_self"},
        is_user_message=False,
    ) is False
