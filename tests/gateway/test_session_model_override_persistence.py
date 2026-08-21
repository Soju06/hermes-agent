"""Per-session /model overrides must survive gateway restarts (#3659 salvage).

``GatewayRunner._session_model_overrides`` is in-memory, so before persistence
a gateway restart silently reverted every session to the global default model.
The non-secret parts (model/provider/base_url) are now written through to the
session store (``SessionEntry.model_override`` in sessions.json) and lazily
rehydrated on first use after a restart, with credentials re-resolved through
the normal runtime provider resolution.

Covers:
  - the override survives a simulated restart (a second SessionStore instance
    reading the same sessions dir, and a fresh runner rehydrating from it)
  - /new (SessionStore.reset_session) clears the persisted override so a
    restart cannot resurrect it
  - api_key is NEVER serialized to sessions.json
"""
import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.session import (
    SessionEntry,
    SessionSource,
    SessionStore,
    sanitize_model_override,
)

OVERRIDE = {
    "model": "gpt-5o",
    "provider": "openai",
    "api_key": "sk-SUPER-SECRET-do-not-persist",
    "base_url": "https://api.openai.example/v1",
    "api_mode": "responses",
}


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


@pytest.fixture
def store_factory(tmp_path, monkeypatch):
    """Build SessionStores over a shared sessions dir, without SQLite."""

    def _raise():
        raise RuntimeError("SQLite disabled in test")

    import hermes_state

    monkeypatch.setattr(hermes_state, "SessionDB", _raise)

    def _make() -> SessionStore:
        store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
        assert store._db is None
        return store

    return _make


def _sessions_json(tmp_path) -> str:
    return (tmp_path / "sessions.json").read_text(encoding="utf-8")


def test_override_persists_and_survives_restart(store_factory, tmp_path):
    store = store_factory()
    entry = store.get_or_create_session(_make_source())
    session_key = entry.session_key

    store.set_model_override(session_key, OVERRIDE)

    # Simulated restart: a brand-new store instance reads the same dir.
    store2 = store_factory()
    persisted = store2.get_model_override(session_key)
    assert persisted == {
        "model": "gpt-5o",
        "provider": "openai",
        "base_url": "https://api.openai.example/v1",
    }


def _make_runner(store):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner.session_store = store
    return runner


def _apply_router_override(runner, source):
    from hermes_cli.model_switch import ModelSwitchResult

    runner._session_db = None
    runner._pending_model_notes = {}
    runner._evict_cached_agent = MagicMock()
    directive = {
        "route": "SYSTEM_DEV",
        "provider": "anthropic",
        "model": "claude-fable-5",
        "reasoning_effort": "xhigh",
    }
    result = ModelSwitchResult(
        success=True,
        new_model="claude-fable-5",
        target_provider="anthropic",
        api_key="sk-ant-secret",
        base_url="https://api.anthropic.com",
        api_mode="anthropic_messages",
    )
    with patch("hermes_cli.model_switch.switch_model", return_value=result):
        assert asyncio.run(
            runner._apply_model_router_directive(
                source=source,
                session_key=runner.session_store._generate_session_key(source),
                directive=directive,
                cfg={"model": {"default": "chat-model"}},
            )
        ) == (True, True)


def test_runner_rehydrates_override_after_restart(store_factory):
    store = store_factory()
    entry = store.get_or_create_session(_make_source())
    session_key = entry.session_key
    store.set_model_override(session_key, OVERRIDE)

    # Simulated restart: fresh store + fresh runner with an empty in-memory
    # override map, credentials re-resolved via runtime provider resolution.
    runner = _make_runner(store_factory())
    with patch(
        "gateway.run._resolve_runtime_agent_kwargs_for_provider",
        return_value={
            "api_key": "sk-fresh-from-keychain",
            "api_mode": "responses",
            "base_url": "https://api.openai.example/v1",
            "provider": "openai",
        },
    ):
        runner._rehydrate_session_model_override(session_key)

    override = runner._session_model_overrides[session_key]
    assert override["model"] == "gpt-5o"
    assert override["provider"] == "openai"
    assert override["base_url"] == "https://api.openai.example/v1"
    # Credentials come from live resolution, never from disk.
    assert override["api_key"] == "sk-fresh-from-keychain"
    assert override["api_mode"] == "responses"


def test_router_reasoning_survives_store_and_runner_restart(store_factory):
    source = _make_source()
    store = store_factory()
    entry = store.get_or_create_session(source)
    session_key = entry.session_key
    _apply_router_override(_make_runner(store), source)

    # Simulated gateway restart: both the SessionStore and GatewayRunner are
    # new, so no in-memory SessionState survives from the directive apply.
    restarted = _make_runner(store_factory())
    with patch(
        "gateway.run._resolve_runtime_agent_kwargs_for_provider",
        return_value={
            "api_key": "sk-ant-fresh",
            "api_mode": "anthropic_messages",
            "base_url": "https://api.anthropic.com",
            "provider": "anthropic",
        },
    ):
        restarted._rehydrate_session_model_override(session_key)

    assert restarted._session_model_overrides[session_key]["model"] == "claude-fable-5"
    assert restarted._session_reasoning_overrides[session_key] == {
        "enabled": True,
        "effort": "xhigh",
    }


def test_legacy_override_without_reasoning_rehydrates_without_inventing_it(store_factory):
    store = store_factory()
    entry = store.get_or_create_session(_make_source())
    store.set_model_override(entry.session_key, OVERRIDE)

    restarted = _make_runner(store_factory())
    with patch(
        "gateway.run._resolve_runtime_agent_kwargs_for_provider",
        return_value={"api_key": "fresh", "api_mode": "responses"},
    ):
        restarted._rehydrate_session_model_override(entry.session_key)

    assert entry.session_key not in restarted._session_reasoning_overrides


def test_live_reasoning_override_wins_over_rehydrated_router_reasoning(store_factory):
    source = _make_source()
    store = store_factory()
    entry = store.get_or_create_session(source)
    _apply_router_override(_make_runner(store), source)

    restarted = _make_runner(store_factory())
    restarted._set_session_reasoning_override(
        entry.session_key, {"enabled": True, "effort": "low"}
    )
    with patch(
        "gateway.run._resolve_runtime_agent_kwargs_for_provider",
        return_value={"api_key": "fresh", "api_mode": "anthropic_messages"},
    ):
        restarted._rehydrate_session_model_override(entry.session_key)

    assert restarted._session_reasoning_overrides[entry.session_key] == {
        "enabled": True,
        "effort": "low",
    }


def test_reset_clears_persisted_router_reasoning_with_model_override(store_factory):
    source = _make_source()
    store = store_factory()
    entry = store.get_or_create_session(source)
    _apply_router_override(_make_runner(store), source)

    store.reset_session(entry.session_key)

    assert store_factory().get_model_override(entry.session_key) is None


def test_sanitize_model_override():
    assert sanitize_model_override(None) is None
    assert sanitize_model_override({}) is None
    assert sanitize_model_override({"api_key": "sk-x", "api_mode": "chat"}) is None
    assert sanitize_model_override(OVERRIDE) == {
        "model": "gpt-5o",
        "provider": "openai",
        "base_url": "https://api.openai.example/v1",
    }
    assert sanitize_model_override(
        {
            **OVERRIDE,
            "reasoning_config": {"enabled": True, "effort": "xhigh"},
        }
    )["reasoning_config"] == {"enabled": True, "effort": "xhigh"}
    assert "reasoning_config" not in sanitize_model_override(
        {
            **OVERRIDE,
            "reasoning_config": {"enabled": True, "effort": "invalid"},
        }
    )
