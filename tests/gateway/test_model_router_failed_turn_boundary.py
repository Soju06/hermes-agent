"""A hard refusal leaves the durable tail closed (fork #58 refusal-hop-clean-fork).

On v0.23.4 a content-policy hard refusal is out of the model context before the router's post-turn
``_handle_gateway_hard_refusal`` runs. The agent hands back the pre-refusal history and writes no
failed-turn boundary for it (upstream a07afee37c). The gateway's
``_hmwa_retain_content_policy_refused_turn`` soft-deletes the refused turn owner-side and keeps the
user's message on screen (aa24f0ffb6, 6ec9d85795). The router then only stages the one-shot
permissive hop. It must not mask anything: after the retention the newest active user row is the
PREVIOUS turn's, so hiding the assistant rows after it would hide that completed answer and reopen
a user-only tail. A soft refusal (#59 refusal-soft-turnend) is a completed turn, so its reply is
masked on the real store and the earlier answer stays. Only router-classified soft refusals may
remove context (#76 clean-fork-soft-refusal-only), and that mask must still leave a failed-turn
boundary row (``display_kind="failed_turn"``, upstream #107070) active: hiding it would reopen a
user-only tail that alternation repair merges into the next message.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.turn_failure_copy import FAILED_TURN_DISPLAY_KIND, FAILED_TURN_NOTICE
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


def test_soft_refusal_masks_only_the_refused_reply_on_the_real_store(monkeypatch, store):
    import gateway.model_router as mr_mod

    reply = "I won't help with that request, and here is a long explanation of why not."
    sid = seed_session(store.persistence, "s", "telegram")
    first, earlier, current, _ = seed_messages(
        store.persistence, sid,
        {"role": "user", "content": "earlier request"},
        {"role": "assistant", "content": "earlier completed answer"},
        {"role": "user", "content": "current request"},
        {"role": "assistant", "content": reply},
    )
    monkeypatch.setattr(mr_mod, "classify_prior_refusal", MagicMock(return_value={
        "prior_refusal": True, "prior_refusal_confidence": 0.95, "source": "llm",
    }))
    runner = _runner(monkeypatch, store)

    masked = asyncio.run(runner._handle_gateway_soft_refusal(
        "tg:c1", sid, SimpleNamespace(), {"final_response": reply}, "current request",
    ))

    assert masked == 1
    assert list(store.persistence.messages.active_message_ids(sid)) == [first, earlier, current]
    assert runner._model_router_state["tg:c1"]["force_refusal_route"] is True


def test_soft_refusal_mask_skips_the_failed_turn_boundary(monkeypatch, store):
    from hermes_cli.model_routes import load_routes

    sid = seed_session(store.persistence, "s", "telegram")
    _, earlier, _, refusal, boundary = seed_messages(
        store.persistence, sid,
        {"role": "user", "content": "earlier request"},
        {"role": "assistant", "content": "earlier completed answer"},
        {"role": "user", "content": "current request"},
        {"role": "assistant", "content": "I can't help with that."},
        {"role": "assistant", "content": FAILED_TURN_NOTICE, "display_kind": FAILED_TURN_DISPLAY_KIND},
    )
    runner = _runner(monkeypatch, store)
    cfg = _cfg()
    catalog = load_routes(cfg)

    masked = asyncio.run(runner._stage_gateway_refusal_recovery(
        "tg:c1", sid, SimpleNamespace(),
        cfg=cfg, catalog=catalog, refusal=catalog.router.refusal, kind="soft",
    ))

    active_ids = set(store.persistence.messages.active_message_ids(sid))
    assert masked == 1
    assert refusal not in active_ids
    assert {earlier, boundary} <= active_ids
    assert store.persistence.messages.latest_conversation_role(sid) == "assistant"
    assert runner._model_router_state["tg:c1"]["force_refusal_route"] is True
