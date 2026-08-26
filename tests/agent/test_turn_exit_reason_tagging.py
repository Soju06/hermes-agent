"""Early-return exit_reason tagging in the conversation loop (#74 class).

Before this fix, ``_run_conversation_impl`` had ~30 early ``return`` sites
that never set ``_turn_exit_reason``, so every turn ending on one of them was
recorded in the waterfall trace as the catch-all ``early_return`` — a tag that
says only "the loop returned mid-flight and nobody said why". Context
overflow, compression exhaustion, quota walls, interrupts, and length caps
were all indistinguishable in ``turn_traces.jsonl`` (43/2,017 turns in the
08-14→08-18 window).

The fix threads a ``_tag_exit(reason)`` helper through every old-base early
return site. These tests pin three behavior contracts:

1. STRUCTURAL: every old-base top-level ``return`` in
   ``_run_conversation_impl`` (except the terminal ``finalize_turn`` return)
   is immediately preceded by a ``_tag_exit(...)`` call. Two early exits added
   upstream after this topic was authored remain explicitly inventoried and
   untagged; future return sites still fail the test.
2. RUNTIME: turns that end on an early return emit a specific
   ``exit_reason`` into the trace sink (driven end-to-end through the real
   loop), and the normal completion path still records ``text_response(...)``.
3. FALLBACK: the ``run_conversation`` wrapper's safety net now writes
   ``early_return(untagged)`` / ``exception``, keeping "tagging omission"
   distinguishable from tagged early exits.

Observation-only invariant: tagging must not change the returned dict — the
lock-defer contract from ``test_compression_lock_defer.py`` is re-asserted on
the same run that checks the trace tag.
"""

from __future__ import annotations

import ast
from collections import Counter
import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent import conversation_loop, turn_trace
from run_agent import AIAgent
import run_agent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_trace_state(monkeypatch):
    """Isolate the env gate and thread-local current between tests."""
    monkeypatch.delenv("HERMES_TURN_TRACE", raising=False)
    monkeypatch.delenv("HERMES_TURN_TRACE_FILE", raising=False)
    turn_trace.adopt(None)
    yield
    turn_trace.adopt(None)


@pytest.fixture(autouse=True)
def _fast_paths(monkeypatch):
    """No real sleeps/backoff in loop-driving tests."""
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(run_agent, "jittered_backoff", lambda *a, **k: 0.0)
    monkeypatch.setattr(conversation_loop, "jittered_backoff", lambda *a, **k: 0.0)


@pytest.fixture
def sink(tmp_path, monkeypatch):
    """Enable tracing with the sink redirected into tmp_path."""
    path = tmp_path / "turn_traces.jsonl"
    monkeypatch.setenv("HERMES_TURN_TRACE", "1")
    monkeypatch.setenv("HERMES_TURN_TRACE_FILE", str(path))
    return path


def _read_records(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def _turn_span_tags(record):
    for span in record["spans"]:
        if span["n"] == "turn":
            return span.get("tags", {})
    raise AssertionError("no turn span in trace record")


# ---------------------------------------------------------------------------
# 1. Structural contract: every old-base early return remains tagged
# ---------------------------------------------------------------------------


def _is_tag_exit_call(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id == "_tag_exit"
    )


def _is_finalize_turn_return(stmt: ast.Return) -> bool:
    return (
        isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id == "finalize_turn"
    )


def _known_new_upstream_untagged_return(stmt: ast.Return) -> str | None:
    """Name the two post-topic upstream exits intentionally left untagged."""
    value = stmt.value
    if isinstance(value, ast.Dict):
        names = {
            item.id
            for item in value.values
            if isinstance(item, ast.Name)
        }
        if {"_rep_response", "_rep_error"} <= names:
            return "repetition_dominated_truncation"
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "_compression_deferred_result"
    ):
        return "output_cap_retry_lock_defer"
    return None


def _iter_statement_blocks(node: ast.AST):
    """Yield every list-of-statements block under ``node``, skipping nested
    function/lambda scopes (their returns exit the nested callable, not the
    turn)."""
    stack = [node]
    while stack:
        current = stack.pop()
        for field_value in ast.iter_child_nodes(current):
            if isinstance(
                field_value, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            ):
                continue
            stack.append(field_value)
        for _field, value in ast.iter_fields(current):
            if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                yield value


class TestEveryEarlyReturnIsTagged:
    def test_all_early_returns_preceded_by_tag_exit(self):
        """Invariant between two pieces of the source: each early ``return``
        must relate to a ``_tag_exit`` immediately before it. A new return
        site added without a tag fails here with its line number."""
        source = inspect.getsource(conversation_loop)
        tree = ast.parse(source)
        impl = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "_run_conversation_impl"
        )

        violations = []
        known_new_untagged = []
        returns_seen = 0
        for block in _iter_statement_blocks(impl):
            for idx, stmt in enumerate(block):
                if not isinstance(stmt, ast.Return):
                    continue
                returns_seen += 1
                if _is_finalize_turn_return(stmt):
                    continue  # terminal return; epilogue tags via _turn_exit_reason
                prev = block[idx - 1] if idx > 0 else None
                if prev is None or not _is_tag_exit_call(prev):
                    label = _known_new_upstream_untagged_return(stmt)
                    if label is None:
                        violations.append(stmt.lineno)
                    else:
                        known_new_untagged.append((label, stmt.lineno))

        assert not violations, (
            f"early return(s) at conversation_loop.py line(s) {violations} are "
            f"not immediately preceded by _tag_exit(...) — every early exit "
            f"must record why the turn ended"
        )
        assert Counter(label for label, _line in known_new_untagged) == Counter(
            {
                "repetition_dominated_truncation": 1,
                "output_cap_retry_lock_defer": 1,
            }
        ), f"post-topic upstream untagged exits changed: {known_new_untagged}"
        # The contract is only meaningful if the walker actually saw the
        # loop's returns (guards against the impl being renamed/refactored
        # in a way that silently empties this test).
        assert returns_seen >= 20

    def test_wrapper_fallback_distinguishes_untagged(self):
        """The wrapper's safety-net string must mark the untagged bucket
        explicitly so 'tagging omission' never masquerades as a tagged exit.

        Note: ``run_conversation.__wrapped__`` points at the impl, so
        ``inspect.getsource(run_conversation)`` follows it — read the module
        source instead.
        """
        source = inspect.getsource(conversation_loop)
        assert 'exit_reason="exception" if result is None else "early_return(untagged)"' in source


# ---------------------------------------------------------------------------
# 2. Wrapper fallback behavior (drives run_conversation directly)
# ---------------------------------------------------------------------------


def _bare_agent():
    return SimpleNamespace(
        session_id="exit-reason-test",
        platform="cli",
        model="test/model",
        _api_call_count=0,
        _current_turn_id="turn-1",
    )


class TestWrapperFallback:
    def test_untagged_impl_return_records_early_return_untagged(self, sink, monkeypatch):
        monkeypatch.setattr(
            conversation_loop,
            "_run_conversation_impl",
            lambda *a, **k: {"final_response": "ok"},
        )
        result = conversation_loop.run_conversation(_bare_agent(), "hi")
        assert result == {"final_response": "ok"}

        records = _read_records(sink)
        assert len(records) == 1
        assert records[0]["tags"]["exit_reason"] == "early_return(untagged)"
        assert _turn_span_tags(records[0])["exit_reason"] == "early_return(untagged)"

    def test_raising_impl_records_exception(self, sink, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("impl exploded")

        monkeypatch.setattr(conversation_loop, "_run_conversation_impl", _boom)
        with pytest.raises(RuntimeError, match="impl exploded"):
            conversation_loop.run_conversation(_bare_agent(), "hi")

        records = _read_records(sink)
        assert len(records) == 1
        assert records[0]["tags"]["exit_reason"] == "exception"

    def test_tagged_reason_survives_wrapper(self, sink, monkeypatch):
        """A reason tagged inside the impl must NOT be overwritten by the
        wrapper fallback."""

        def _tagging_impl(agent, *_a, **_k):
            tt = turn_trace.get_bound(agent)
            tt.tag(exit_reason="context_overflow(cannot_compress_further)")
            return {"final_response": None}

        monkeypatch.setattr(conversation_loop, "_run_conversation_impl", _tagging_impl)
        conversation_loop.run_conversation(_bare_agent(), "hi")

        records = _read_records(sink)
        assert records[0]["tags"]["exit_reason"] == (
            "context_overflow(cannot_compress_further)"
        )


# ---------------------------------------------------------------------------
# 3. End-to-end through the real loop (AIAgent + mocked client)
# ---------------------------------------------------------------------------


def _make_tool_defs(*names: str) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": f"{n} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for n in names
    ]


def _mock_response(content="Hello", finish_reason="stop"):
    msg = SimpleNamespace(
        content=content,
        tool_calls=None,
        reasoning_content=None,
        reasoning=None,
    )
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    resp = SimpleNamespace(choices=[choice], model="test/model")
    resp.usage = None
    return resp


def _make_413_error(message="Request entity too large"):
    err = Exception(message)
    err.status_code = 413
    return err


@pytest.fixture()
def agent():
    with (
        patch("run_agent.get_tool_definitions", return_value=_make_tool_defs("web_search")),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        a = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        a.client = MagicMock()
        a._cached_system_prompt = "You are helpful."
        a._use_prompt_caching = False
        a.tool_delay = 0
        a.compression_enabled = True
        a.save_trajectories = False
        return a


_PREFILL = [
    {"role": "user", "content": "previous question"},
    {"role": "assistant", "content": "previous answer"},
]


def _lock_skipping_compress(agent):
    """Compression double that no-ops because another path holds the lock
    (mirrors test_compression_lock_defer.py)."""

    def _compress(messages, _system_message, **_kwargs):
        agent._compression_skipped_due_to_lock = (
            "pid=4242:tid=1:agent=deadbeef:nonce=abcd1234"
        )
        return messages, "You are helpful."

    return _compress


class TestLoopExitReasonsEndToEnd:
    def test_normal_text_response_keeps_text_response_reason(self, sink, agent):
        """Regression pin: the tagging helper must not disturb the normal
        completion path — a plain answer still exits as ``text_response(...)``."""
        agent.client.chat.completions.create.return_value = _mock_response()

        with (
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
        ):
            result = agent.run_conversation("hello")

        assert result["final_response"] == "Hello"
        records = _read_records(sink)
        assert len(records) == 1
        reason = records[0]["tags"]["exit_reason"]
        assert reason.startswith("text_response("), reason

    def test_lock_deferred_413_tags_compression_deferred(self, sink, agent):
        """The 413 lock-defer early return must carry its own reason instead
        of collapsing into ``early_return`` — and stay observation-only (the
        returned dict keeps the #49874 soft-defer contract exactly)."""
        agent.client.chat.completions.create.side_effect = _make_413_error()

        with (
            patch.object(
                agent, "_compress_context",
                side_effect=_lock_skipping_compress(agent),
            ),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
        ):
            result = agent.run_conversation(
                "hello", conversation_history=list(_PREFILL)
            )

        # Observation-only: the soft-defer result contract is untouched.
        assert result.get("compression_deferred") is True
        assert result.get("failed") is False
        assert result.get("completed") is False

        records = _read_records(sink)
        assert len(records) == 1
        assert records[0]["tags"]["exit_reason"] == "compression_deferred(lock_held)"
        assert _turn_span_tags(records[0])["exit_reason"] == (
            "compression_deferred(lock_held)"
        )

    def test_tagging_disabled_trace_is_noop(self, agent):
        """With HERMES_TURN_TRACE unset, _tag_exit must not blow up (trace is
        None) and the turn result must be identical."""
        agent.client.chat.completions.create.side_effect = _make_413_error()

        with (
            patch.object(
                agent, "_compress_context",
                side_effect=_lock_skipping_compress(agent),
            ),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
        ):
            result = agent.run_conversation(
                "hello", conversation_history=list(_PREFILL)
            )

        assert result.get("compression_deferred") is True
        assert result.get("failed") is False
