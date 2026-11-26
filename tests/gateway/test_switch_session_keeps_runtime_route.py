"""The durable runtime route rides with the chat route like the /model pin (2026-10-30 sync review).

``SessionEntry.runtime_model/runtime_provider/runtime_reasoning_effort`` (agent ``model_switch`` /
plugin ``runtime_override``) are the durable twin of the ``/model`` pin. ``switch_session`` re-points a
key for non-boundary reasons (async-delegation re-pin, compression-tip heal, CLI handoff, /branch) and
keeps the pin (upstream #119864), so it keeps the runtime route too. ``/resume`` and ``/new`` are
conversation boundaries and drop both.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource, SessionStore, build_session_key
from tests.fixtures.storage_seed import seed_session

RUNTIME = ("gpt-5.5", "codex-nekos", "high")


@pytest.fixture
def store(tmp_path, monkeypatch):
    import persistence_assembly

    def _raise(*_args, **_kwargs):
        raise RuntimeError("SQLite disabled in test")

    monkeypatch.setattr(persistence_assembly, "open_persistence", _raise)
    with patch("gateway.session.SessionStore._ensure_loaded"):
        s = SessionStore(sessions_dir=tmp_path / "sessions", config=GatewayConfig())
    s._loaded = True
    return s


def _runtime_key(store):
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="c1", chat_type="dm",
                           user_id="u1", user_name="tester")
    key = store.get_or_create_session(source).session_key
    assert store.update_runtime_override(key, model=RUNTIME[0], provider=RUNTIME[1], reasoning_effort=RUNTIME[2])
    return key


def _route(entry):
    return entry.runtime_model, entry.runtime_provider, entry.runtime_reasoning_effort


def test_switch_session_keeps_the_runtime_route(store):
    key = _runtime_key(store)
    switched = store.switch_session(key, "other_session_id")
    assert switched is not None and switched.session_id == "other_session_id"
    assert _route(switched) == RUNTIME
    assert _route(store.get_entry(key)) == RUNTIME


def test_reset_session_still_drops_the_runtime_route(store):
    """Control: /new is a boundary; the next turn must not rehydrate the old route."""
    key = _runtime_key(store)
    fresh = store.reset_session(key)
    assert fresh is not None and _route(fresh) == (None, None, None)


@pytest.mark.asyncio
async def test_resume_clears_the_runtime_route(tmp_path):
    """/resume is a boundary: switch_session carried the route, so the handler clears it, as it clears
    the /model pin (``set_model_override(key, None)``)."""
    from gateway.run import GatewayRunner
    from persistence_assembly import open_persistence, profile_scope

    class _Runner(GatewayRunner):
        persistence = None  # the storage interface handle, set below (upstream's /resume harness)

    db = open_persistence(profile_scope(home=tmp_path))
    seed_session(db, "old_session_abc", "telegram", user_id="12345", chat_id="67890")
    db.sessions.set_title("old_session_abc", "My Project")
    seed_session(db, "current_session_001", "telegram", user_id="12345", chat_id="67890")
    event = MessageEvent(text="/resume My Project", source=SessionSource(
        platform=Platform.TELEGRAM, user_id="12345", chat_id="67890", user_name="testuser"))
    key = build_session_key(event.source)
    runner = object.__new__(_Runner)
    runner.adapters = {}
    runner.config = SimpleNamespace(platforms={})
    runner._voice_mode = {}
    runner._session_db = runner.persistence = db
    runner._running_agents = {}
    runner._is_user_authorized = lambda _source: True
    entry = MagicMock(session_id="current_session_001", session_key=key)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = entry
    runner.session_store.load_transcript.return_value = []
    runner.session_store.switch_session.return_value = entry
    try:
        await runner._handle_resume_command(event)
    finally:
        db.close()
    runner.session_store.switch_session.assert_called_once()
    runner.session_store.clear_runtime_overrides.assert_called_once_with(key, model=True, reasoning=True)


@pytest.mark.asyncio
@pytest.mark.usefixtures("authority_cwd_in_tmp")  # symbolic profile ids are relative homes
async def test_model_reset_clears_the_runtime_model_route(tmp_path, monkeypatch):
    """``/model reset`` (upstream 5bb7bdc7c4) drops the /model pin. The runtime route is that pin's
    durable twin, so its model half goes too, or the next read rehydrates the agent-picked model and
    the reset is undone. The level is not a model pick and stays."""
    import json

    import gateway.run as gateway_run
    from gateway.platforms.event import MessageType
    from gateway.run import GatewayRunner

    home = tmp_path / "home"
    home.mkdir()
    # JSON is valid YAML; CI ships no pyyaml for test fixtures.
    (home / "config.yaml").write_text(json.dumps({"model": {"default": "cfg-model", "provider": "cfg-prov"}}))
    monkeypatch.setattr(gateway_run, "_hermes_home", home)
    # The route resolves (runtime-override-rehydrate-credentials keeps a route whose resolution
    # names no provider dormant, and a dormant route is no active override to reset).
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs_for_provider",
                        lambda provider, target_model=None: {
                            "provider": provider, "api_key": "runtime-key",
                            "base_url": "https://runtime.example/v1", "api_mode": "codex_responses"})
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="c1", user_id="u1", chat_type="dm")
    key = build_session_key(source)
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    store.get_or_create_session(source)
    assert store.update_runtime_override(key, model=RUNTIME[0], provider=RUNTIME[1], reasoning_effort=RUNTIME[2])

    # A fresh runner: the route exists only in the store, as after a gateway restart.
    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(multiplex_profiles=False)
    runner.session_store = store
    runner._session_db = None
    runner._evict_cached_agent = lambda k, **_kw: None
    runner._normalize_source_for_session_key = lambda s: s
    runner._session_key_for_source = lambda s: build_session_key(s)
    try:
        reply = await runner._handle_model_command(
            MessageEvent(text="/model reset", message_type=MessageType.TEXT, source=source))

        assert "cfg-model" in reply
        assert RUNTIME[0] in runner._pending_model_notes[key]  # the runtime route was the active override
        assert _route(store.get_entry(key)) == (None, None, RUNTIME[2])
        assert runner._get_session_model_override(key) is None  # nothing comes back on the next read
    finally:
        store.close_all_db_handles()
