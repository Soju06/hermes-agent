"""Task 20 — ADR-006 multi-turn termination via turn counter.

Tests the A2AAdapter's `_turn_counters` enforcement in `_wrapped`:

- Per-(peer_id, context_id) cap at `max_turns_per_conversation` (default 5).
- 6th inbound for the same key is dropped:
    - real handler is NOT invoked (no LLM call, no cost)
    - capture callback fires with a turn-limit notice (so A2A caller
      doesn't hang for 60s waiting for the executor reply)
    - mirror to Discord is NOT called (silent on the human-visible side)
- Counter is per-pair: different peer or different chat doesn't share count.
- Reset on human message: when a non-bot message hits the mirror channel,
  all counters keyed by that channel/context are cleared.
- Turn limit is config-overridable.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.a2a import A2A_AVAILABLE, A2AAdapter


pytestmark = pytest.mark.skipif(
    not A2A_AVAILABLE, reason="a2a-sdk not installed (extras: a2a)"
)


def _make_adapter(extra: dict | None = None) -> A2AAdapter:
    return A2AAdapter(PlatformConfig(enabled=True, extra=extra or {}))


class _SpyDiscord:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def send(self, chat_id: str, content: str, **kwargs: Any) -> Any:
        self.calls.append((chat_id, content))
        return SimpleNamespace(success=True)


def _install_runner(monkeypatch, discord_adapter: Any | None) -> None:
    fake_runner = SimpleNamespace(adapters={Platform.DISCORD: discord_adapter})
    monkeypatch.setattr(
        "gateway.run._gateway_runner_ref",
        lambda: fake_runner if discord_adapter is not None else None,
    )


def _make_event(*, peer_id: str, context_id: str, message_id: str, text: str = "ping"):
    """Forge a MessageEvent-shaped object (only fields _wrapped reads)."""
    source = SimpleNamespace(user_id=peer_id, chat_id=context_id)
    return SimpleNamespace(
        text=text,
        message_id=message_id,
        source=source,
    )


# ──────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────


def test_init_default_max_turns_is_5():
    a = _make_adapter()
    assert a._max_turns_per_conversation == 5
    assert a._turn_counters == {}


def test_init_max_turns_overridable():
    a = _make_adapter({"max_turns_per_conversation": 12})
    assert a._max_turns_per_conversation == 12


# ──────────────────────────────────────────────────────────────────────
# Turn counter enforcement (no Discord mirror to avoid noise)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_under_limit_passes_through(monkeypatch):
    """5 inbounds (== limit) all reach the real handler."""
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)
    a = _make_adapter({"max_turns_per_conversation": 5})

    handler_calls: list[str] = []

    async def real_handler(event):
        handler_calls.append(event.text)
        return f"reply-{event.text}"

    a.set_message_handler(real_handler)
    wrapped = a._message_handler

    captured: list[str] = []

    async def cap_cb(text: str) -> None:
        captured.append(text)

    for i in range(5):
        msg_id = f"msg-{i}"
        a._post_response_callbacks[msg_id] = cap_cb
        ev = _make_event(peer_id="peer-A", context_id="ctx-1", message_id=msg_id, text=f"q{i}")
        await wrapped(ev)

    assert handler_calls == [f"q{i}" for i in range(5)]
    assert captured == [f"reply-q{i}" for i in range(5)]
    # Counter advanced exactly 5.
    assert a._turn_counters[("peer-A", "ctx-1")] == 5


@pytest.mark.asyncio
async def test_at_limit_drops_inbound_no_handler_call(monkeypatch):
    """6th inbound: handler NOT called, capture cb fires with limit notice."""
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)
    a = _make_adapter({"max_turns_per_conversation": 3})  # tighten for speed

    handler_calls = []

    async def real_handler(event):
        handler_calls.append(event.text)
        return f"reply-{event.text}"

    a.set_message_handler(real_handler)
    wrapped = a._message_handler

    captured: list[str] = []

    async def cap_cb(text: str) -> None:
        captured.append(text)

    # First 3 pass.
    for i in range(3):
        msg_id = f"msg-{i}"
        a._post_response_callbacks[msg_id] = cap_cb
        await wrapped(_make_event(peer_id="P", context_id="C", message_id=msg_id))

    # 4th must drop.
    a._post_response_callbacks["msg-overflow"] = cap_cb
    await wrapped(_make_event(peer_id="P", context_id="C", message_id="msg-overflow"))

    # Handler ran exactly 3 times — not 4.
    assert len(handler_calls) == 3
    # Capture cb fired 4 times: 3 normal + 1 limit notice.
    assert len(captured) == 4
    assert "turn limit" in captured[-1].lower()


@pytest.mark.asyncio
async def test_drop_does_not_mirror_to_discord(monkeypatch):
    """Limit-exceeded drops do NOT post to the Discord mirror channel."""
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)
    a = _make_adapter(
        {
            "max_turns_per_conversation": 2,
            "mirror_channel_id": "chan-mirror",
            # Crank rate-limit to ~0 so legitimate mirrors fire fast.
            "min_dual_send_interval_seconds": 0.001,
        }
    )

    async def real_handler(event):
        return f"reply-{event.text}"

    a.set_message_handler(real_handler)
    wrapped = a._message_handler

    async def cap_cb(text: str) -> None:
        pass

    for i in range(2):
        a._post_response_callbacks[f"m{i}"] = cap_cb
        await wrapped(_make_event(peer_id="P", context_id="C", message_id=f"m{i}"))

    # Mirror called twice (legitimate replies).
    assert len(spy.calls) == 2

    # 3rd inbound: dropped; mirror count must not increase.
    a._post_response_callbacks["m-drop"] = cap_cb
    await wrapped(_make_event(peer_id="P", context_id="C", message_id="m-drop"))

    assert len(spy.calls) == 2  # unchanged — drop was silent on Discord side


@pytest.mark.asyncio
async def test_counter_per_peer_chat_pair(monkeypatch):
    """(peer_id, context_id) pair is the cap key — different peer/chat is independent."""
    _install_runner(monkeypatch, _SpyDiscord())
    a = _make_adapter({"max_turns_per_conversation": 2})

    handler_calls = []

    async def real_handler(event):
        handler_calls.append((event.source.user_id, event.source.chat_id))
        return "ok"

    a.set_message_handler(real_handler)
    wrapped = a._message_handler

    async def cap_cb(text: str) -> None:
        pass

    # Fill quota for (A, X).
    for i in range(2):
        a._post_response_callbacks[f"a-{i}"] = cap_cb
        await wrapped(_make_event(peer_id="A", context_id="X", message_id=f"a-{i}"))

    # (B, X) — different peer, fresh quota.
    a._post_response_callbacks["b-0"] = cap_cb
    await wrapped(_make_event(peer_id="B", context_id="X", message_id="b-0"))

    # (A, Y) — same peer different chat, fresh quota.
    a._post_response_callbacks["a-y-0"] = cap_cb
    await wrapped(_make_event(peer_id="A", context_id="Y", message_id="a-y-0"))

    # 3rd to (A, X) drops.
    a._post_response_callbacks["a-drop"] = cap_cb
    await wrapped(_make_event(peer_id="A", context_id="X", message_id="a-drop"))

    # Handler saw: 2 from (A,X) + 1 from (B,X) + 1 from (A,Y) = 4 total.
    assert len(handler_calls) == 4
    assert a._turn_counters[("A", "X")] == 2  # capped
    assert a._turn_counters[("B", "X")] == 1
    assert a._turn_counters[("A", "Y")] == 1


# ──────────────────────────────────────────────────────────────────────
# Reset hook
# ──────────────────────────────────────────────────────────────────────


def test_reset_clears_counters_for_chat():
    """Calling _reset_turn_counters_for_chat clears keys with matching chat_id."""
    a = _make_adapter()
    a._turn_counters[("peer-A", "ctx-1")] = 5
    a._turn_counters[("peer-B", "ctx-1")] = 3
    a._turn_counters[("peer-A", "ctx-2")] = 4  # different chat

    cleared = a._reset_turn_counters_for_chat("ctx-1")

    assert cleared == 2
    assert ("peer-A", "ctx-1") not in a._turn_counters
    assert ("peer-B", "ctx-1") not in a._turn_counters
    assert a._turn_counters[("peer-A", "ctx-2")] == 4  # untouched


def test_reset_noop_for_unknown_chat():
    a = _make_adapter()
    a._turn_counters[("p", "c1")] = 2
    cleared = a._reset_turn_counters_for_chat("c-other")
    assert cleared == 0
    assert a._turn_counters[("p", "c1")] == 2


@pytest.mark.asyncio
async def test_after_reset_quota_refills(monkeypatch):
    """After reset, a fresh `max_turns` worth of inbounds is allowed."""
    _install_runner(monkeypatch, _SpyDiscord())
    a = _make_adapter({"max_turns_per_conversation": 2})

    async def real_handler(event):
        return "ok"

    a.set_message_handler(real_handler)
    wrapped = a._message_handler

    async def cap_cb(text: str) -> None:
        pass

    for i in range(2):
        a._post_response_callbacks[f"x-{i}"] = cap_cb
        await wrapped(_make_event(peer_id="P", context_id="C", message_id=f"x-{i}"))
    assert a._turn_counters[("P", "C")] == 2

    # Reset.
    a._reset_turn_counters_for_chat("C")

    # Fresh quota: 2 more must pass.
    handler_calls = []
    async def real_handler2(event):
        handler_calls.append(event.message_id)
        return "ok"
    a.set_message_handler(real_handler2)
    wrapped = a._message_handler

    for i in range(2):
        a._post_response_callbacks[f"y-{i}"] = cap_cb
        await wrapped(_make_event(peer_id="P", context_id="C", message_id=f"y-{i}"))

    assert handler_calls == ["y-0", "y-1"]
    assert a._turn_counters[("P", "C")] == 2
