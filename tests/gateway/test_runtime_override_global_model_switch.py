"""A ``/model --global`` switch is not shadowed by an older persisted runtime route.

Since upstream #100314 the global-success branch drops the session pin with
``set_model_override(key, None)`` so config.yaml is the one durable authority. A runtime route
persisted earlier by an agent ``model_switch`` or a plugin ``runtime_override``
(``SessionEntry.runtime_model``/``runtime_provider``) is the pin's durable twin: unless that clear
also drops it, the next turn's rehydrate restores the old route over the model the user just
saved, in the same process and after a restart. The route's reasoning level is independent of the
model route and stays.
"""

from __future__ import annotations

import hermes_yaml as yaml
import pytest

from gateway.config import Platform
from gateway.platforms.event import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource

DEFAULT_RUNTIME = {
    "api_key": "config-secret",
    "base_url": "https://openrouter.ai/api/v1",
    "provider": "openrouter",
    "api_mode": "chat_completions",
    "command": None,
    "args": [],
    "credential_pool": None,
}


def _source() -> SessionSource:
    return SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm")


def _fake_switch_result():
    from hermes_cli.model_switch import ModelSwitchResult

    return ModelSwitchResult(
        success=True,
        new_model="gpt-5.5",
        target_provider="openrouter",
        provider_changed=True,
        api_key="sk-test",
        base_url="https://openrouter.ai/api/v1",
        api_mode="chat_completions",
        provider_label="OpenRouter",
        is_global=True,
    )


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    import gateway.run as gateway_run

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    cfg_path = hermes_home / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"model": {"default": "old-model", "provider": "openrouter"}, "providers": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr("hermes_cli.config.get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", dict)
    monkeypatch.setattr("hermes_cli.model_switch.switch_model", lambda **kw: _fake_switch_result())
    monkeypatch.setattr("hermes_cli.model_switch.resolve_display_context_length", lambda *a, **k: 272000)
    return cfg_path


def _store_runner(sessions_dir, monkeypatch) -> GatewayRunner:
    """Bare runner over a real JSON-backed SessionStore (the durable runtime route lives there)."""
    import persistence_assembly
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore

    def _no_sqlite(*_a, **_k):
        raise RuntimeError("SQLite disabled in test")

    # The storage interface opens state.db through ``persistence_assembly.open_persistence``
    # (the old ``hermes_state.SessionDB`` store is gone upstream).
    monkeypatch.setattr(persistence_assembly, "open_persistence", _no_sqlite)
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: object()}
    runner._voice_mode = {}
    runner._session_model_overrides = {}
    runner._running_agents = {}
    runner.session_store = SessionStore(sessions_dir=sessions_dir, config=GatewayConfig())
    return runner


@pytest.mark.asyncio
async def test_global_switch_drops_the_older_runtime_route(tmp_path, isolated_home, monkeypatch):
    import gateway.run as gateway_run

    # Given: an agent model_switch / runtime_override persisted a route for this session.
    runner = _store_runner(tmp_path / "sessions", monkeypatch)
    source = _source()
    session_key = runner._session_key_for_source(source)
    runner.session_store.get_or_create_session(source)
    assert runner.session_store.update_runtime_override(
        session_key, model="runtime-model", provider="runtime-provider", reasoning_effort="high",
    )
    route_resolutions = []

    def resolve_provider(**kwargs):
        route_resolutions.append(kwargs)
        return {**DEFAULT_RUNTIME, "provider": "runtime-provider", "base_url": "https://runtime.example/v1"}

    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", resolve_provider)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: dict(DEFAULT_RUNTIME))

    # When: the user saves a model globally.
    confirmation = await runner._handle_model_command(
        MessageEvent(text="/model gpt-5.5 --global", message_type=MessageType.TEXT, source=source)
    )

    # Then: config.yaml decides the next turn; the runtime route is gone, its level stays.
    assert "config.yaml" in confirmation
    assert yaml.safe_load(isolated_home.read_text(encoding="utf-8"))["model"]["default"] == "gpt-5.5"
    entry = runner.session_store.get_entry(session_key)
    assert (entry.runtime_model, entry.runtime_provider) == (None, None)
    assert entry.runtime_reasoning_effort == "high"
    model, runtime = runner._resolve_session_agent_runtime(
        source=source, user_config={"model": {"default": "gpt-5.5", "provider": "openrouter"}},
    )
    assert model == "gpt-5.5"
    assert runtime["provider"] == "openrouter"
    assert route_resolutions == []
    # Restart: a fresh store + runner rehydrate no route either.
    restarted = _store_runner(tmp_path / "sessions", monkeypatch)
    restarted._rehydrate_session_model_override(session_key)
    assert restarted._session_model_override(session_key) is None
    assert route_resolutions == []
