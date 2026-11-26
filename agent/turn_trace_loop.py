"""Turn-trace bookkeeping for the conversation loop (``agent/conversation_loop.py``).

The loop only marks phase boundaries; this module owns the rest: trace ownership around
``run_conversation`` (adopt the gateway's bound trace, or own one for direct callers), the root
``turn`` span, per-iteration ``iteration`` spans, the ``llm.call`` retrofit and the per-tool
``tools.call`` spans of ``agent/tool_executor.py``. Every helper is a no-op when no trace is bound
(``HERMES_TURN_TRACE`` unset), see ``agent/turn_trace.py``.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from agent import turn_trace


def trace_llm_call(
    _tt, started, agent, s: Any, error: Optional[str] = None, pfp_tags: Optional[dict] = None,
) -> None:
    """Close the in-flight ``llm.call`` span (turn trace). Failed / interrupted attempts are
    recorded too (tagged ``error``) so their wall time isn't misread as hermes overhead.
    ``pfp_tags`` (HERMES_TURN_TRACE_PREFIX request-prefix fingerprints) ride successful calls."""
    if _tt is None or started is None:
        return
    try:
        llm_tags = {"api_request_id": s.api_request_id, "model": agent.model}
        if error:
            llm_tags["error"] = error
        else:
            stream_diag = getattr(agent, "_last_stream_diag", None)
            if isinstance(stream_diag, dict) and stream_diag.get("first_chunk_at"):
                llm_tags["ttft_ms"] = round(
                    (stream_diag["first_chunk_at"] - stream_diag["started_at"]) * 1000.0, 1,
                )
            if pfp_tags:
                llm_tags.update(pfp_tags)
        _tt.add_span("llm.call", started, time.time(), **llm_tags)
    except Exception:
        pass


class IterationSpans:
    """``iteration`` spans of one ``_run_conversation_turn`` loop (``i`` counts passes from 1).

    The open iteration is parked on the trace (``_pending_iteration``) so the root-span close in
    :func:`conversation_trace` can still end it when the loop returns early or raises."""

    __slots__ = ("trace", "_pass")

    def __init__(self, trace: Optional[turn_trace.TurnTrace]):
        self.trace = trace
        self._pass = 0

    def next(self) -> None:
        """Close the previous pass's span and open the next one."""
        if self.trace is None:
            return
        now = time.time()
        _close_pending_iteration(self.trace, now)
        self._pass += 1
        self.trace._pending_iteration = (now, self._pass)

    def close(self, exit_reason: Any) -> None:
        """Loop left normally: close the open pass and record why the loop ended. Trace-level tag;
        :func:`conversation_trace` copies it onto the root ``turn`` span (exit reason is loop
        state, not a result field)."""
        if self.trace is None:
            return
        _close_pending_iteration(self.trace, time.time())
        try:
            self.trace.tag(exit_reason=exit_reason)
        except Exception:
            pass


def _close_pending_iteration(trace: turn_trace.TurnTrace, now: float) -> None:
    pending = getattr(trace, "_pending_iteration", None)
    if pending:
        trace.add_span("iteration", pending[0], now, i=pending[1])
    trace._pending_iteration = None


@contextmanager
def conversation_trace(agent: Any) -> Iterator[None]:
    """Waterfall trace ownership around one ``run_conversation`` call, plus its root ``turn`` span.

    The gateway begins its trace at message ingress and binds it to the agent before this call, so
    it is only adopted here; direct callers (CLI, API server, subagents) own a trace for the turn
    when tracing is enabled."""
    trace = turn_trace.get_bound(agent)
    owner = False
    if trace is None and turn_trace.enabled():
        trace = turn_trace.begin(
            key=agent.session_id or None,
            platform=getattr(agent, "platform", None) or "cli",
            session_key=agent.session_id or "",
        )
        owner = trace is not None
        if owner:
            turn_trace.bind(agent, trace)
    elif trace is not None:
        turn_trace.adopt(trace)
    started = time.time()
    returned = False
    try:
        yield
        returned = True
    finally:
        if trace is not None:
            _record_turn_span(agent, trace, started, returned)
        if owner:
            trace.finish()
            turn_trace.bind(agent, None)
            turn_trace.adopt(None)


def _record_turn_span(agent: Any, trace: turn_trace.TurnTrace, started: float, returned: bool) -> None:
    try:
        _close_pending_iteration(trace, time.time())
        if "exit_reason" not in trace.tags:
            trace.tag(exit_reason="early_return" if returned else "exception")
        tags = {
            "model": agent.model,
            "iterations": getattr(agent, "_api_call_count", 0),
            "turn_id": getattr(agent, "_current_turn_id", "") or "",
        }
        for key in ("tool_calls", "exit_reason"):
            if key in trace.tags:
                tags[key] = trace.tags[key]
        trace.add_span("turn", started, time.time(), **tags)
    except Exception:
        pass


def trace_tool_call(agent: Any, name: str, start: float, duration: float, **tags: Any) -> None:
    """One ``tools.call`` span (``start`` to now) on the agent's bound trace; ``execute_ms`` is the
    executor's own measured tool duration."""
    trace = turn_trace.get_bound(agent)
    if trace is None:
        return
    try:
        trace.add_span("tools.call", start, time.time(), tool=name, execute_ms=round(duration * 1000.0, 1), **tags)
    except Exception:
        pass


@contextmanager
def tool_publish_span(agent: Any, name: str, start: float, duration: float) -> Iterator[None]:
    """Sequential executor: the block publishes one result (observe -> commit/SessionDB flush ->
    project); the ``tools.call`` span from ``start`` closes after it, ``flush_ms`` timing the block."""
    if turn_trace.get_bound(agent) is None:
        yield
        return
    flush_started = time.time()
    try:
        yield
    finally:
        trace_tool_call(agent, name, start, duration, flush_ms=round((time.time() - flush_started) * 1000.0, 1))
