"""Instant-transport-failure fail-fast for ``turn_api_error.handle_api_error``.

Consecutive sub-2s transport failures (connection refused/reset before any bytes) mean the
endpoint is down, not congested. With no fallback left, burning the remaining ``max_retries``
(a dozen attempts with growing backoff) only adds minutes of user-visible silence, so the turn
ends with an actionable error after ``HERMES_FAST_CONN_FAIL_LIMIT`` instant failures (default 3,
0 disables). Slow timeouts reset the streak and keep the full retry budget: backoff genuinely
helps congestion. The check runs once ``turn_recovery.route_classified_error`` fell through,
so the eager fallback had its chance first.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from agent.turn_failure_copy import stamp_failure
from agent.turn_recovery import (
    _TRANSPORT_FAILURE_REASONS, _failed_turn_result, _with_delivered_partial, settle_delivered_partial,
)
from agent.turn_retry_state import TurnRetryState

logger = logging.getLogger("agent.conversation_loop")

# A transport failure faster than this died before the endpoint answered (refused/reset).
_INSTANT_FAILURE_SECONDS = 2.0


def _failed_connect_seconds(agent: Any, retry: TurnRetryState) -> float:
    """How long the failed attempt's last physical connect lived.

    On the streaming wire one loop attempt is up to ``HERMES_STREAM_RETRIES + 1`` connects with
    an interruptible reconnect backoff (1s, 2s, ...) between them, so the attempt's wall time
    against an endpoint that refuses every connect is that backoff, not the endpoint. The
    streaming call stamps a fresh ``_last_stream_diag`` per connect: one opened inside this
    attempt is the measure; otherwise (non-streaming, codex passthrough) the attempt is."""
    started = retry.attempt_started_at
    diag = getattr(agent, "_last_stream_diag", None)
    connect_started = diag.get("started_at") if isinstance(diag, dict) else None
    if started and isinstance(connect_started, (int, float)) and connect_started >= started:
        started = connect_started
    return time.time() - started


def track_instant_transport_failure(agent: Any, retry: TurnRetryState, classified: Any) -> None:
    """Extend the instant-failure streak on ``retry``, or reset it on any other outcome.

    ``attempt_started_at`` is stamped by the retry loop right before the API call; a slow
    timeout (or an unstamped attempt) resets the streak like a non-transport error does."""
    is_transport_failure = classified.reason in _TRANSPORT_FAILURE_REASONS
    if is_transport_failure and _failed_connect_seconds(agent, retry) < _INSTANT_FAILURE_SECONDS:
        retry.fast_transport_failures += 1
    else:
        retry.fast_transport_failures = 0


def _fast_fail_limit() -> int:
    try:
        return int(os.environ.get("HERMES_FAST_CONN_FAIL_LIMIT", "3") or 3)
    except ValueError:
        return 3


def fast_transport_failure_result(
    agent: Any, api_error: Exception, classified: Any, retry: TurnRetryState, *,
    messages: list[dict[str, Any]], conversation_history: Any, api_call_count: int,
    current_turn_user_idx: Any = None,
) -> Optional[dict[str, Any]]:
    """The failed-turn result once the instant-failure streak reaches the limit, else None.

    Runs after the eager fallback had its chance: a streak here means fallback either does not
    exist or already failed to absorb the outage. Persists like retry exhaustion: this turn's
    continuation trail collapses into the delivered partial (kept as the reply, ``partial``),
    and an open user/tool tail is left to the failed-turn boundary closers
    (``conversation_loop._close_durable_failed_turn``, the gateway's own), which write a
    display-only row instead of an error the model would read back as its own reply."""
    limit = _fast_fail_limit()
    if not (
        classified.reason in _TRANSPORT_FAILURE_REASONS
        and limit > 0
        and retry.fast_transport_failures >= limit
        and not agent._has_pending_fallback()
    ):
        return None
    agent._flush_status_buffer()
    msg = (
        f"Provider unreachable: {retry.fast_transport_failures} "
        f"consecutive instant connection failures "
        f"({agent._summarize_api_error(api_error)}). Giving up early — the endpoint "
        f"appears to be down and no fallback provider is available."
    )
    # Diagnostic rail, like the retry-exhaustion line: display.suppress_warning_notifications hides it.
    agent._emit_diagnostic_status(f"❌ {msg}")
    logger.error(
        "Fail-fast after %d instant transport failures %s error=%s",
        retry.fast_transport_failures,
        agent._client_log_context(),
        api_error,
    )
    delivered = settle_delivered_partial(agent, messages, current_turn_user_idx)
    agent._persist_session(messages, conversation_history)
    final_response, keep_partial = _with_delivered_partial(msg, msg, delivered)
    # The classifier's retry verdict, as on retry exhaustion: kanban in-place recovery and the
    # error surfaces key on ``failure_retryable``.
    result = stamp_failure(
        _failed_turn_result(final_response, messages, api_call_count, msg),
        classified.reason.value, bool(classified.retryable),
    )
    if keep_partial:
        result["partial"] = True
    return result
