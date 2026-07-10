from __future__ import annotations

import threading
from datetime import datetime

from gateway.session import SessionEntry, SessionStore


SESSION_KEY = "agent:main:local:dm"


def _store_with_entry() -> tuple[SessionStore, SessionEntry]:
    # Upstream's set_model_override persists a ``dataclasses.replace`` snapshot before publishing,
    # so the entry must be a real SessionEntry and the whole-index writers are stubbed.
    now = datetime.now()
    entry = SessionEntry(session_key=SESSION_KEY, session_id="s1", created_at=now, updated_at=now)
    store = object.__new__(SessionStore)
    store._lock = threading.Lock()
    store._loaded = True
    store._entries = {SESSION_KEY: entry}
    store._save = lambda: None
    store._snapshot_routing_locked = lambda: ({}, 1)
    store._persist_routing_data = lambda data, generation: None
    return store, entry


def test_runtime_route_write_clears_older_model_override_only():
    # Given
    store, entry = _store_with_entry()
    entry.model_override = {"model": "slash-model", "provider": "slash-provider"}
    entry.runtime_reasoning_effort = "high"
    update_runtime_override = getattr(store, "update_runtime_override", None)

    # When
    assert callable(update_runtime_override)
    updated = update_runtime_override(
        SESSION_KEY,
        model="runtime-model",
        provider="runtime-provider",
    )

    # Then
    assert updated is True
    assert entry.model_override is None
    assert entry.runtime_model == "runtime-model"
    assert entry.runtime_provider == "runtime-provider"
    assert entry.runtime_reasoning_effort == "high"


def test_model_override_write_clears_older_runtime_route_only():
    # Given
    store, entry = _store_with_entry()
    entry.runtime_model = "runtime-model"
    entry.runtime_provider = "runtime-provider"
    entry.runtime_reasoning_effort = "low"

    # When
    store.set_model_override(
        SESSION_KEY,
        {
            "model": "slash-model",
            "provider": "slash-provider",
            "api_key": "must-not-persist",
            "api_mode": "codex_responses",
        },
    )

    # Then
    assert entry.model_override == {
        "model": "slash-model",
        "provider": "slash-provider",
    }
    assert entry.runtime_model is None
    assert entry.runtime_provider is None
    assert entry.runtime_reasoning_effort == "low"
