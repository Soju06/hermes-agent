"""Task 18 — Tier-1.5 well-known peer list resolution (ADR-004).

Tests the `peers: list[url]` config form:
- AgentCards are auto-fetched at connect()
- `_peers` dict is keyed by `discord-identity.bot_user_id`
- Chicken-egg defense: peer down at first attempt, up by second/third → resolves
- Lazy retry in send() picks up late-booting peers
- Missing extension is permanent skip (no retry)
- Mixing list+dict form is rejected
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2A_AVAILABLE, A2AAdapter


pytestmark = pytest.mark.skipif(
    not A2A_AVAILABLE, reason="a2a-sdk not installed (extras: a2a)"
)


def _make_card(name: str, url: str, bot_user_id: str) -> dict[str, Any]:
    """Minimal AgentCard JSON with discord-identity extension."""
    return {
        "name": name,
        "description": f"Test bot {name}",
        "version": "0.1.0",
        "protocol_version": "0.3.0",
        "url": url,
        "default_input_modes": ["text/plain"],
        "default_output_modes": ["text/plain"],
        "supported_interfaces": [
            {"url": url, "protocol_binding": "JSONRPC"}
        ],
        "skills": [
            {
                "id": "chat",
                "name": "Chat",
                "description": "Generic chat",
                "tags": ["chat"],
            }
        ],
        "capabilities": {
            "streaming": False,
            "extensions": [
                {
                    "uri": "https://hermes-a2a.dev/extensions/discord-identity/v1",
                    "description": "Discord bot identity",
                    "params": {
                        "bot_user_id": bot_user_id,
                        "guild_ids": [],
                    },
                }
            ],
        },
    }


def _make_adapter(extra: dict) -> A2AAdapter:
    return A2AAdapter(PlatformConfig(enabled=True, extra=extra))


# ──────────────────────────────────────────────────────────────────────
# Config form parsing
# ──────────────────────────────────────────────────────────────────────


def test_peers_dict_form_still_works():
    """Backward compat: `peers: {bot_id: url}` dict form preserves existing behavior."""
    adapter = _make_adapter(
        {
            "listen": "127.0.0.1:8780",
            "peers": {"1234567890": "http://peer-a:8800/"},
        }
    )
    assert adapter._peers == {"1234567890": "http://peer-a:8800/"}
    assert adapter._peers_to_resolve == []


def test_peers_list_form_starts_empty():
    """List form: `_peers` empty until connect() resolves AgentCards."""
    adapter = _make_adapter(
        {
            "listen": "127.0.0.1:8781",
            "peers": ["http://peer-a:8800/", "http://peer-b:8801/"],
        }
    )
    assert adapter._peers == {}
    assert adapter._peers_to_resolve == [
        "http://peer-a:8800/",
        "http://peer-b:8801/",
    ]


def test_peers_invalid_type_rejected():
    """Anything other than list/dict raises ValueError at __init__."""
    with pytest.raises(ValueError, match="must be list"):
        _make_adapter(
            {
                "listen": "127.0.0.1:8782",
                "peers": "http://just-a-string/",
            }
        )


# ──────────────────────────────────────────────────────────────────────
# AgentCard resolution
# ──────────────────────────────────────────────────────────────────────


def _install_mock_transport(monkeypatch, route_map: dict[str, Any]):
    """Replace httpx.AsyncClient with a MockTransport-backed version.

    `route_map` maps URL → response. Each value is one of:
      - dict → 200 JSON body
      - int → status code (no body)
      - Exception → raised
      - callable(request) → custom handler
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url not in route_map:
            return httpx.Response(404, json={"error": "no route"})
        payload = route_map[url]
        if callable(payload):
            return payload(request)
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, int):
            return httpx.Response(payload)
        if isinstance(payload, dict):
            return httpx.Response(200, json=payload)
        raise TypeError(f"unsupported route value: {type(payload)}")

    transport = httpx.MockTransport(_handler)

    real_async_client = httpx.AsyncClient

    def _make_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("httpx.AsyncClient", _make_client)
    return transport


@pytest.mark.asyncio
async def test_resolve_well_known_peers_happy_path(monkeypatch):
    """Two peer URLs → both AgentCards fetched → _peers keyed by bot_user_id."""
    card_a = _make_card("Bot-A", "http://peer-a:8800/", "1111111111")
    card_b = _make_card("Bot-B", "http://peer-b:8801/", "2222222222")
    _install_mock_transport(
        monkeypatch,
        {
            "http://peer-a:8800/.well-known/agent-card.json": card_a,
            "http://peer-b:8801/.well-known/agent-card.json": card_b,
        },
    )

    adapter = _make_adapter(
        {
            "listen": "127.0.0.1:8783",
            "peers": ["http://peer-a:8800/", "http://peer-b:8801/"],
        }
    )

    await adapter._resolve_well_known_peers()

    assert adapter._peers == {
        "1111111111": "http://peer-a:8800/",
        "2222222222": "http://peer-b:8801/",
    }


@pytest.mark.asyncio
async def test_resolve_skips_card_without_discord_identity(monkeypatch):
    """AgentCard with no discord-identity extension → permanent skip, no retry."""
    bare_card = {
        "name": "Bare",
        "description": "no identity",
        "version": "0.1.0",
        "protocol_version": "0.3.0",
        "url": "http://bare:8800/",
        "default_input_modes": ["text/plain"],
        "default_output_modes": ["text/plain"],
        "supported_interfaces": [{"url": "http://bare:8800/", "protocol_binding": "JSONRPC"}],
        "skills": [{"id": "chat", "name": "Chat", "description": "x", "tags": ["chat"]}],
        "capabilities": {"streaming": False, "extensions": []},
    }
    call_count = {"n": 0}

    def _handler(request):
        call_count["n"] += 1
        return httpx.Response(200, json=bare_card)

    _install_mock_transport(
        monkeypatch,
        {"http://bare:8800/.well-known/agent-card.json": _handler},
    )

    adapter = _make_adapter(
        {"listen": "127.0.0.1:8784", "peers": ["http://bare:8800/"]}
    )

    await adapter._resolve_well_known_peers()

    assert adapter._peers == {}
    # Permanent skip — exactly 1 fetch attempt, no backoff retries.
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_resolve_chicken_egg_recovers(monkeypatch):
    """Peer down on attempt 1 → up by attempt 2 → resolves within backoff window.

    Backoff schedule is [1.0s, 3.0s, 7.0s]. We patch asyncio.sleep so the test
    runs in real-time milliseconds, then assert resolution succeeded after the
    second attempt.
    """
    card = _make_card("Late-Boot", "http://late:8800/", "3333333333")
    attempt_log = {"n": 0}

    def _handler(request):
        attempt_log["n"] += 1
        if attempt_log["n"] == 1:
            raise httpx.ConnectError("peer not up yet")
        return httpx.Response(200, json=card)

    _install_mock_transport(
        monkeypatch,
        {"http://late:8800/.well-known/agent-card.json": _handler},
    )

    sleep_log = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay, *args, **kwargs):
        sleep_log.append(delay)
        await real_sleep(0)  # yield to event loop, but no actual delay

    monkeypatch.setattr("gateway.platforms.a2a.asyncio.sleep", _fake_sleep)

    adapter = _make_adapter(
        {"listen": "127.0.0.1:8785", "peers": ["http://late:8800/"]}
    )

    await adapter._resolve_well_known_peers()

    assert adapter._peers == {"3333333333": "http://late:8800/"}
    assert attempt_log["n"] == 2
    # First retry should have been ~1.0s per backoff schedule.
    assert sleep_log[0] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_resolve_gives_up_after_three_attempts(monkeypatch):
    """All 3 attempts fail → peer stays unresolved, queued for lazy retry."""
    attempt_log = {"n": 0}

    def _handler(request):
        attempt_log["n"] += 1
        raise httpx.ConnectError("permanently down")

    _install_mock_transport(
        monkeypatch,
        {"http://dead:8800/.well-known/agent-card.json": _handler},
    )

    real_sleep = asyncio.sleep

    async def _fake_sleep(delay, *args, **kwargs):
        await real_sleep(0)

    monkeypatch.setattr("gateway.platforms.a2a.asyncio.sleep", _fake_sleep)

    adapter = _make_adapter(
        {"listen": "127.0.0.1:8786", "peers": ["http://dead:8800/"]}
    )

    await adapter._resolve_well_known_peers()

    assert adapter._peers == {}
    # Backoff schedule [1, 3, 7] → exactly 3 attempts, no 4th.
    assert attempt_log["n"] == 3
    assert "http://dead:8800/" in adapter._unresolved_peer_urls


@pytest.mark.asyncio
async def test_send_lazy_retries_unresolved_peer(monkeypatch):
    """send() to unknown peer triggers one more well-known resolve pass.

    Scenario: peer was offline at connect() (3 attempts failed, queued for lazy
    retry). Now the peer is up, and send() is called with the peer's bot_user_id
    as chat_id. The lazy retry path should rediscover the peer and proceed.

    Here we don't go through a full A2A roundtrip — we just verify that send()
    populates _peers via the lazy retry call before failing on the actual
    network (which is fine, because the test isn't mocking the JSON-RPC POST).
    """
    card = _make_card("Recovered", "http://recovered:8800/", "4444444444")
    fetch_log = {"n": 0}

    def _handler(request):
        fetch_log["n"] += 1
        # First attempt = connect() time, all fail. From attempt 4 onward
        # (the lazy retry triggered by send()), succeed.
        if fetch_log["n"] <= 3:
            raise httpx.ConnectError("not up at connect time")
        return httpx.Response(200, json=card)

    _install_mock_transport(
        monkeypatch,
        {
            "http://recovered:8800/.well-known/agent-card.json": _handler,
        },
    )

    real_sleep = asyncio.sleep

    async def _fake_sleep(delay, *args, **kwargs):
        await real_sleep(0)

    monkeypatch.setattr("gateway.platforms.a2a.asyncio.sleep", _fake_sleep)

    adapter = _make_adapter(
        {"listen": "127.0.0.1:8787", "peers": ["http://recovered:8800/"]}
    )

    # Simulate connect() resolution attempt — all fails, peer queued for lazy retry.
    await adapter._resolve_well_known_peers()
    assert adapter._peers == {}
    assert "http://recovered:8800/" in adapter._unresolved_peer_urls

    # Now send() with the bot_user_id should trigger lazy retry that succeeds.
    # Don't actually send (no full a2a server) — verify the lazy retry happens
    # by checking _peers gets populated.
    result = await adapter.send(chat_id="4444444444", content="hi")

    # Peer was discovered by the lazy retry, so the actual send proceeded
    # and failed on the network (no real server). Either way, _peers should
    # now be populated:
    assert adapter._peers == {"4444444444": "http://recovered:8800/"}
    # The send() either succeeded mock-wise or failed at JSON-RPC — both fine.
    # We only care that the lazy resolve happened.
    assert fetch_log["n"] >= 4  # initial 3 + at least 1 lazy
