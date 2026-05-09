"""Unit tests for the A2A AgentCard builder.

Tests `A2AAdapter._build_agent_card()` against a2a-sdk 1.0.2 proto shapes:
- supported_interfaces[].url instead of top-level url
- Discord identity as an AgentExtension on capabilities
- snake_case proto fields throughout
"""
from __future__ import annotations

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2A_AVAILABLE, A2AAdapter


pytestmark = pytest.mark.skipif(
    not A2A_AVAILABLE, reason="a2a-sdk not installed (extras: a2a)"
)


def _make_adapter(extra: dict) -> A2AAdapter:
    return A2AAdapter(PlatformConfig(enabled=True, extra=extra))


def test_card_minimal_defaults():
    """No agent_card config → name=Hermes Agent, URL from listen, no extension."""
    adapter = _make_adapter({"listen": "127.0.0.1:8765"})
    card = adapter._build_agent_card()

    assert card.name == "Hermes Agent"
    assert "Hermes" in card.description
    assert card.version == "0.1.0"
    assert any(s.id == "chat" for s in card.skills)
    assert list(card.default_input_modes) == ["text/plain"]
    assert list(card.default_output_modes) == ["text/plain"]

    # URL surfaces through supported_interfaces (1.0.2 API)
    assert len(card.supported_interfaces) == 1
    iface = card.supported_interfaces[0]
    assert iface.url == "http://127.0.0.1:8765/"
    assert iface.protocol_binding == "JSONRPC"

    # No Discord identity → no extension
    assert len(card.capabilities.extensions) == 0
    assert card.capabilities.streaming is False


def test_card_with_discord_extension():
    """Discord bot identity is attached as a typed AgentExtension."""
    adapter = _make_adapter(
        {
            "listen": "127.0.0.1:8765",
            "agent_card": {
                "name": "Bot-A",
                "description": "Test bot",
                "version": "1.2.3",
                "discord_bot_user_id": "1234567890",
                "discord_guild_ids": ["999", 888],  # mixed str/int → normalized
            },
        }
    )
    card = adapter._build_agent_card()

    assert card.name == "Bot-A"
    assert card.description == "Test bot"
    assert card.version == "1.2.3"

    # Extension present, with the documented URI
    exts = list(card.capabilities.extensions)
    assert len(exts) == 1
    ext = exts[0]
    assert ext.uri == "https://hermes.nous/extensions/discord-identity/v1"
    assert ext.required is False

    # Struct → JSON dict round-trip
    params_dict = dict(ext.params)
    assert params_dict["bot_user_id"] == "1234567890"
    assert list(params_dict["guild_ids"]) == ["999", "888"]


def test_card_url_override():
    """An explicit agent_card.url overrides the listen-derived default."""
    adapter = _make_adapter(
        {
            "listen": "127.0.0.1:8765",
            "agent_card": {"url": "https://bot-a.example.com:9000"},
        }
    )
    card = adapter._build_agent_card()
    assert card.supported_interfaces[0].url == "https://bot-a.example.com:9000/"


def test_card_no_a2a_raises_when_unavailable(monkeypatch):
    """If a2a-sdk is somehow gone at build time, raise rather than fabricate."""
    import gateway.platforms.a2a as mod

    monkeypatch.setattr(mod, "A2A_AVAILABLE", False)
    adapter = _make_adapter({"listen": "127.0.0.1:8765"})
    with pytest.raises(RuntimeError, match="a2a-sdk not installed"):
        adapter._build_agent_card()
