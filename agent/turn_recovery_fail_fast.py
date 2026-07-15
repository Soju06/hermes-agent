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

from agent.message_sanitization import close_interrupted_tool_sequence
from agent.turn_recovery import _TRANSPORT_FAILURE_REASONS
from agent.turn_retry_state import TurnRetryState

logger = logging.getLogger("agent.conversation_loop")

# A transport failure faster than this died before the endpoint answered (refused/reset).
_INSTANT_FAILURE_SECONDS = 2.0


def track_instant_transport_failure(retry: TurnRetryState, classified: Any) -> None:
    """Extend the instant-failure streak on ``retry``, or reset it on any other outcome.

    ``attempt_started_at`` is stamped by the retry loop right before the API call; a slow
    timeout (or an unstamped attempt) resets the streak like a non-transport error does."""
    is_transport_failure = classified.reason in _TRANSPORT_FAILURE_REASONS
    if is_transport_failure and (time.time() - retry.attempt_started_at) < _INSTANT_FAILURE_SECONDS:
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
) -> Optional[dict[str, Any]]:
    """The failed-turn result once the instant-failure streak reaches the limit, else None.

    Runs after the eager fallback had its chance: a streak here means fallback either does not
    exist or already failed to absorb the outage."""
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
    agent._emit_status(f"❌ {msg}")
    logger.error(
        "Fail-fast after %d instant transport failures %s error=%s",
        retry.fast_transport_failures,
        agent._client_log_context(),
        api_error,
    )
    close_interrupted_tool_sequence(messages, f"[System: API call aborted — {msg}]")
    agent._persist_session(messages, conversation_history)
    return {
        "final_response": msg,
        "messages": messages,
        "api_calls": api_call_count,
        "completed": False,
        "failed": True,
        "error": msg,
        "failure_reason": classified.reason.value,
    }
