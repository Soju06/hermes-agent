"""Loop-side turn-trace bookkeeping (``agent/turn_trace_loop.py``; fork topic turn-waterfall-tracing).

A direct caller (CLI) owns one trace per ``run_conversation`` and emits it with the root ``turn``
span, per-pass ``iteration`` spans and the ``llm.call`` retrofit; a trace the gateway bound before
the call is only adopted, never finished here. A raised turn still closes the open iteration.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent import turn_trace
from agent.turn_trace_loop import IterationSpans, conversation_trace


@pytest.fixture()
def sink(tmp_path, monkeypatch):
    path = tmp_path / "turn_traces.jsonl"
    monkeypatch.setenv("HERMES_TURN_TRACE", "1")
    monkeypatch.setenv("HERMES_TURN_TRACE_FILE", str(path))
    yield path
    turn_trace.adopt(None)


def _records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _span(record, name):
    return [span for span in record["spans"] if span["n"] == name]


def _agent():
    return SimpleNamespace(session_id="sess-1", platform="cli", model="test/model",
                           _api_call_count=0, _current_turn_id="turn-1")


def test_cli_turn_emits_one_record_with_loop_spans(sink):
    from run_agent import AIAgent
    from tests.agent.test_run_agent import _mock_assistant_msg

    with (
        patch("model_tools.get_tool_definitions", return_value=[]),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
    ):
        agent = AIAgent(api_key="test-key-1234567890", base_url="https://openrouter.ai/api/v1",
                        quiet_mode=True, skip_context_files=True, skip_memory=True)
    agent.client = MagicMock()
    agent._cached_system_prompt = "You are helpful."
    agent._use_prompt_caching = False
    agent.compression_enabled = False
    agent.save_trajectories = False
    agent.client.chat.completions.create.side_effect = [SimpleNamespace(
        id="chatcmpl-test", model="test/model", usage=None,
        choices=[SimpleNamespace(index=0, message=_mock_assistant_msg(content="answer"), finish_reason="stop")],
    )]
    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("question")

    assert result["final_response"] == "answer"
    records = _records(sink)
    assert len(records) == 1
    names = {span["n"] for span in records[0]["spans"]}
    assert {"turn", "iteration", "iteration.context_assemble", "iteration.request_setup", "llm.call",
            "turn.prologue", "turn.finalize"} <= names
    (turn,) = _span(records[0], "turn")
    assert turn["tags"]["exit_reason"] == records[0]["tags"]["exit_reason"]
    assert turn["tags"]["exit_reason"] not in ("exception", "early_return")
    assert turn_trace.get_bound(agent) is None  # the owner unbinds after finishing


def test_raised_turn_closes_the_open_iteration_and_tags_exception(sink):
    agent = _agent()
    with pytest.raises(RuntimeError):
        with conversation_trace(agent):
            iterations = IterationSpans(turn_trace.get_bound(agent))
            iterations.next()
            iterations.next()
            raise RuntimeError("provider exploded")

    (record,) = _records(sink)
    assert [span["tags"]["i"] for span in _span(record, "iteration")] == [1, 2]
    (turn,) = _span(record, "turn")
    assert turn["tags"]["exit_reason"] == "exception"
    assert turn_trace.get_bound(agent) is None


def test_gateway_bound_trace_is_adopted_not_finished(sink):
    agent = _agent()
    gateway_trace = turn_trace.TurnTrace(key="agent:main:telegram:dm:1")
    turn_trace.bind(agent, gateway_trace)
    with conversation_trace(agent):
        iterations = IterationSpans(turn_trace.get_bound(agent))
        iterations.next()
        iterations.close("text_response(stop)")

    assert _records(sink) == []  # the gateway finishes its own trace after delivery
    assert turn_trace.get_bound(agent) is gateway_trace
    record = gateway_trace.finish(status="ok")
    (turn,) = _span(record, "turn")
    assert turn["tags"]["exit_reason"] == "text_response(stop)"
    assert len(_span(record, "iteration")) == 1


def test_tool_batch_records_tools_call_spans_from_both_executors(sink):
    """A mixed batch runs its parallel-safe prefix on the concurrent executor and its barrier on
    the sequential one (``agent/tool_executor.py``); every call is one ``tools.call`` span inside
    the batch's ``tools.batch`` span, and the sequential call also times its publish
    (``flush_ms``)."""
    from run_agent import AIAgent
    from tests.agent.test_run_agent import _mock_tool_call

    tool_defs = [{"type": "function", "function": {
        "name": name, "description": name, "parameters": {"type": "object", "properties": {}}}}
        for name in ("web_search", "terminal")]
    with (
        patch("model_tools.get_tool_definitions", return_value=tool_defs),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
    ):
        agent = AIAgent(api_key="test-key-1234567890", base_url="https://openrouter.ai/api/v1",
                        quiet_mode=True, skip_context_files=True, skip_memory=True)
    agent.client = MagicMock()
    trace = turn_trace.TurnTrace(key="agent:main:telegram:dm:1")
    turn_trace.bind(agent, trace)
    calls = [
        _mock_tool_call("web_search", '{"query":"a"}', call_id="s1"),
        _mock_tool_call("web_search", '{"query":"b"}', call_id="s2"),
        _mock_tool_call("terminal", '{"command":"echo done"}', call_id="t1"),
    ]
    messages = []
    with patch("model_tools.handle_function_call",
               side_effect=lambda name, args, task_id, **kwargs: json.dumps({"ok": name})):
        agent._execute_tool_calls(SimpleNamespace(content="", tool_calls=calls), messages, "task-1")

    assert [m["tool_call_id"] for m in messages] == ["s1", "s2", "t1"]
    record = trace.finish(status="ok")
    (batch,) = _span(record, "tools.batch")
    assert batch["tags"] == {"count": 3, "mode": "segmented", "segments": 2}
    tool_spans = _span(record, "tools.call")
    assert sorted(span["tags"]["tool"] for span in tool_spans) == ["terminal", "web_search", "web_search"]
    for span in tool_spans:
        assert "execute_ms" in span["tags"]
        assert batch["t0"] <= span["t0"] and span["t0"] + span["d"] <= batch["t0"] + batch["d"] + 0.05
    assert [("flush_ms" in span["tags"]) for span in tool_spans if span["tags"]["tool"] == "web_search"] == [
        False, False]
    (sequential,) = [span for span in tool_spans if span["tags"]["tool"] == "terminal"]
    assert sequential["tags"]["flush_ms"] >= 0
