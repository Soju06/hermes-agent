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

RUNTIME = ("gpt-5.5", "codex-nekos", "high")


@pytest.fixture
def store(tmp_path, monkeypatch):
    import hermes_state

    def _raise():
        raise RuntimeError("SQLite disabled in test")

    monkeypatch.setattr(hermes_state, "SessionDB", _raise)
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
    from hermes_state import AsyncSessionDB, SessionDB
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("old_session_abc", "telegram", user_id="12345", chat_id="67890")
    db.set_session_title("old_session_abc", "My Project")
    db.create_session("current_session_001", "telegram", user_id="12345", chat_id="67890")
    event = MessageEvent(text="/resume My Project", source=SessionSource(
        platform=Platform.TELEGRAM, user_id="12345", chat_id="67890", user_name="testuser"))
    key = build_session_key(event.source)
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner.config = SimpleNamespace(platforms={})
    runner._voice_mode = {}
    runner._session_db = AsyncSessionDB(db)
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
