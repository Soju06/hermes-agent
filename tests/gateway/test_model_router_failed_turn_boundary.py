"""A hard refusal leaves the durable tail closed (fork #58 refusal-hop-clean-fork).

On v0.23.4 a content-policy hard refusal is out of the model context before the router's post-turn
``_handle_gateway_hard_refusal`` runs. The agent hands back the pre-refusal history and writes no
failed-turn boundary for it (upstream a07afee37c). The gateway's
``_hmwa_retain_content_policy_refused_turn`` soft-deletes the refused turn owner-side and keeps the
user's message on screen (aa24f0ffb6, 6ec9d85795). The router then only stages the one-shot
permissive hop. It must not mask anything: after the retention the newest active user row is the
PREVIOUS turn's, so hiding the assistant rows after it would hide that completed answer and reopen
a user-only tail.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.config import GatewayConfig
from gateway.session import SessionStore
from tests.fixtures.storage_seed import seed_messages, seed_session


def _cfg():
    return {
        "model_routes": {
            "routes": {
                "PERMISSIVE_CHAT": {
                    "description": "low-refusal chat route", "provider": "p2", "model": "grok-4.5",
                },
            },
            "router": {"refusal": {"enabled": True, "notify": False}},
        },
    }


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    s = SessionStore(sessions_dir=tmp_path / "sessions", config=GatewayConfig())
    yield s
    s.close_all_db_handles()


def _runner(monkeypatch, store):
    import gateway.run as run_mod
    from gateway.run import GatewayRunner

    monkeypatch.setattr(run_mod, "_load_gateway_config", _cfg)
    runner = object.__new__(GatewayRunner)
    runner.session_store = store
    runner._model_router_state = {}
    return runner


def test_hard_refusal_after_upstream_retention_keeps_the_earlier_answer(monkeypatch, store):
    sid = seed_session(store.persistence, "s", "telegram")
    first, earlier, current = seed_messages(
        store.persistence, sid,
        {"role": "user", "content": "earlier request"},
        {"role": "assistant", "content": "earlier completed answer"},
        {"role": "user", "content": "current request"},
    )
    # Upstream's owner-side cleanup of the refused turn, which runs before the router handler.
    assert store.retain_refused_turn_for_display(sid, notice="refused", boundary_row_id=current) == {"retained": True}
    runner = _runner(monkeypatch, store)

    masked = asyncio.run(runner._handle_gateway_hard_refusal(
        "tg:c1", sid, SimpleNamespace(),
        {"error": "content_policy_blocked: blocked by provider", "failure_reason": "content_policy_blocked"},
    ))

    assert masked == 0
    assert list(store.persistence.messages.active_message_ids(sid)) == [first, earlier]
    # The durable tail stays closed by the earlier answer.
    assert store.persistence.messages.latest_conversation_role(sid) == "assistant"
    assert runner._model_router_state["tg:c1"]["force_refusal_route"] is True
