"""Task 20 — Discord-side reset hook for ADR-006 turn counter.

Tests `DiscordAdapter._maybe_reset_a2a_turn_counters`:
- Skips bot messages (only humans reset).
- Skips when message channel ≠ A2A mirror_channel_id.
- Skips when A2A adapter not registered.
- Calls A2A._reset_turn_counters_for_chat for every chat_id in counters
  when human posts in mirror channel.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gateway.config import Platform


def _make_discord_adapter():
    """Build a stripped-down Discord adapter just for the reset method.

    DiscordAdapter requires discord.py at import time but only for connect().
    We can construct it via PlatformConfig and call the reset method without
    ever connecting.
    """
    from gateway.platforms.discord import DiscordAdapter, DISCORD_AVAILABLE
    if not DISCORD_AVAILABLE:
        pytest.skip("discord.py not installed")
    from gateway.config import PlatformConfig
    return DiscordAdapter(PlatformConfig(enabled=True, extra={}))


def _install_runner_with_a2a(monkeypatch, *, mirror_chan: str | None,
                              counters: dict[tuple[str, str], int]):
    """Stub `_gateway_runner_ref` to return a fake runner exposing an A2A
    adapter with the given mirror channel + counters.
    Returns the `reset_log` list that records every reset call."""
    reset_log: list[str] = []

    def _reset(chat_id: str) -> int:
        reset_log.append(chat_id)
        # Mimic A2AAdapter behavior: drop matching keys.
        keys = [k for k in counters if k[1] == chat_id]
        for k in keys:
            counters.pop(k, None)
        return len(keys)

    fake_a2a = SimpleNamespace(
        _mirror_channel_id=mirror_chan,
        _turn_counters=counters,
        _reset_turn_counters_for_chat=_reset,
    )
    fake_runner = SimpleNamespace(adapters={Platform.A2A: fake_a2a})
    monkeypatch.setattr(
        "gateway.run._gateway_runner_ref", lambda: fake_runner
    )
    return reset_log


def _make_msg(*, chan_id: str, author_is_bot: bool):
    return SimpleNamespace(
        channel=SimpleNamespace(id=chan_id),
        author=SimpleNamespace(bot=author_is_bot, id=42),
    )


def test_skip_bot_authored_message(monkeypatch):
    """Bot authors don't trigger reset (only humans do)."""
    adapter = _make_discord_adapter()
    counters = {("peer", "ctx-1"): 5}
    reset_log = _install_runner_with_a2a(
        monkeypatch, mirror_chan="m-chan", counters=counters
    )

    adapter._maybe_reset_a2a_turn_counters(
        _make_msg(chan_id="m-chan", author_is_bot=True)
    )

    assert reset_log == []
    assert counters == {("peer", "ctx-1"): 5}


def test_skip_when_channel_not_mirror(monkeypatch):
    """Human in some-other-channel doesn't reset A2A counters."""
    adapter = _make_discord_adapter()
    counters = {("peer", "ctx-1"): 5}
    reset_log = _install_runner_with_a2a(
        monkeypatch, mirror_chan="m-chan", counters=counters
    )

    adapter._maybe_reset_a2a_turn_counters(
        _make_msg(chan_id="other-chan", author_is_bot=False)
    )

    assert reset_log == []
    assert counters == {("peer", "ctx-1"): 5}


def test_skip_when_no_mirror_configured(monkeypatch):
    """A2A without mirror_channel_id → nothing to match; skip."""
    adapter = _make_discord_adapter()
    counters = {("peer", "ctx-1"): 5}
    reset_log = _install_runner_with_a2a(
        monkeypatch, mirror_chan=None, counters=counters
    )

    adapter._maybe_reset_a2a_turn_counters(
        _make_msg(chan_id="any", author_is_bot=False)
    )

    assert reset_log == []


def test_skip_when_no_runner(monkeypatch):
    """No gateway runner → silent skip."""
    adapter = _make_discord_adapter()
    monkeypatch.setattr("gateway.run._gateway_runner_ref", lambda: None)
    # Should not raise.
    adapter._maybe_reset_a2a_turn_counters(
        _make_msg(chan_id="any", author_is_bot=False)
    )


def test_skip_when_no_a2a_adapter(monkeypatch):
    """A2A adapter not registered → silent skip."""
    adapter = _make_discord_adapter()
    fake_runner = SimpleNamespace(adapters={})  # no A2A
    monkeypatch.setattr(
        "gateway.run._gateway_runner_ref", lambda: fake_runner
    )
    adapter._maybe_reset_a2a_turn_counters(
        _make_msg(chan_id="any", author_is_bot=False)
    )


def test_human_in_mirror_channel_resets_all_chats(monkeypatch):
    """Human speaks in mirror channel → _reset_turn_counters_for_chat called
    for every distinct chat_id in counters."""
    adapter = _make_discord_adapter()
    counters = {
        ("peer-A", "ctx-1"): 3,
        ("peer-B", "ctx-1"): 2,
        ("peer-A", "ctx-2"): 4,
    }
    reset_log = _install_runner_with_a2a(
        monkeypatch, mirror_chan="m-chan", counters=counters
    )

    adapter._maybe_reset_a2a_turn_counters(
        _make_msg(chan_id="m-chan", author_is_bot=False)
    )

    assert sorted(reset_log) == ["ctx-1", "ctx-2"]
    assert counters == {}


def test_chan_id_string_coercion(monkeypatch):
    """Discord channel.id is int; mirror_channel_id is str — both must compare."""
    adapter = _make_discord_adapter()
    counters = {("p", "1234567890"): 2}
    reset_log = _install_runner_with_a2a(
        monkeypatch, mirror_chan="1234567890", counters=counters
    )

    msg = SimpleNamespace(
        channel=SimpleNamespace(id=1234567890),  # int from Discord
        author=SimpleNamespace(bot=False, id=99),
    )
    adapter._maybe_reset_a2a_turn_counters(msg)

    assert reset_log == ["1234567890"]
