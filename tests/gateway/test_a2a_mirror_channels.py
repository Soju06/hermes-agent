"""ADR-008 (Phase 3a): Per-peer mirror routing tests.

Tests the lookup priority in `_resolve_a2a_mirror_swap`:
  1. mirror_channels[source.user_id]      — peer match
  2. mirror_channels["default"]            — default fallback
  3. mirror_channel_id (legacy, single)    — Phase 2.5 backwards-compat
  4. None → swap=False                     — force-off via Phase 2.5 helper

These are pure-helper tests — no full GatewayRunner stand-up.
"""

from unittest.mock import MagicMock

from gateway.config import Platform
from gateway.run import _resolve_a2a_mirror_swap


class _Source:
    """Minimal stand-in for SessionSource."""

    def __init__(self, platform, user_id):
        self.platform = platform
        self.user_id = user_id


class _Adapter:
    """Minimal stand-in for A2A adapter exposing the two mirror fields."""

    def __init__(self, mirror_channel_id=None, mirror_channels=None):
        self._mirror_channel_id = mirror_channel_id
        self._mirror_channels = dict(mirror_channels or {})


def test_per_peer_channel_picks_specific():
    """ADR-008: peer_user_id key가 mirror_channels에 박혀있으면 그 channel."""
    source = _Source(Platform.A2A, "peer_alpha")
    a2a = _Adapter(
        mirror_channel_id="legacy_chan",
        mirror_channels={"peer_alpha": "alpha_chan", "peer_beta": "beta_chan"},
    )
    discord = MagicMock()
    adapters = {Platform.A2A: a2a, Platform.DISCORD: discord}

    out_adapter, out_chan, swapped = _resolve_a2a_mirror_swap(
        source=source,
        runner_adapters=adapters,
        fallback_adapter=None,
        fallback_chat_id="",
    )
    assert swapped is True
    assert out_adapter is discord
    assert out_chan == "alpha_chan"


def test_default_channel_fallback():
    """peer 키 없으면 mirror_channels['default']."""
    source = _Source(Platform.A2A, "unknown_peer")
    a2a = _Adapter(
        mirror_channel_id="legacy_chan",
        mirror_channels={"peer_alpha": "alpha_chan", "default": "default_chan"},
    )
    discord = MagicMock()
    adapters = {Platform.A2A: a2a, Platform.DISCORD: discord}

    _, out_chan, swapped = _resolve_a2a_mirror_swap(
        source=source,
        runner_adapters=adapters,
        fallback_adapter=None,
        fallback_chat_id="",
    )
    assert swapped is True
    assert out_chan == "default_chan"


def test_legacy_mirror_channel_id_fallback():
    """mirror_channels 비어있으면 mirror_channel_id (Phase 2.5 backwards-compat)."""
    source = _Source(Platform.A2A, "peer_alpha")
    a2a = _Adapter(mirror_channel_id="legacy_chan", mirror_channels={})
    discord = MagicMock()
    adapters = {Platform.A2A: a2a, Platform.DISCORD: discord}

    _, out_chan, swapped = _resolve_a2a_mirror_swap(
        source=source,
        runner_adapters=adapters,
        fallback_adapter=None,
        fallback_chat_id="",
    )
    assert swapped is True
    assert out_chan == "legacy_chan"


def test_no_swap_when_all_channels_missing():
    """mirror_channels + mirror_channel_id 둘 다 없으면 swap=False."""
    source = _Source(Platform.A2A, "peer_alpha")
    a2a = _Adapter(mirror_channel_id=None, mirror_channels={})
    discord = MagicMock()
    adapters = {Platform.A2A: a2a, Platform.DISCORD: discord}

    out_adapter, out_chan, swapped = _resolve_a2a_mirror_swap(
        source=source,
        runner_adapters=adapters,
        fallback_adapter="fb_adapter",
        fallback_chat_id="fb_chat",
    )
    assert swapped is False
    assert out_adapter == "fb_adapter"
    assert out_chan == "fb_chat"


def test_per_peer_takes_priority_over_default():
    """peer 매치 + default 둘 다 있으면 peer 매치가 우선."""
    source = _Source(Platform.A2A, "peer_alpha")
    a2a = _Adapter(
        mirror_channels={"peer_alpha": "alpha_chan", "default": "default_chan"},
    )
    discord = MagicMock()
    adapters = {Platform.A2A: a2a, Platform.DISCORD: discord}

    _, out_chan, _swapped = _resolve_a2a_mirror_swap(
        source=source,
        runner_adapters=adapters,
        fallback_adapter=None,
        fallback_chat_id="",
    )
    assert out_chan == "alpha_chan"


def test_default_takes_priority_over_legacy():
    """mirror_channels['default'] + mirror_channel_id 둘 다 있으면 default가 우선."""
    source = _Source(Platform.A2A, "unknown_peer")
    a2a = _Adapter(
        mirror_channel_id="legacy_chan",
        mirror_channels={"default": "default_chan"},
    )
    discord = MagicMock()
    adapters = {Platform.A2A: a2a, Platform.DISCORD: discord}

    _, out_chan, _swapped = _resolve_a2a_mirror_swap(
        source=source,
        runner_adapters=adapters,
        fallback_adapter=None,
        fallback_chat_id="",
    )
    assert out_chan == "default_chan"
