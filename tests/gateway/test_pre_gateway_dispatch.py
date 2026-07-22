"""Tests for the pre_gateway_dispatch plugin hook.

The hook allows plugins to intercept incoming messages before auth and
agent dispatch. It runs in _handle_message and acts on returned action
dicts: {"action": "skip"|"rewrite"|"allow"}.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _clear_auth_env(monkeypatch) -> None:
    for key in (
        "TELEGRAM_ALLOWED_USERS",
        "WHATSAPP_ALLOWED_USERS",
        "GATEWAY_ALLOWED_USERS",
        "TELEGRAM_ALLOW_ALL_USERS",
        "WHATSAPP_ALLOW_ALL_USERS",
        "GATEWAY_ALLOW_ALL_USERS",
    ):
        monkeypatch.delenv(key, raising=False)


def _make_event(text: str = "hello", platform: Platform = Platform.WHATSAPP) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_id="m1",
        source=SessionSource(
            platform=platform,
            user_id="15551234567@s.whatsapp.net",
            chat_id="15551234567@s.whatsapp.net",
            user_name="tester",
            chat_type="dm",
        ),
    )


def _make_runner(platform: Platform):
    from gateway.run import GatewayRunner

    config = GatewayConfig(
        platforms={platform: PlatformConfig(enabled=True)},
    )
    runner = object.__new__(GatewayRunner)
    runner.config = config
    adapter = SimpleNamespace(send=AsyncMock())
    runner.adapters = {platform: adapter}
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = False
    runner.pairing_store._is_rate_limited.return_value = False
    runner.session_store = MagicMock()
    runner._running_agents = {}
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._update_prompt_pending = {}
    return runner, adapter


@pytest.mark.asyncio
async def test_hook_skip_short_circuits_dispatch(monkeypatch):
    """A plugin returning {'action': 'skip'} drops the message before auth."""
    _clear_auth_env(monkeypatch)

    def _fake_hook(name, **kwargs):
        if name == "pre_gateway_dispatch":
            return [{"action": "skip", "reason": "plugin-handled"}]
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_hook)

    runner, adapter = _make_runner(Platform.WHATSAPP)

    result = await runner._handle_message(_make_event("hi"))

    assert result is None
    adapter.send.assert_not_awaited()
    runner.pairing_store.generate_code.assert_not_called()


@pytest.mark.asyncio
async def test_hook_rewrite_replaces_event_text(monkeypatch):
    """A plugin returning {'action': 'rewrite', 'text': ...} mutates event.text."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")

    seen_text = {}

    def _fake_hook(name, **kwargs):
        if name == "pre_gateway_dispatch":
            return [{"action": "rewrite", "text": "REWRITTEN"}]
        return []

    async def _capture(event, source, _quick_key, _run_generation):
        seen_text["value"] = event.text
        return "ok"

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_hook)

    runner, _adapter = _make_runner(Platform.WHATSAPP)
    runner._handle_message_with_agent = _capture  # noqa: SLF001

    await runner._handle_message(_make_event("original"))

    assert seen_text.get("value") == "REWRITTEN"


@pytest.mark.asyncio
async def test_hook_runtime_override_applies_after_auth_before_agent(monkeypatch):
    """runtime_override switches the session route before the first agent call."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")

    def _fake_hook(name, **kwargs):
        if name == "pre_gateway_dispatch":
            return [
                {
                    "action": "runtime_override",
                    "model": "gpt-5.5",
                    "provider": "codex-nekos",
                    "reasoning_effort": "high",
                    "reason": "codex-lb PR review",
                }
            ]
        return []

    switch_calls = []

    def _fake_switch_model(**kwargs):
        switch_calls.append(kwargs)
        return SimpleNamespace(
            success=True,
            new_model="gpt-5.5",
            target_provider="codex-nekos",
            api_key="test-key",
            base_url="https://codex.nekos.me/v1",
            api_mode="codex_responses",
            provider_label="codex-nekos",
            error_message="",
        )

    captured = {}

    async def _capture(event, source, _quick_key, _run_generation):
        session_key = runner._session_key_for_source(source)  # noqa: SLF001
        captured["model_override"] = runner._session_model_overrides.get(session_key)  # noqa: SLF001
        captured["reasoning_override"] = runner._session_reasoning_overrides.get(session_key)  # noqa: SLF001
        return "ok"

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_hook)
    monkeypatch.setattr("hermes_cli.model_switch.switch_model", _fake_switch_model)

    runner, _adapter = _make_runner(Platform.WHATSAPP)
    runner._handle_message_with_agent = _capture  # noqa: SLF001

    await runner._handle_message(_make_event("codex-lb 817 확인해줘"))

    assert switch_calls
    assert switch_calls[0]["raw_input"] == "gpt-5.5"
    assert switch_calls[0]["explicit_provider"] == "codex-nekos"
    assert captured["model_override"] == {
        "model": "gpt-5.5",
        "provider": "codex-nekos",
        "api_key": "test-key",
        "base_url": "https://codex.nekos.me/v1",
        "api_mode": "codex_responses",
    }
    assert captured["reasoning_override"] == {"enabled": True, "effort": "high"}


@pytest.mark.asyncio
async def test_hook_runtime_override_does_not_apply_before_auth(monkeypatch):
    """runtime_override from an unauthorized message must not mutate session state."""
    _clear_auth_env(monkeypatch)
    monkeypatch.delenv("WHATSAPP_ALLOWED_USERS", raising=False)

    def _fake_hook(name, **kwargs):
        if name == "pre_gateway_dispatch":
            return [
                {
                    "action": "runtime_override",
                    "model": "gpt-5.5",
                    "provider": "codex-nekos",
                    "reasoning_effort": "high",
                }
            ]
        return []

    def _fail_switch_model(**_kwargs):
        raise AssertionError("runtime override applied before auth")

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_hook)
    monkeypatch.setattr("hermes_cli.model_switch.switch_model", _fail_switch_model)

    runner, _adapter = _make_runner(Platform.WHATSAPP)
    runner.pairing_store.generate_code.return_value = "12345"

    result = await runner._handle_message(_make_event("codex-lb 817 확인해줘"))

    assert result is None
    assert runner._session_model_overrides == {}


@pytest.mark.asyncio
async def test_hook_allow_falls_through_to_auth(monkeypatch):
    """A plugin returning {'action': 'allow'} continues to normal dispatch."""
    _clear_auth_env(monkeypatch)
    # No allowed users set → auth fails → pairing flow triggers.
    monkeypatch.delenv("WHATSAPP_ALLOWED_USERS", raising=False)

    def _fake_hook(name, **kwargs):
        if name == "pre_gateway_dispatch":
            return [{"action": "allow"}]
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_hook)

    runner, adapter = _make_runner(Platform.WHATSAPP)
    runner.pairing_store.generate_code.return_value = "12345"

    result = await runner._handle_message(_make_event("hi"))

    # auth chain ran → pairing code was generated
    assert result is None
    runner.pairing_store.generate_code.assert_called_once()


@pytest.mark.asyncio
async def test_hook_exception_does_not_break_dispatch(monkeypatch):
    """A raising plugin hook does not break the gateway."""
    _clear_auth_env(monkeypatch)
    monkeypatch.delenv("WHATSAPP_ALLOWED_USERS", raising=False)

    def _fake_hook(name, **kwargs):
        raise RuntimeError("plugin blew up")

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_hook)

    runner, _adapter = _make_runner(Platform.WHATSAPP)
    runner.pairing_store.generate_code.return_value = None

    # Should not raise; falls through to auth chain.
    result = await runner._handle_message(_make_event("hi"))
    assert result is None


@pytest.mark.asyncio
async def test_internal_events_bypass_hook(monkeypatch):
    """Internal events (event.internal=True) skip the plugin hook entirely."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")

    called = {"count": 0}

    def _fake_hook(name, **kwargs):
        called["count"] += 1
        return [{"action": "skip"}]

    async def _capture(event, source, _quick_key, _run_generation):
        return "ok"

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_hook)

    runner, _adapter = _make_runner(Platform.WHATSAPP)
    runner._handle_message_with_agent = _capture  # noqa: SLF001

    event = _make_event("hi")
    event.internal = True

    # Even though the hook would say skip, internal events bypass it.
    await runner._handle_message(event)
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_hook_prepend_prepends_text_to_event(monkeypatch):
    """A plugin returning {'action': 'prepend', 'text': ...} prepends to event.text."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")

    seen_text = {}

    def _fake_hook(name, **kwargs):
        if name == "pre_gateway_dispatch":
            return [{"action": "prepend", "text": "[CONTEXT]\nSome context."}]
        return []

    async def _capture(event, source, _quick_key, _run_generation):
        seen_text["value"] = event.text
        return "ok"

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_hook)

    runner, _adapter = _make_runner(Platform.WHATSAPP)
    runner._handle_message_with_agent = _capture

    await runner._handle_message(_make_event("hello world"))

    assert seen_text["value"].startswith("[CONTEXT]")
    assert "hello world" in seen_text["value"]


@pytest.mark.asyncio
async def test_hook_multiple_prepends_accumulate(monkeypatch):
    """Multiple prepend directives are combined before the original text."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")

    seen_text = {}

    def _fake_hook(name, **kwargs):
        if name == "pre_gateway_dispatch":
            return [
                {"action": "prepend", "text": "First."},
                {"action": "prepend", "text": "Second."},
            ]
        return []

    async def _capture(event, source, _quick_key, _run_generation):
        seen_text["value"] = event.text
        return "ok"

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_hook)

    runner, _adapter = _make_runner(Platform.WHATSAPP)
    runner._handle_message_with_agent = _capture

    await runner._handle_message(_make_event("original"))

    val = seen_text["value"]
    assert val.startswith("First.")
    assert "Second." in val
    assert val.endswith("original")


@pytest.mark.asyncio
async def test_hook_prepend_with_runtime_override(monkeypatch):
    """prepend and runtime_override directives both apply in the same turn."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")

    dispatched = {}

    def _fake_hook(name, **kwargs):
        if name == "pre_gateway_dispatch":
            return [
                {
                    "action": "runtime_override",
                    "model": "gpt-5.5",
                    "provider": "codex-nekos",
                },
                {"action": "prepend", "text": "[Dev routing applied]"},
            ]
        return []

    async def _capture(event, source, _quick_key, _run_generation):
        dispatched["text"] = event.text
        return "ok"

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", _fake_hook)

    runner, _adapter = _make_runner(Platform.WHATSAPP)
    runner._handle_message_with_agent = _capture
    # Stub the override application
    runner._apply_gateway_runtime_override = lambda d, s: None

    await runner._handle_message(_make_event("build it"))

    assert "[Dev routing applied]" in dispatched["text"]
    assert "build it" in dispatched["text"]
