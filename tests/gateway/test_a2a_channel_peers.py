"""ADR-011 v2.1 / Phase 4 Task 34: `_channel_peers` data structure + static config load.

Tests that:
  - `display.platforms.a2a.channel_peers` dict 로드되어 `self._channel_peers`에 박힘
  - `_channel_peers` 비어 있을 때 default = `{}` (no-op friendly)
  - `_peer_cards` field 박혀 있음 (Task 40 verify chain용 — Phase 4 scope에선 empty default)
  - `_resolve_well_known_peers`가 successfully resolved peer의 AgentCard을 `_peer_cards`에 저장
  - Channel id 없는 peer_id가 박혀 있어도 graceful (no exception)

These are pure adapter-init / fetch-helper tests — no full GatewayRunner stand-up.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2AAdapter


def _make_config(**extra: Any) -> PlatformConfig:
    """Minimal PlatformConfig for adapter tests."""
    return PlatformConfig(
        enabled=True,
        token="",
        extra=extra,
    )


def test_channel_peers_default_empty():
    """ADR-011: channel_peers config 없으면 self._channel_peers = {} (default)."""
    config = _make_config(listen="127.0.0.1:9999")
    adapter = A2AAdapter(config)
    assert hasattr(adapter, "_channel_peers")
    assert adapter._channel_peers == {}


def test_channel_peers_loaded_from_config():
    """ADR-011: channel_peers dict 박힌 config가 _channel_peers에 정확히 박힘."""
    config = _make_config(
        listen="127.0.0.1:9999",
        channel_peers={
            "1502907302901055679": ["bot_a_id", "bot_b_id", "bot_c_id"],
            "9999999999999999999": ["bot_d_id"],
        },
    )
    adapter = A2AAdapter(config)
    assert adapter._channel_peers == {
        "1502907302901055679": ["bot_a_id", "bot_b_id", "bot_c_id"],
        "9999999999999999999": ["bot_d_id"],
    }


def test_peer_cards_default_empty():
    """ADR-012 Task 40: _peer_cards field가 박혀 있고 default = {}."""
    config = _make_config(listen="127.0.0.1:9999")
    adapter = A2AAdapter(config)
    assert hasattr(adapter, "_peer_cards")
    assert adapter._peer_cards == {}


def test_peer_cards_populated_by_resolve_well_known_peers():
    """ADR-012 Task 40: successfully resolved peer의 AgentCard가 _peer_cards에 저장됨.

    ADR-004 `_resolve_well_known_peers` flow: fetch /.well-known/agent-card.json,
    parse discord-identity extension, register peer keyed by bot_user_id.

    Task 34 amend: 같은 fetched card를 self._peer_cards[bot_id] = card 박음.
    Task 40에서 hermes-channel-broadcast/v1 extension verify에 사용.
    """
    config = _make_config(
        listen="127.0.0.1:9999",
        peers=["http://peer-alpha:8800/"],  # list form → goes through _resolve_well_known_peers
    )
    adapter = A2AAdapter(config)

    fake_card = {
        "capabilities": {
            "extensions": [
                {
                    "uri": "https://hermes-a2a.dev/extensions/discord-identity/v1",
                    "params": {"bot_user_id": "peer_alpha_id"},
                }
            ]
        }
    }

    async def _fake_get(*args, **kwargs):
        class _Resp:
            def json(self):
                return fake_card

            def raise_for_status(self):
                return None

        return _Resp()

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url):
            return await _fake_get(url)

    # ADR-004 uses `import httpx` inside `_resolve_well_known_peers`; patch
    # `httpx.AsyncClient` so the test stays offline.
    with patch("httpx.AsyncClient", lambda *a, **kw: _FakeClient()):
        import asyncio

        asyncio.run(adapter._resolve_well_known_peers())

    assert adapter._peers == {"peer_alpha_id": "http://peer-alpha:8800/"}
    assert "peer_alpha_id" in adapter._peer_cards
    assert adapter._peer_cards["peer_alpha_id"] == fake_card


def test_channel_peers_with_unknown_bot_id_does_not_raise():
    """ADR-011 Task 34: channel_peers에 박힌 bot_id가 _peers/_peer_cards에 없어도 graceful.

    Task 35에서 broadcast 시 unknown bot_id는 skip하지만, 박는 시점엔 config 정상 로드.
    """
    config = _make_config(
        listen="127.0.0.1:9999",
        channel_peers={
            "channel_1": ["unknown_bot_id_1", "unknown_bot_id_2"],
        },
    )
    # Should not raise
    adapter = A2AAdapter(config)
    assert adapter._channel_peers["channel_1"] == ["unknown_bot_id_1", "unknown_bot_id_2"]
    # _peers/_peer_cards empty — unknown bot is OK at config load time
    assert adapter._peers == {}
    assert adapter._peer_cards == {}
