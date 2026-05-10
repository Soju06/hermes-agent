"""Task 24 — ADR-007 v2 status_adapter swap + force-off fallback.

Tests the helper that decides whether to swap the gateway's `_status_adapter`
to the Discord mirror channel when an A2A inbound message arrives, plus the
force-off behavior when the swap fails (mirror_channel_id missing or Discord
adapter not registered) — without that, GatewayStreamConsumer would post
partials to the A2A adapter and corrupt the JSONRPC reply path.

The swap logic itself lives in gateway/run.py at the inbound chokepoint
(line ~13755). To keep it unit-testable without standing up a full
GatewayRunner, the logic is extracted into a pure helper:

    _resolve_a2a_mirror_swap(source, runner_adapters, fallback_adapter,
                              fallback_chat_id) -> (adapter, chat_id, swapped)

- source.platform == Platform.A2A AND runner has both A2A adapter (with
  populated `_mirror_channel_id`) AND Discord adapter
    → returns (discord_adapter, mirror_chan, True)
- otherwise: returns (fallback_adapter, fallback_chat_id, False)

The companion `_force_off_a2a_streaming_when_no_swap(source, plat_streaming,
swapped) -> plat_streaming` enforces the v2 invariant: A2A inbound + swap
failed → streaming OFF (regardless of user config).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gateway.config import Platform
from gateway.platforms.a2a import A2A_AVAILABLE


pytestmark = pytest.mark.skipif(
    not A2A_AVAILABLE, reason="a2a-sdk not installed (extras: a2a)"
)


# ──────────────────────────────────────────────────────────────────────
# Helpers under test (must exist after Task 24 implementation)
# ──────────────────────────────────────────────────────────────────────

def _import_helpers():
    from gateway.run import (
        _resolve_a2a_mirror_swap,
        _force_off_a2a_streaming_when_no_swap,
    )
    return _resolve_a2a_mirror_swap, _force_off_a2a_streaming_when_no_swap


def _make_source(platform: Platform, chat_id: str = "src-chat") -> SimpleNamespace:
    return SimpleNamespace(platform=platform, chat_id=chat_id)


def _make_a2a_adapter(mirror_channel_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(_mirror_channel_id=mirror_channel_id)


# ──────────────────────────────────────────────────────────────────────
# _resolve_a2a_mirror_swap
# ──────────────────────────────────────────────────────────────────────

def test_swap_happens_when_a2a_inbound_with_mirror_and_discord():
    """A2A inbound + mirror_channel_id 박힘 + Discord adapter → swap."""
    resolve, _ = _import_helpers()
    a2a_adapter = _make_a2a_adapter("1502907302901055679")
    discord_adapter = SimpleNamespace(_kind="discord-spy")
    fallback = SimpleNamespace(_kind="a2a-fallback")
    adapters = {Platform.A2A: a2a_adapter, Platform.DISCORD: discord_adapter}

    adapter, chat_id, swapped = resolve(
        source=_make_source(Platform.A2A),
        runner_adapters=adapters,
        fallback_adapter=fallback,
        fallback_chat_id="src-chat",
    )

    assert swapped is True
    assert adapter is discord_adapter
    assert chat_id == "1502907302901055679"


def test_no_swap_when_mirror_channel_id_missing():
    """A2A inbound + mirror_channel_id None → no swap, fallback returned."""
    resolve, _ = _import_helpers()
    a2a_adapter = _make_a2a_adapter(None)
    discord_adapter = SimpleNamespace(_kind="discord-spy")
    fallback = SimpleNamespace(_kind="a2a-fallback")
    adapters = {Platform.A2A: a2a_adapter, Platform.DISCORD: discord_adapter}

    adapter, chat_id, swapped = resolve(
        source=_make_source(Platform.A2A, chat_id="src-chat"),
        runner_adapters=adapters,
        fallback_adapter=fallback,
        fallback_chat_id="src-chat",
    )

    assert swapped is False
    assert adapter is fallback
    assert chat_id == "src-chat"


def test_no_swap_when_discord_adapter_missing():
    """A2A inbound + mirror_channel_id 박힘 but Discord adapter 없음 → no swap."""
    resolve, _ = _import_helpers()
    a2a_adapter = _make_a2a_adapter("1502907302901055679")
    fallback = SimpleNamespace(_kind="a2a-fallback")
    adapters = {Platform.A2A: a2a_adapter}  # No DISCORD entry

    adapter, chat_id, swapped = resolve(
        source=_make_source(Platform.A2A, chat_id="src-chat"),
        runner_adapters=adapters,
        fallback_adapter=fallback,
        fallback_chat_id="src-chat",
    )

    assert swapped is False
    assert adapter is fallback
    assert chat_id == "src-chat"


def test_no_swap_when_a2a_adapter_missing():
    """A2A inbound but A2A adapter not registered → no swap (defensive)."""
    resolve, _ = _import_helpers()
    discord_adapter = SimpleNamespace(_kind="discord-spy")
    fallback = SimpleNamespace(_kind="a2a-fallback")
    adapters = {Platform.DISCORD: discord_adapter}  # No A2A entry

    adapter, chat_id, swapped = resolve(
        source=_make_source(Platform.A2A, chat_id="src-chat"),
        runner_adapters=adapters,
        fallback_adapter=fallback,
        fallback_chat_id="src-chat",
    )

    assert swapped is False
    assert adapter is fallback
    assert chat_id == "src-chat"


def test_no_swap_for_non_a2a_inbound():
    """Discord/Telegram/etc. inbound → no swap (preserves existing logic)."""
    resolve, _ = _import_helpers()
    a2a_adapter = _make_a2a_adapter("1502907302901055679")
    discord_adapter = SimpleNamespace(_kind="discord-spy")
    fallback = SimpleNamespace(_kind="discord-fallback")
    adapters = {Platform.A2A: a2a_adapter, Platform.DISCORD: discord_adapter}

    for non_a2a in (Platform.DISCORD, Platform.TELEGRAM):
        adapter, chat_id, swapped = resolve(
            source=_make_source(non_a2a, chat_id="src-chat"),
            runner_adapters=adapters,
            fallback_adapter=fallback,
            fallback_chat_id="src-chat",
        )
        assert swapped is False, f"non-A2A {non_a2a} should not swap"
        assert adapter is fallback
        assert chat_id == "src-chat"


# ──────────────────────────────────────────────────────────────────────
# _force_off_a2a_streaming_when_no_swap (ADR-007 v2 Risk B Resolution)
# ──────────────────────────────────────────────────────────────────────

def test_force_off_when_a2a_inbound_and_swap_failed():
    """A2A inbound + swap failed + user config streaming=True → force False."""
    _, force_off = _import_helpers()
    result = force_off(
        source=_make_source(Platform.A2A),
        plat_streaming=True,
        swapped=False,
    )
    assert result is False, "swap-failed A2A inbound must force streaming OFF"


def test_no_force_off_when_a2a_inbound_and_swap_succeeded():
    """A2A inbound + swap succeeded → preserve user config (True stays True)."""
    _, force_off = _import_helpers()
    result = force_off(
        source=_make_source(Platform.A2A),
        plat_streaming=True,
        swapped=True,
    )
    assert result is True


def test_no_force_off_when_a2a_inbound_swap_succeeded_but_user_off():
    """A2A inbound + swap succeeded + user config False → preserve False."""
    _, force_off = _import_helpers()
    result = force_off(
        source=_make_source(Platform.A2A),
        plat_streaming=False,
        swapped=True,
    )
    assert result is False


def test_no_force_off_for_non_a2a_inbound():
    """Discord/Telegram inbound → never force off (preserves existing logic)."""
    _, force_off = _import_helpers()
    for non_a2a in (Platform.DISCORD, Platform.TELEGRAM):
        for streaming in (True, False, None):
            result = force_off(
                source=_make_source(non_a2a),
                plat_streaming=streaming,
                swapped=False,  # swap never happens for non-A2A anyway
            )
            assert result == streaming, (
                f"non-A2A {non_a2a} with streaming={streaming} must be unchanged"
            )


def test_force_off_handles_none_plat_streaming():
    """A2A inbound + swap failed + plat_streaming=None (no override) → False."""
    # None means "follow global config" — but for A2A without swap we MUST
    # block streaming regardless of global config to protect the JSONRPC path.
    _, force_off = _import_helpers()
    result = force_off(
        source=_make_source(Platform.A2A),
        plat_streaming=None,
        swapped=False,
    )
    assert result is False
