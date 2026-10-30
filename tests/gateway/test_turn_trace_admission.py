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
