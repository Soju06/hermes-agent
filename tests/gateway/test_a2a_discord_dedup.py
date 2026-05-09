"""Task 10 — Discord ↔ A2A dedup.

Two units under test:

1. ``_inject_a2a_dedup_into_discord`` (in ``gateway/run.py``) — copies
   the A2A peers map into Discord's ``config.extra["a2a_dedup_config"]``
   *before* either adapter is built, so the wiring is independent of
   dict-iteration order during the platform connect loop.

2. ``DiscordAdapter._is_a2a_peer_echo`` — pure check that returns True
   iff the inbound author_id is a registered A2A peer.

We avoid spinning up a real ``DiscordAdapter`` (constructor pulls in
``discord.py`` and is heavy); the test fakes the bare attributes the
helper actually touches (``self.config.extra`` and ``self.name``) via a
SimpleNamespace, then calls the unbound method.
"""

from __future__ import annotations

from types import SimpleNamespace

from gateway.config import Platform, PlatformConfig
from gateway.run import _inject_a2a_dedup_into_discord


# ---------------------------------------------------------------------------
# _inject_a2a_dedup_into_discord
# ---------------------------------------------------------------------------


def _platforms(*, a2a: PlatformConfig | None, discord: PlatformConfig | None) -> dict:
    out: dict = {}
    if a2a is not None:
        out[Platform.A2A] = a2a
    if discord is not None:
        out[Platform.DISCORD] = discord
    return out


def test_inject_copies_peers_when_both_enabled():
    a2a = PlatformConfig(
        enabled=True,
        extra={"peers": {"1234567890": "http://10.0.0.1:8765/"}},
    )
    discord = PlatformConfig(enabled=True, extra={})

    _inject_a2a_dedup_into_discord(_platforms(a2a=a2a, discord=discord))

    assert discord.extra["a2a_dedup_config"] == {
        "peers": {"1234567890": "http://10.0.0.1:8765/"}
    }


def test_inject_noop_when_a2a_disabled():
    a2a = PlatformConfig(
        enabled=False,
        extra={"peers": {"1234567890": "http://10.0.0.1:8765/"}},
    )
    discord = PlatformConfig(enabled=True, extra={})

    _inject_a2a_dedup_into_discord(_platforms(a2a=a2a, discord=discord))

    assert "a2a_dedup_config" not in discord.extra


def test_inject_noop_when_discord_disabled():
    a2a = PlatformConfig(
        enabled=True,
        extra={"peers": {"1234567890": "http://x/"}},
    )
    discord = PlatformConfig(enabled=False, extra={})

    _inject_a2a_dedup_into_discord(_platforms(a2a=a2a, discord=discord))

    assert "a2a_dedup_config" not in discord.extra


def test_inject_noop_when_no_peers():
    a2a = PlatformConfig(enabled=True, extra={"peers": {}})
    discord = PlatformConfig(enabled=True, extra={})

    _inject_a2a_dedup_into_discord(_platforms(a2a=a2a, discord=discord))

    assert "a2a_dedup_config" not in discord.extra


def test_inject_noop_when_a2a_missing_entirely():
    discord = PlatformConfig(enabled=True, extra={})

    _inject_a2a_dedup_into_discord(_platforms(a2a=None, discord=discord))

    assert "a2a_dedup_config" not in discord.extra


def test_inject_idempotent_overwrite():
    """Re-running injection refreshes (not appends) the snapshot."""
    a2a_v1 = PlatformConfig(
        enabled=True, extra={"peers": {"1": "http://a/"}}
    )
    a2a_v2 = PlatformConfig(
        enabled=True, extra={"peers": {"2": "http://b/"}}
    )
    discord = PlatformConfig(enabled=True, extra={})

    _inject_a2a_dedup_into_discord(_platforms(a2a=a2a_v1, discord=discord))
    _inject_a2a_dedup_into_discord(_platforms(a2a=a2a_v2, discord=discord))

    assert discord.extra["a2a_dedup_config"] == {"peers": {"2": "http://b/"}}


def test_inject_deep_copies_peers_dict():
    """Mutating the original A2A peers after injection must not affect
    Discord's snapshot — guards against shared-reference foot-guns."""
    original = {"1234567890": "http://10.0.0.1:8765/"}
    a2a = PlatformConfig(enabled=True, extra={"peers": original})
    discord = PlatformConfig(enabled=True, extra={})

    _inject_a2a_dedup_into_discord(_platforms(a2a=a2a, discord=discord))

    original["new"] = "http://newly-added/"
    assert "new" not in discord.extra["a2a_dedup_config"]["peers"]


# ---------------------------------------------------------------------------
# DiscordAdapter._is_a2a_peer_echo
# ---------------------------------------------------------------------------


def _fake_adapter(extra: dict) -> SimpleNamespace:
    """Stand-in for DiscordAdapter — only the attributes the helper reads."""
    cfg = SimpleNamespace(extra=extra)
    return SimpleNamespace(config=cfg, name="discord-test")


def _call_is_a2a_peer_echo(adapter, author_id) -> bool:
    """Invoke the unbound ``_is_a2a_peer_echo`` against our fake adapter.

    We intentionally don't import DiscordAdapter at module load (saves
    ~discord.py import cost in the rest of the suite) — instead we lazy-
    import inside the call so collection stays cheap.
    """
    from gateway.platforms.discord import DiscordAdapter
    return DiscordAdapter._is_a2a_peer_echo(adapter, author_id)


def test_is_a2a_peer_echo_true_for_registered_peer():
    adapter = _fake_adapter({
        "a2a_dedup_config": {"peers": {"1234567890": "http://x/"}},
    })
    assert _call_is_a2a_peer_echo(adapter, "1234567890") is True


def test_is_a2a_peer_echo_false_for_unregistered_id():
    adapter = _fake_adapter({
        "a2a_dedup_config": {"peers": {"1234567890": "http://x/"}},
    })
    assert _call_is_a2a_peer_echo(adapter, "9999") is False


def test_is_a2a_peer_echo_false_when_no_dedup_config():
    """Adapter with no a2a_dedup_config injection — every author passes."""
    adapter = _fake_adapter({})
    assert _call_is_a2a_peer_echo(adapter, "1234567890") is False


def test_is_a2a_peer_echo_accepts_int_author_id():
    """Discord hands int IDs from the wire; the resolver coerces."""
    adapter = _fake_adapter({
        "a2a_dedup_config": {"peers": {"1234567890": "http://x/"}},
    })
    assert _call_is_a2a_peer_echo(adapter, 1234567890) is True
