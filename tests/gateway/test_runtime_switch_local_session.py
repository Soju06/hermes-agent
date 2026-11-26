"""An agent ``model_switch`` on a LOCAL (launch-policy) session is durable (2026-10-30 sync review).

Classic CLI, TUI and Desktop turns run in the gateway under a frozen launch policy, and
``_resolve_session_agent_runtime`` answers from that policy before any messaging override is read.
The runtime_update_callback therefore commits a local session's switch through the owner's
canonical ``model`` mutation (what a typed ``/model`` there does), landing at the next turn
boundary; the messaging override slot and the SessionEntry ``runtime_*`` fields stay untouched.
"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from tests.fixtures.storage_read import session_row
from tests.gateway.fixtures.storage_handles import close_storage_handles, storage_handle  # noqa: F401
from tests.waiting import async_wait_until


def _owner(tmp_path, monkeypatch, handle):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import SessionAuthority
    from gateway.session_controls import AuthorityConnection
    from gateway.session_local import create_local_session
    from hermes_cli import model_switch
    monkeypatch.setattr(run, '_load_gateway_config', lambda *a: {})
    monkeypatch.setattr(model_switch, 'switch_model', lambda **k: model_switch.ModelSwitchResult(
        success=True, new_model=k['raw_input'], target_provider='custom', base_url='http://127.0.0.1:9/v1'))
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store.persistence
    messaging_writes = []
    runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                             _evict_cached_agent=lambda route: None, _cached_agent_for=lambda route: None,
                             _handle_message=handle,
                             _resolve_session_agent_runtime=lambda **k: ('frozen', {}),
                             _set_session_reasoning_override=lambda key, value: None,
                             # The messaging-session path of the callback: recorded, never expected here.
                             _session_state=lambda key: messaging_writes.append(('state', key)) or SimpleNamespace(
                                 conversation=SimpleNamespace(model_override=None, reasoning_override=None)),
                             _persist_session_runtime_override=lambda key, **k: messaging_writes.append(('persist', key)))
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = SessionAuthority(runner, profile_id='owned', instance_id='owner',
                                 persistence=storage_handle(db.scope.profile_dir),
                                 epoch=db.runtime.begin_epoch(instance_id='owner'))
    runner.session_authority = authority
    owner = AuthorityConnection(authority, object(), {'user_id': 'human'})
    ref = create_local_session(authority, owner.actor, dict(request_id='m', source='gui', cwd=str(tmp_path),
                                                            model='frozen', toolsets=[]))
    return store, db, authority, owner, ref, messaging_writes


def _policy(authority, ref):
    from gateway.session_policy import policy_for_source
    live = authority.sessions[ref.session_id]
    return policy_for_source(authority.runner, live.source)


async def _run_turn(authority, owner, db, ref, name):
    from gateway.session_contract import Submission
    turn = await authority.submit(owner.actor, Submission(name, ref, {'text': name}, 'queue'))
    await async_wait_until(
        lambda: db.admissions.get(admission_id=turn.admission_id).status == 'terminal',
        timeout=10, what=f'turn {name} settled')


async def _switch_during_a_turn(tmp_path, monkeypatch, *, model_override, reasoning_config):
    """Run RUNNING (whose agent fires the runtime_update_callback from its worker thread, as the
    model_switch tool does), then NEXT; returns what each turn ran on plus the owner's state."""
    from gateway.run_turn_runner import TurnRunner
    seen, state = [], {}

    async def handle(event):
        policy = _policy(state['authority'], state['ref'])
        seen.append((event.text, policy.model, (policy.reasoning_config or {}).get('effort')))
        if event.text == 'RUNNING':
            live = state['authority'].sessions[state['ref'].session_id]
            ctx = SimpleNamespace(session_key=live.route, source=live.source)
            callback = TurnRunner._make_runtime_update_callback(ctx, state['authority'].runner)
            await asyncio.to_thread(callback, scope='session', model_override=model_override,
                                    reasoning_config=reasoning_config)
        return 'ok'
    store, db, authority, owner, ref, messaging_writes = _owner(tmp_path, monkeypatch, handle)
    state.update(authority=authority, ref=ref)
    try:
        await _run_turn(authority, owner, db, ref, 'RUNNING')
        await _run_turn(authority, owner, db, ref, 'NEXT')
        live = authority.sessions[ref.session_id]
        entry = store.get_entry(live.route)
        # What a cold (restarted) owner restores the session's policy from.
        receipt_policy = db.bindings.receipt(ref.session_id).policy
        return seen, dict(session_row(db, ref.session_id), receipt_policy=receipt_policy), entry, messaging_writes
    finally:
        await owner.close()
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_agent_model_switch_on_a_local_session_commits_its_policy(tmp_path, monkeypatch):
    seen, row, entry, messaging_writes = await _switch_during_a_turn(
        tmp_path, monkeypatch,
        model_override={'model': 'switched', 'provider': 'custom', 'api_key': 'k',
                        'base_url': 'http://127.0.0.1:9/v1', 'api_mode': 'chat_completions'},
        reasoning_config={'enabled': True, 'effort': 'high'})
    # The running turn keeps its route (the live agent switched in place); the next turn runs the
    # committed selection, model and level together, as ``/model switched --reasoning high`` would.
    assert [s[:2] for s in seen] == [('RUNNING', 'frozen'), ('NEXT', 'switched')], seen
    assert seen[-1][2] == 'high', seen
    assert row['model'] == 'switched'
    assert row['receipt_policy']['model'] == 'switched'
    # The policy is the one durable store: no messaging override, no SessionEntry runtime_* shadow.
    assert messaging_writes == []
    assert entry is None or (entry.runtime_model, entry.runtime_provider, entry.runtime_reasoning_effort) == (
        None, None, None)


@pytest.mark.asyncio
async def test_agent_reasoning_switch_on_a_local_session_commits_its_policy(tmp_path, monkeypatch):
    seen, row, entry, messaging_writes = await _switch_during_a_turn(
        tmp_path, monkeypatch, model_override=None, reasoning_config={'enabled': True, 'effort': 'low'})
    assert seen[0][:2] == ('RUNNING', 'frozen') and seen[0][2] != 'low', seen
    assert seen[-1] == ('NEXT', 'frozen', 'low'), seen
    assert messaging_writes == []
    assert entry is None or entry.runtime_reasoning_effort is None
