"""Task 24 — ADR-007 inbound mirror (HermesA2AExecutor side).

Tests `A2AAdapter._mirror_a2a_inbound_to_discord(peer_id, text)` — fires
when the executor receives an inbound A2A message, mirrors it to the
configured Discord channel as `📥 from {peer_id}: {text}` so humans see
both sides of the peer-to-peer conversation in the channel.

Replier-side stream-consumer mirroring (the OUTbound path) is handled by
the `_status_adapter` swap in gateway/run.py. The inbound mirror is the
*other* half of the conversation visibility — without it, only the reply
shows up and the channel reads like a one-sided phone call.

Best-effort semantics (matches ADR-003 Risk D):
- mirror_channel_id missing → no-op
- text empty / whitespace-only → no-op
- Discord adapter not registered → no-op
- Discord send raises → swallow + log (do NOT break the inbound dispatch)
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
    """Records send() calls; lets us assert chat_id/content."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_next: bool = False

    async def send(self, chat_id: str, content: str, **kwargs: Any) -> Any:
        self.calls.append((chat_id, content))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated discord send failure")
        return SimpleNamespace(success=True)


def _install_runner(monkeypatch, discord_adapter: Any | None) -> None:
    """Stub `_gateway_runner_ref` so the helper finds the spy."""
    fake_runner = (
        SimpleNamespace(adapters={Platform.DISCORD: discord_adapter})
        if discord_adapter is not None
        else None
    )

    def _runner_ref():
        return fake_runner

    monkeypatch.setattr("gateway.run._gateway_runner_ref", _runner_ref)


# ──────────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────────

def test_inbound_mirror_posts_with_peer_prefix(monkeypatch):
    """mirror_chan + text + Discord → one send with `📥 from {peer}: {text}`."""
    a = _make_adapter({"mirror_channel_id": "1502907302901055679"})
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)

    asyncio.run(a._mirror_a2a_inbound_to_discord("bot-a-id", "Hello Bot-B!"))

    assert len(spy.calls) == 1
    chat_id, content = spy.calls[0]
    assert chat_id == "1502907302901055679"
    assert "bot-a-id" in content
    assert "Hello Bot-B!" in content
    # Visible inbound marker (emoji or text) must distinguish from reply.
    assert "📥" in content or "from" in content.lower()


# ──────────────────────────────────────────────────────────────────────
# No-op edge cases
# ──────────────────────────────────────────────────────────────────────

def test_inbound_mirror_noop_when_mirror_channel_unset(monkeypatch):
    """No mirror_channel_id → silent no-op, zero sends."""
    a = _make_adapter()  # no mirror_channel_id
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)

    asyncio.run(a._mirror_a2a_inbound_to_discord("bot-a-id", "Hello"))

    assert spy.calls == []


def test_inbound_mirror_noop_when_text_empty(monkeypatch):
    """Empty text → no-op (don't post '📥 from X: ' with nothing to show)."""
    a = _make_adapter({"mirror_channel_id": "12345"})
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)

    asyncio.run(a._mirror_a2a_inbound_to_discord("bot-a-id", ""))
    asyncio.run(a._mirror_a2a_inbound_to_discord("bot-a-id", "   "))
    asyncio.run(a._mirror_a2a_inbound_to_discord("bot-a-id", None))  # type: ignore[arg-type]

    assert spy.calls == []


def test_inbound_mirror_noop_when_no_discord_adapter(monkeypatch):
    """No Discord adapter registered → silent no-op."""
    a = _make_adapter({"mirror_channel_id": "12345"})
    _install_runner(monkeypatch, None)  # No runner / no Discord

    asyncio.run(a._mirror_a2a_inbound_to_discord("bot-a-id", "Hello"))
    # No way to assert sends; just must not raise.


def test_inbound_mirror_noop_when_no_runner(monkeypatch):
    """No gateway runner registered → silent no-op."""
    a = _make_adapter({"mirror_channel_id": "12345"})

    def _runner_ref():
        return None

    monkeypatch.setattr("gateway.run._gateway_runner_ref", _runner_ref)

    asyncio.run(a._mirror_a2a_inbound_to_discord("bot-a-id", "Hello"))
    # Just must not raise.


# ──────────────────────────────────────────────────────────────────────
# Failure swallow (best-effort)
# ──────────────────────────────────────────────────────────────────────

def test_inbound_mirror_swallows_discord_send_failure(monkeypatch):
    """Discord send raises → caller does NOT see exception (best-effort)."""
    a = _make_adapter({"mirror_channel_id": "12345"})
    spy = _SpyDiscord()
    spy.fail_next = True
    _install_runner(monkeypatch, spy)

    # Must NOT raise.
    asyncio.run(a._mirror_a2a_inbound_to_discord("bot-a-id", "Hello"))

    # The send was attempted.
    assert len(spy.calls) == 1
