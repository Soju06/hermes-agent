from __future__ import annotations

import json

import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig, Platform
from gateway.session import SessionSource, SessionStore


def _source() -> SessionSource:
    # A messaging source: LOCAL sessions answer from their frozen launch policy before any
    # persisted override is read, so only a messaging turn ever reads this route.
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_name="Telegram DM",
        chat_type="dm",
        user_id="user-1",
    )


def _runner(store: SessionStore) -> gateway_run.GatewayRunner:
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.session_store = store
    runner.config = None
    runner._session_model_overrides = {}
    runner._load_reasoning_config = lambda model="": {
        "enabled": True,
        "effort": "medium",
    }
    return runner


def test_runtime_route_rehydrates_from_state_db_without_json(tmp_path, monkeypatch):
    # Given: the profile's state.db is the test's isolated HERMES_HOME store.
    sessions_dir = tmp_path / "sessions"
    config = GatewayConfig(write_sessions_json=False)
    first_store = SessionStore(sessions_dir=sessions_dir, config=config)
    entry = first_store.get_or_create_session(_source())
    session_key = entry.session_key
    assert first_store.update_runtime_override(
        session_key,
        model="restart-model",
        provider="restart-provider",
        reasoning_effort="high",
    )
    assert not (sessions_dir / "sessions.json").exists()
    # Upstream (v0.23) keeps the gateway routing index on the storage interface handle of the
    # one store that owns it (``SessionStore._routing_persistence``).
    first_db = first_store._routing_persistence
    assert first_db is not None
    rows = first_db.routing.load_routing_entries(
        routing_scope=first_store._routing_scope()
    )
    durable = json.loads(rows[session_key])
    assert durable["runtime_model"] == "restart-model"
    assert durable["runtime_provider"] == "restart-provider"
    assert durable["runtime_reasoning_effort"] == "high"
    assert "api_key" not in durable
    assert "base_url" not in durable
    assert "api_mode" not in durable
    assert "fake-runtime-key" not in rows[session_key]
    first_store.close_all_db_handles()

    restarted_store = SessionStore(sessions_dir=sessions_dir, config=config)
    runner = _runner(restarted_store)

    def resolve_provider(**kwargs):
        assert kwargs == {
            "requested": "restart-provider",
            "target_model": "restart-model",
        }
        return {
            "api_key": "fake-runtime-key",
            "base_url": "https://restart.example/v1",
            "provider": "restart-provider",
            "api_mode": "codex_responses",
            "command": None,
            "args": [],
            "credential_pool": None,
        }

    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        resolve_provider,
    )
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: pytest.fail("DB-only restart must use the persisted route bundle"),
    )

    # When
    # Production shape (run_turn.py): the turn passes its source, so the launch-policy gate runs.
    model, runtime = runner._resolve_session_agent_runtime(
        source=_source(),
        user_config={
            "model": {
                "default": "default-model",
                "provider": "default-provider",
            }
        },
    )
    reasoning = runner._resolve_session_reasoning_config(source=_source())

    # Then
    assert model == "restart-model"
    assert runtime["provider"] == "restart-provider"
    assert runtime["base_url"] == "https://restart.example/v1"
    assert runtime["api_key"] == "fake-runtime-key"
    assert runtime["api_mode"] == "codex_responses"
    assert reasoning == {"enabled": True, "effort": "high"}
    restarted_store.close_all_db_handles()


def test_reasoning_reset_drops_the_persisted_level_across_a_restart(tmp_path):
    """``/reasoning reset`` clears the durable ``runtime_reasoning_effort`` of an agent model_switch /
    plugin runtime_override, not only the in-memory override: otherwise the next resolve, and every
    restart, re-installs it. The runtime route itself stays."""
    # Given: a persisted route with a level, already rehydrated into this process.
    sessions_dir = tmp_path / "sessions"
    config = GatewayConfig(write_sessions_json=False)
    store = SessionStore(sessions_dir=sessions_dir, config=config)
    session_key = store.get_or_create_session(_source()).session_key
    assert store.update_runtime_override(
        session_key, model="restart-model", provider="restart-provider", reasoning_effort="high",
    )
    runner = _runner(store)
    assert runner._resolve_session_reasoning_config(source=_source()) == {"enabled": True, "effort": "high"}

    # When
    runner._apply_reasoning_selection(session_key, "telegram", "reset")

    # Then: the configured level wins now and after a restart; the route is untouched.
    assert runner._resolve_session_reasoning_config(source=_source()) == {"enabled": True, "effort": "medium"}
    store.close_all_db_handles()
    restarted_store = SessionStore(sessions_dir=sessions_dir, config=config)
    entry = restarted_store.get_entry(session_key)
    assert entry.runtime_reasoning_effort is None
    assert (entry.runtime_model, entry.runtime_provider) == ("restart-model", "restart-provider")
    assert _runner(restarted_store)._resolve_session_reasoning_config(source=_source()) == {
        "enabled": True, "effort": "medium",
    }
    restarted_store.close_all_db_handles()
