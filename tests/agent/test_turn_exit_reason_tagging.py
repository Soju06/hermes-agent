"""Early-return exit_reason tagging in the conversation loop (#74 class).

Before this fix, the turn loop had ~30 early exits that never set
``_turn_exit_reason``, so every turn ending on one of them was recorded in the
waterfall trace as the catch-all ``early_return`` — a tag that says only "the
loop returned mid-flight and nobody said why". Context overflow, compression
exhaustion, quota walls, interrupts, and length caps were all indistinguishable
in ``turn_traces.jsonl`` (43/2,017 turns in the 08-14→08-18 window).

Upstream decomposed the old ``_run_conversation_impl`` monolith into phase
modules (``agent/turn_*.py``) that end the turn by returning a ``"return"``
verdict; the loop in ``agent/conversation_loop.py`` forwards that verdict's
``result``. The fix threads ``turn_trace_loop.tag_exit(reason)`` through
every such terminal verdict site. These tests pin three behavior contracts:

1. STRUCTURAL: every ``"return"`` verdict construction in the phase modules
   (and every early ``return`` in ``_run_conversation_turn`` that does not just
   forward a phase result) is immediately preceded by a ``tag_exit(...)``
   call, or is emitted by a helper (``_Trunc.end_turn`` / ``_Recovery.fail_turn``)
   that tags internally. Forwarding sites (``_verdict("return", _x.result)``)
   are exempt — the inner phase already tagged. A new return site added
   without a tag fails here with its module and line number.
2. RUNTIME: turns that end on an early return emit a specific
   ``exit_reason`` into the trace sink (driven end-to-end through the real
   loop), and the normal completion path still records ``text_response(...)``.
3. FALLBACK: the ``run_conversation`` trace wrapper's safety net
   (``turn_trace_loop.conversation_trace``) now writes
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

from agent import (
    conversation_loop,
    turn_api_call,
    turn_api_error,
    turn_empty_response,
    turn_final_response,
    turn_iteration_prep,
    turn_loop_errors,
    turn_overflow,
    turn_preflight,
    turn_preflight_gate,
    turn_recovery,
    turn_recovery_autorecover,
    turn_recovery_fail_fast,
    turn_response_check,
    turn_response_intake,
    turn_tool_round,
    turn_tool_validation,
    turn_trace,
    turn_trace_loop,
    turn_truncation,
)
from run_agent import AIAgent


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
    """No real sleeps/backoff in loop-driving tests.

    The backoff is computed via a lazy ``agent.retry_utils`` import in the phase
    modules; ``run_agent.jittered_backoff`` / ``conversation_loop.jittered_backoff``
    are plugin-compat aliases only.
    """
    import time as _time

    import agent.retry_utils as _retry_utils

    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(_retry_utils, "jittered_backoff", lambda *a, **k: 0.0)


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
# 1. Structural contract: every early return remains tagged
# ---------------------------------------------------------------------------

# Every module that can end the turn with a ``"return"`` verdict (plus the loop
# itself). A phase module added upstream that grows a terminal verdict must be
# listed here to be covered.
PHASE_MODULES = [
    conversation_loop,
    turn_api_call,
    turn_api_error,
    turn_empty_response,
    turn_final_response,
    turn_iteration_prep,
    turn_loop_errors,
    turn_overflow,
    turn_preflight,
    turn_preflight_gate,
    turn_recovery,
    turn_recovery_autorecover,
    turn_recovery_fail_fast,
    turn_response_check,
    turn_response_intake,
    turn_tool_round,
    turn_tool_validation,
    turn_truncation,
]


def _is_tag_exit_call(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id == "tag_exit"
    )


def _is_str_const(node: ast.AST, value: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def _is_forwarded_result(node: ast.AST) -> bool:
    """``_x.result`` — the verdict of an inner phase that already tagged its exit."""
    return isinstance(node, ast.Attribute) and node.attr == "result"


def _terminal_verdict_return(stmt: ast.Return) -> bool:
    """A ``return`` that BUILDS a terminal verdict: ``_verdict("return", ...)``,
    ``_done("return", ...)``, ``self.done("return", ...)``, ``RefusalVerdict("return", ...)``,
    the ``("return", result, ...)`` tuple form or the ``{"action": "return", ...}`` dict
    form (``turn_recovery_autorecover``) — excluding forwards of an inner phase's ``.result``."""
    value = stmt.value
    if isinstance(value, ast.Call) and value.args and _is_str_const(value.args[0], "return"):
        return not (len(value.args) > 1 and _is_forwarded_result(value.args[1]))
    if isinstance(value, ast.Tuple) and value.elts and _is_str_const(value.elts[0], "return"):
        return True
    if isinstance(value, ast.Dict):
        return any(
            _is_str_const(k, "action") and _is_str_const(v, "return")
            for k, v in zip(value.keys, value.values)
        )
    return False


# Names the loop returns that forward an inner phase's or helper's result (which tagged)
# or the finalized result of a loop that ran to its end.
_LOOP_FORWARDED_NAMES = frozenset({"early_result", "_prelude_result", "result"})


def _loop_early_return(stmt: ast.Return) -> bool:
    """In the loop's own functions: a ``return`` that neither forwards a phase result
    (``return _pg.result`` / ``return early_result``) nor is the terminal
    ``return result`` after ``finalize_turn`` — i.e. the loop ends the turn itself
    (a call, or a runtime's own result such as ``return codex_result``)."""
    value = stmt.value
    if isinstance(value, ast.Call):
        return True
    return isinstance(value, ast.Name) and value.id not in _LOOP_FORWARDED_NAMES


def _known_untagged_return(module, stmt: ast.Return) -> str | None:
    """Name exits intentionally left untagged. None today: the interpreter-shutdown
    abandon in ``turn_api_error.handle_api_error`` was the last one (tagged by
    ``fix(telemetry): tag upstream interpreter shutdown exit``)."""
    return None


def _iter_statement_blocks(node: ast.AST):
    """Yield every list-of-statements block under ``node``, including nested
    function scopes (phase helpers nest ``_verdict`` closures and helper methods)."""
    stack = [node]
    while stack:
        current = stack.pop()
        for child in ast.iter_child_nodes(current):
            stack.append(child)
        for _field, value in ast.iter_fields(current):
            if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                yield value


def _scan_module(module, *, select):
    """``(violations, known, returns_seen)`` for every early-exit ``return`` selected by
    ``select(stmt)`` in ``module``'s source: a violation is a selected return whose
    preceding statement is not ``tag_exit(...)``."""
    source = inspect.getsource(module)
    tree = ast.parse(source)
    violations, known, seen = [], [], 0
    for block in _iter_statement_blocks(tree):
        for idx, stmt in enumerate(block):
            if not isinstance(stmt, ast.Return) or not select(stmt):
                continue
            seen += 1
            prev = block[idx - 1] if idx > 0 else None
            if prev is None or not _is_tag_exit_call(prev):
                label = _known_untagged_return(module, stmt)
                if label is None:
                    violations.append(f"{module.__name__}:{stmt.lineno}")
                else:
                    known.append(label)
    return violations, known, seen


class TestEveryEarlyReturnIsTagged:
    def test_all_terminal_verdicts_preceded_by_tag_exit(self):
        """Invariant between two pieces of the source: each terminal ``"return"``
        verdict must relate to a ``tag_exit`` immediately before it. A new return
        site added without a tag fails here with its module and line number."""
        violations, known, returns_seen = [], [], 0
        for module in PHASE_MODULES:
            v, k, n = _scan_module(module, select=_terminal_verdict_return)
            violations += v
            known += k
            returns_seen += n

        assert not violations, (
            f"terminal verdict(s) at {violations} are not immediately preceded by "
            f"tag_exit(...) — every early exit must record why the turn ended"
        )
        assert Counter(known) == Counter(), f"inventoried untagged exits changed: {known}"
        # The contract is only meaningful if the walker actually saw the phases'
        # terminal verdicts (guards against a refactor silently emptying this test).
        assert returns_seen >= 20

    def test_loop_own_early_returns_preceded_by_tag_exit(self):
        """The loop ends the turn itself in three places: the preflight compression
        timeout in ``_run_conversation_turn``, and the two turns that never enter the
        generic loop, which ``_turn_without_loop`` hands back as ``early_result`` (the
        ``pre_llm_call`` veto, and the codex app-server runtime's own result from
        ``_codex_app_server_turn``). Each must tag."""
        source = inspect.getsource(conversation_loop)
        tree = ast.parse(source)
        owners = {"_run_conversation_turn", "_turn_without_loop", "_codex_app_server_turn"}
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in owners]
        assert {f.name for f in funcs} == owners
        violations, seen = [], 0
        for func in funcs:
            for block in _iter_statement_blocks(func):
                for idx, stmt in enumerate(block):
                    if not isinstance(stmt, ast.Return) or not _loop_early_return(stmt):
                        continue
                    seen += 1
                    prev = block[idx - 1] if idx > 0 else None
                    if prev is None or not _is_tag_exit_call(prev):
                        violations.append(f"{func.name}:{stmt.lineno}")
        assert not violations, (
            f"early return(s) at conversation_loop.py {violations} are not "
            f"immediately preceded by tag_exit(...)"
        )
        assert seen >= 3

    def test_helpers_that_build_terminal_verdicts_tag_internally(self):
        """``_Trunc.end_turn`` and ``_Recovery.fail_turn`` are the funnels every
        truncation / overflow terminal exit goes through; they must tag with the
        caller-supplied ``exit_reason`` before stamping the verdict."""
        for fn in (turn_truncation._Trunc.end_turn, turn_overflow._Recovery.fail_turn):
            src = inspect.getsource(fn)
            assert "exit_reason" in inspect.signature(fn).parameters
            assert "tag_exit(" in src, f"{fn.__qualname__} must tag the exit"

    def test_wrapper_fallback_distinguishes_untagged(self):
        """The wrapper's safety-net string must mark the untagged bucket
        explicitly so 'tagging omission' never masquerades as a tagged exit."""
        source = inspect.getsource(turn_trace_loop)
        assert 'exit_reason="early_return(untagged)" if returned else "exception"' in source


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
            "_run_conversation_turn",
            lambda *a, **k: {"final_response": "ok"},
        )
        result = conversation_loop.run_conversation(_bare_agent(), "hi")
        assert result["final_response"] == "ok"

        records = _read_records(sink)
        assert len(records) == 1
        assert records[0]["tags"]["exit_reason"] == "early_return(untagged)"
        assert _turn_span_tags(records[0])["exit_reason"] == "early_return(untagged)"

    def test_raising_impl_records_exception(self, sink, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("impl exploded")

        monkeypatch.setattr(conversation_loop, "_run_conversation_turn", _boom)
        with pytest.raises(RuntimeError, match="impl exploded"):
            conversation_loop.run_conversation(_bare_agent(), "hi")

        records = _read_records(sink)
        assert len(records) == 1
        assert records[0]["tags"]["exit_reason"] == "exception"

    def test_tagged_reason_survives_wrapper(self, sink, monkeypatch):
        """A reason tagged inside the loop must NOT be overwritten by the
        wrapper fallback."""

        def _tagging_impl(agent, *_a, **_k):
            turn_trace_loop.tag_exit("context_overflow(cannot_compress_further)", agent)
            return {"final_response": None}

        monkeypatch.setattr(conversation_loop, "_run_conversation_turn", _tagging_impl)
        conversation_loop.run_conversation(_bare_agent(), "hi")

        records = _read_records(sink)
        assert records[0]["tags"]["exit_reason"] == (
            "context_overflow(cannot_compress_further)"
        )

    def test_tag_exit_resolves_thread_local_trace_without_agent(self, sink, monkeypatch):
        """Phase modules call ``tag_exit(reason)`` with no agent handle: the wrapper
        adopted the trace on this thread, so the tag still lands."""

        def _phase_style_impl(*_a, **_k):
            turn_trace_loop.tag_exit("nous_rate_limit_no_fallback")
            return {"final_response": None}

        monkeypatch.setattr(conversation_loop, "_run_conversation_turn", _phase_style_impl)
        conversation_loop.run_conversation(_bare_agent(), "hi")

        records = _read_records(sink)
        assert records[0]["tags"]["exit_reason"] == "nous_rate_limit_no_fallback"

    def test_tag_exit_is_noop_without_trace(self):
        """Tracing off: no bound/current trace, must not raise."""
        turn_trace_loop.tag_exit("anything")
        turn_trace_loop.tag_exit("anything", _bare_agent())


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
        patch("model_tools.get_tool_definitions", return_value=_make_tool_defs("web_search")),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
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
        """With HERMES_TURN_TRACE unset, tag_exit must not blow up (trace is
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
