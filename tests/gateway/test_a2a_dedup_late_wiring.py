"""Task 18 — Dedup re-wiring after list-form A2A peer resolution (ADR-004).

Verifies the late dedup wiring path:
- _inject_a2a_dedup_into_discord skips list-form peers (waits for connect)
- _wire_dedup_into_discord populates Discord's dedup map after resolve
- Setter hook is called when adapter exposes set_a2a_dedup_peers
- No-op when Discord adapter is None or peers dict is empty
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.config import Platform, PlatformConfig


def _make_a2a_cfg_dict():
    return PlatformConfig(
        enabled=True,
        extra={"peers": {"111": "http://peer-a/", "222": "http://peer-b/"}},
    )


def _make_a2a_cfg_list():
    return PlatformConfig(
        enabled=True, extra={"peers": ["http://peer-a/", "http://peer-b/"]}
    )


def _make_discord_cfg():
    return PlatformConfig(enabled=True, extra={})


def test_inject_dedup_dict_form_works_as_before():
    """Dict-form peers → static injection populates dedup config at load time."""
    from gateway.run import _inject_a2a_dedup_into_discord

    a2a = _make_a2a_cfg_dict()
    disc = _make_discord_cfg()
    platforms = {Platform.A2A: a2a, Platform.DISCORD: disc}

    _inject_a2a_dedup_into_discord(platforms)

    assert disc.extra.get("a2a_dedup_config") == {
        "peers": {"111": "http://peer-a/", "222": "http://peer-b/"}
    }


def test_inject_dedup_list_form_skips():
    """List-form peers → static injection skips (waits for connect-time resolve)."""
    from gateway.run import _inject_a2a_dedup_into_discord

    a2a = _make_a2a_cfg_list()
    disc = _make_discord_cfg()
    platforms = {Platform.A2A: a2a, Platform.DISCORD: disc}

    _inject_a2a_dedup_into_discord(platforms)

    # Skipped — no static dedup config injected for list-form peers.
    assert "a2a_dedup_config" not in disc.extra


def test_wire_dedup_late_populates_after_resolve():
    """After A2AAdapter._peers is populated, _wire_dedup_into_discord injects."""
    from gateway.run import _wire_dedup_into_discord

    a2a_adapter = SimpleNamespace(
        _peers={"333": "http://peer-c/", "444": "http://peer-d/"}
    )
    disc_cfg = _make_discord_cfg()
    discord_adapter = SimpleNamespace(config=disc_cfg)

    _wire_dedup_into_discord(a2a_adapter, discord_adapter)

    assert disc_cfg.extra["a2a_dedup_config"] == {
        "peers": {"333": "http://peer-c/", "444": "http://peer-d/"}
    }


def test_wire_dedup_calls_setter_when_present():
    """If the Discord adapter exposes set_a2a_dedup_peers, it gets called too."""
    from gateway.run import _wire_dedup_into_discord

    a2a_adapter = SimpleNamespace(_peers={"555": "http://peer-e/"})
    disc_cfg = _make_discord_cfg()
    setter_calls = []
    discord_adapter = SimpleNamespace(
        config=disc_cfg,
        set_a2a_dedup_peers=lambda m: setter_calls.append(dict(m)),
    )

    _wire_dedup_into_discord(a2a_adapter, discord_adapter)

    assert disc_cfg.extra["a2a_dedup_config"] == {
        "peers": {"555": "http://peer-e/"}
    }
    assert setter_calls == [{"555": "http://peer-e/"}]


def test_wire_dedup_noop_when_discord_missing():
    """No Discord adapter → silent no-op, no exception."""
    from gateway.run import _wire_dedup_into_discord

    a2a_adapter = SimpleNamespace(_peers={"777": "http://peer-f/"})
    # Should not raise.
    _wire_dedup_into_discord(a2a_adapter, None)


def test_wire_dedup_noop_when_peers_empty():
    """Empty _peers → no dedup config injected."""
    from gateway.run import _wire_dedup_into_discord

    a2a_adapter = SimpleNamespace(_peers={})
    disc_cfg = _make_discord_cfg()
    discord_adapter = SimpleNamespace(config=disc_cfg)

    _wire_dedup_into_discord(a2a_adapter, discord_adapter)

    assert "a2a_dedup_config" not in disc_cfg.extra
