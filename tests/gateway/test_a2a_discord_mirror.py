"""Task 19 — ADR-003 dual-send mirror (replier-side, static channel).

Tests the A2AAdapter's `_mirror_to_discord` path attached to the
`_wrapped` capture handler:

- When mirror_channel_id is configured, the reply text is sent to the
  Discord adapter via `discord_adapter.send(chat_id=mirror_chan, content=...)`
- Mirror is replier-side: only the bot that GENERATED the reply mirrors;
  the receiver does not re-mirror (handled by the wrapper firing only on
  the executor side).
- Rate-limit: bursts respect `min_dual_send_interval_seconds` (default 1.5s).
- Best-effort: a Discord send failure does NOT break the A2A reply path
  (the post_response capture callback still fires).
- Mirror is no-op when mirror_channel_id is unset, when text is empty,
  or when no Discord adapter is registered.
"""
from __future__ import annotations

import asyncio
import time
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
    """Records send() calls; lets us assert chat_id/content + ordering."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []
        self.fail_next: bool = False

    async def send(self, chat_id: str, content: str, **kwargs: Any) -> Any:
        # Record both args and a monotonic timestamp for rate-limit assertions.
        self.calls.append(
            (chat_id, content, asyncio.get_running_loop().time())
        )
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated discord send failure")
        return SimpleNamespace(success=True)


def _install_runner(monkeypatch, discord_adapter: Any | None) -> None:
    """Stub `_gateway_runner_ref` so `_mirror_to_discord` finds the spy."""
    fake_runner = SimpleNamespace(adapters={Platform.DISCORD: discord_adapter})

    def _runner_ref():
        return fake_runner if discord_adapter is not None else None

    monkeypatch.setattr("gateway.run._gateway_runner_ref", _runner_ref)


# ──────────────────────────────────────────────────────────────────────
# Configuration parsing
# ──────────────────────────────────────────────────────────────────────


def test_init_defaults_no_mirror():
    """No mirror config → _mirror_channel_id is None."""
    a = _make_adapter()
    assert a._mirror_channel_id is None
    assert a._min_mirror_interval_s == pytest.approx(1.5)
    assert a._last_mirror_at == {}


def test_init_reads_mirror_channel_from_extra():
    """`mirror_channel_id` in extra populates the field."""
    a = _make_adapter({"mirror_channel_id": "12345"})
    assert a._mirror_channel_id == "12345"


def test_init_reads_min_interval_override():
    """`min_dual_send_interval_seconds` in extra overrides the 1.5s default."""
    a = _make_adapter({"min_dual_send_interval_seconds": 0.25})
    assert a._min_mirror_interval_s == pytest.approx(0.25)


# ──────────────────────────────────────────────────────────────────────
# _mirror_to_discord behavior
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mirror_sends_to_configured_channel(monkeypatch):
    """Reply text + configured mirror channel → discord.send called once."""
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)
    a = _make_adapter({"mirror_channel_id": "channel-abc"})

    await a._mirror_to_discord("hello from a2a")

    assert len(spy.calls) == 1
    chan, content, _t = spy.calls[0]
    assert chan == "channel-abc"
    assert content == "hello from a2a"


@pytest.mark.asyncio
async def test_mirror_noop_when_channel_unset(monkeypatch):
    """No mirror_channel_id → no discord.send call (silent skip)."""
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)
    a = _make_adapter()  # no mirror_channel_id

    await a._mirror_to_discord("would have been mirrored")
    assert spy.calls == []


@pytest.mark.asyncio
async def test_mirror_noop_when_text_empty(monkeypatch):
    """Empty/None text → no send (don't post empty messages to Discord)."""
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)
    a = _make_adapter({"mirror_channel_id": "c"})

    await a._mirror_to_discord("")
    await a._mirror_to_discord(None)  # type: ignore[arg-type]
    assert spy.calls == []


@pytest.mark.asyncio
async def test_mirror_noop_when_no_discord_adapter(monkeypatch):
    """No Discord adapter registered → silent skip (no exception)."""
    _install_runner(monkeypatch, None)
    a = _make_adapter({"mirror_channel_id": "c"})

    # Should not raise.
    await a._mirror_to_discord("text")


@pytest.mark.asyncio
async def test_mirror_failure_does_not_propagate(monkeypatch):
    """Discord send raising → swallowed (best-effort), logged, no re-raise."""
    spy = _SpyDiscord()
    spy.fail_next = True
    _install_runner(monkeypatch, spy)
    a = _make_adapter({"mirror_channel_id": "c"})

    # Should not raise — the A2A reply path must keep working.
    await a._mirror_to_discord("text that will fail to mirror")

    assert len(spy.calls) == 1  # call attempted


# ──────────────────────────────────────────────────────────────────────
# Rate limiting (min_dual_send_interval_seconds)
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mirror_rate_limit_enforces_min_interval(monkeypatch):
    """Two back-to-back mirrors → second one waits ≥ min_interval.

    Use a small interval (0.2s) so the test doesn't take forever.
    """
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)
    a = _make_adapter(
        {"mirror_channel_id": "c", "min_dual_send_interval_seconds": 0.2}
    )

    t0 = time.monotonic()
    await a._mirror_to_discord("first")
    await a._mirror_to_discord("second")
    elapsed = time.monotonic() - t0

    assert len(spy.calls) == 2
    # Total wall time must exceed the rate-limit gap.
    assert elapsed >= 0.18  # tolerate ~10ms scheduler jitter
    # And the recorded timestamps inside the spy must be ≥ interval apart.
    _, _, t_first = spy.calls[0]
    _, _, t_second = spy.calls[1]
    assert (t_second - t_first) >= 0.18


@pytest.mark.asyncio
async def test_mirror_rate_limit_per_channel_independent(monkeypatch):
    """Different mirror_channel_id keys don't block each other.

    (Future-proof: even though Phase 2 uses one static channel, the
    _last_mirror_at dict is keyed by chan_id.)
    """
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)
    a = _make_adapter(
        {"mirror_channel_id": "c-A", "min_dual_send_interval_seconds": 1.0}
    )

    # Send twice to channel A — second one will wait.
    # Then immediately swap to a different channel — should NOT wait.
    await a._mirror_to_discord("A1")
    a._mirror_channel_id = "c-B"

    t0 = time.monotonic()
    await a._mirror_to_discord("B1")
    elapsed = time.monotonic() - t0

    assert elapsed < 0.2  # different channel → no rate-limit hit
    assert len(spy.calls) == 2


# ──────────────────────────────────────────────────────────────────────
# _wrapped capture handler integration
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wrapped_handler_invokes_mirror_after_capture(monkeypatch):
    """When the wrapped handler returns text, mirror fires after the capture cb."""
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)
    a = _make_adapter({"mirror_channel_id": "mirror-chan"})

    captured: list[str] = []

    async def real_handler(event):
        return "agent reply text"

    a.set_message_handler(real_handler)

    # Register a fake post-response capture callback for the message_id.
    async def cap_cb(text: str) -> None:
        captured.append(text)

    a._post_response_callbacks["msg-1"] = cap_cb

    # Pull the wrapped handler out via the base-class slot. The wrapper is
    # stored on `self._message_handler` after super().set_message_handler.
    wrapped = a._message_handler
    assert wrapped is not None

    fake_event = SimpleNamespace(text="ping", message_id="msg-1")
    result = await wrapped(fake_event)

    # Wrapper still returns None (so base.py skips its send path).
    assert result is None
    # Capture callback fired with the agent text.
    assert captured == ["agent reply text"]
    # And mirror sent the same text to Discord.
    assert len(spy.calls) == 1
    chan, content, _ = spy.calls[0]
    assert chan == "mirror-chan"
    assert content == "agent reply text"


@pytest.mark.asyncio
async def test_wrapped_handler_no_mirror_when_text_empty(monkeypatch):
    """Handler returns None/empty → no mirror call (and no capture call either)."""
    spy = _SpyDiscord()
    _install_runner(monkeypatch, spy)
    a = _make_adapter({"mirror_channel_id": "mirror-chan"})

    async def real_handler(event):
        return None  # streaming-already-delivered shape

    a.set_message_handler(real_handler)
    wrapped = a._message_handler

    fake_event = SimpleNamespace(text="ping", message_id="msg-empty")
    result = await wrapped(fake_event)
    assert result is None
    assert spy.calls == []
