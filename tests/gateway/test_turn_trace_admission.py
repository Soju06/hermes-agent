"""Gateway turn traces survive the durable admission drain (fork topic turn-waterfall-tracing).

``_handle_message_with_agent`` begins a turn's waterfall trace (``agent/turn_trace.py``) and the
adapter lifecycle that delivers the reply finishes it. With the session authority every inbound
message is admitted to the durable FIFO and executed by the drain on an event restored from the
row, so the trace is bound to an event the adapter never sees: unless the drain hands it to the
waiting adapter event (as it does the crash marker), or finishes it itself, no record is written.
Real GatewayRunner, adapter, SessionAuthority, drain and ``execute_admission`` over SQLite; only
the turn preparation after the trace began is a stand-in (no model).
"""
import asyncio
import json

import pytest

from tests.gateway.test_shared_busy_receive_loop import Adapter, _statuses, _wait_for


def _records(sink):
    if not sink.exists():
        return []
    return [json.loads(line) for line in sink.read_text(encoding='utf-8').splitlines() if line.strip()]


async def _owner(monkeypatch, tmp_path, gate=None):
    from gateway.config import GatewayConfig, Platform
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority

    sink = tmp_path / 'turn_traces.jsonl'
    monkeypatch.setenv('HERMES_TURN_TRACE', '1')
    monkeypatch.setenv('HERMES_TURN_TRACE_FILE', str(sink))
    monkeypatch.setenv('TELEGRAM_ALLOWED_USERS', 'fixture-user')
    runner = GatewayRunner(GatewayConfig())
    adapter = Adapter()
    adapter.gateway_runner = runner  # as GatewayRunner._create_adapter binds every adapter
    runner.adapters = {Platform.TELEGRAM: adapter}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='owner')
    runner._wire_adapter_handlers(adapter)

    async def prepare(event, source, session_entry, session_key, quick_key, run_generation):
        # The real _handle_message_with_agent already began the trace; answer without a model.
        if gate is not None and event.text == 'one':
            await gate.wait()
        return 'reply to ' + event.text, None
    monkeypatch.setattr(runner, '_hmwa_prepare_turn', prepare)
    source = adapter.build_source(chat_id='trace-chat', chat_type='dm', user_id='fixture-user')
    return authority, adapter, source, sink


@pytest.mark.asyncio
async def test_admitted_turn_trace_is_finished_after_delivery(monkeypatch, tmp_path):
    from gateway.platforms.event import MessageEvent

    _authority, adapter, source, sink = await _owner(monkeypatch, tmp_path)
    await adapter.handle_message(MessageEvent(text='one', source=source, message_id='m1'))
    await _wait_for(lambda: adapter.sent and not adapter._background_tasks, 15)

    assert adapter.sent == ['reply to one']
    records = _records(sink)
    assert len(records) == 1, 'the admitted turn emitted no trace record'
    spans = [span['n'] for span in records[0]['spans']]
    assert 'gateway.session_resolve' in spans  # begun by the drain's _handle_message_with_agent
    assert 'transport.delivery' in spans  # finished by the adapter lifecycle after the send
    assert records[0]['tags']['status'] == 'ok'


@pytest.mark.asyncio
async def test_busy_followup_trace_is_finished_once(monkeypatch, tmp_path):
    from gateway.platforms.event import MessageEvent

    gate = asyncio.Event()
    authority, adapter, source, sink = await _owner(monkeypatch, tmp_path, gate)
    await adapter.handle_message(MessageEvent(text='one', source=source, message_id='m1'))
    await _wait_for(lambda: _statuses(authority).get('one') == 'started')
    # Sent while the session is busy: dispatch_shared_busy admits it and its observer sends the reply.
    await asyncio.wait_for(adapter.handle_message(MessageEvent(text='two', source=source, message_id='m2')), 5)
    gate.set()
    await _wait_for(lambda: 'reply to two' in adapter.sent and not adapter._background_tasks, 15)

    assert adapter.sent == ['reply to one', 'reply to two']
    records = _records(sink)
    assert len(records) == 2, f'expected one trace per turn, got {len(records)}'
    assert len({record['trace_id'] for record in records}) == 2
    assert all(record['tags']['status'] == 'ok' for record in records)
    # The observer that sends the follow-up's reply times it like the adapter lifecycle does.
    assert all('transport.delivery' in [span['n'] for span in record['spans']] for record in records)


@pytest.mark.asyncio
async def test_rewritten_turn_trace_is_finished_after_delivery(monkeypatch, tmp_path):
    """A ``pre_gateway_dispatch`` rewrite admits a ``dataclasses.replace`` copy, which drops the
    adapter event's ``_turn_marker_handoff`` (no marker adopter) but keeps its source: the adapter
    lifecycle still finishes the trace after the send, and the source carries nothing afterwards."""
    from agent import turn_trace
    from gateway.platforms.event import MessageEvent

    async def rewrite(name, **_kwargs):
        return [{'action': 'rewrite', 'text': 'rewritten'}] if name == 'pre_gateway_dispatch' else []
    monkeypatch.setattr('hermes_cli.lifecycle.ainvoke_hook', rewrite)
    _authority, adapter, source, sink = await _owner(monkeypatch, tmp_path)
    await adapter.handle_message(MessageEvent(text='one', source=source, message_id='m1'))
    await _wait_for(lambda: adapter.sent and not adapter._background_tasks, 15)

    assert adapter.sent == ['reply to rewritten']
    records = _records(sink)
    assert len(records) == 1
    spans = [span['n'] for span in records[0]['spans']]
    assert 'gateway.session_resolve' in spans
    assert 'transport.delivery' in spans, 'the drain finished the rewritten turn before its delivery'
    assert records[0]['tags']['status'] == 'ok'
    assert turn_trace.get_bound(source) is None


@pytest.mark.parametrize('raised, status', [(asyncio.CancelledError, 'cancelled'), (RuntimeError, 'error')])
def test_raised_admission_finishes_its_trace_with_the_exit_status(monkeypatch, tmp_path, raised, status):
    """A turn that raised in the drain has no lifecycle left to deliver it; the drain finishes the
    trace with the adapter lifecycle's status vocabulary (a cancelled turn is not an error)."""
    from types import SimpleNamespace

    from agent import turn_trace
    from gateway.session_ingress import _hand_over_turn_trace

    sink = tmp_path / 'turn_traces.jsonl'
    monkeypatch.setenv('HERMES_TURN_TRACE', '1')
    monkeypatch.setenv('HERMES_TURN_TRACE_FILE', str(sink))
    source = SimpleNamespace()
    event = SimpleNamespace(source=source)
    trace = turn_trace.TurnTrace(key='raised')
    turn_trace.bind(event, trace)
    turn_trace.bind(source, trace)
    authority = SimpleNamespace(marker_adopters={'a1': SimpleNamespace()})
    with pytest.raises(raised):
        try:
            raise raised()
        finally:
            _hand_over_turn_trace(authority, 'a1', event, handed=False)

    assert [record['tags']['status'] for record in _records(sink)] == [status]
    assert turn_trace.get_bound(event) is None and turn_trace.get_bound(source) is None


def test_trace_hand_over_never_raises_into_the_drain(monkeypatch, tmp_path):
    """It runs first in ``execute_admission``'s ``finally``, ahead of the crash-marker release and
    the admission ContextVar resets: tracing must never break a turn."""
    from types import SimpleNamespace

    from agent import turn_trace
    from gateway.session_ingress import _hand_over_turn_trace

    class Authority:
        @property
        def marker_adopters(self):
            raise RuntimeError('adopter registry unavailable')

    monkeypatch.setenv('HERMES_TURN_TRACE', '1')
    monkeypatch.setenv('HERMES_TURN_TRACE_FILE', str(tmp_path / 'turn_traces.jsonl'))
    event = SimpleNamespace(source=SimpleNamespace())
    turn_trace.bind(event, turn_trace.TurnTrace(key='broken'))
    _hand_over_turn_trace(Authority(), 'a1', event, handed=True)
