"""History anchoring for refusal-driven model hops."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def begin_refusal_turn(agent: Any) -> None:
    """Per-turn reset: the gateway caches agents across turns, so a clean-fork marker
    never outlives its turn; a recall quarantine staged by the gateway for THIS turn
    (``_refusal_recall_quarantine_pending``) becomes active once, then is consumed."""
    agent._refusal_clean_fork_active = False
    agent._refusal_recall_quarantine = bool(
        getattr(agent, "_refusal_recall_quarantine_pending", False)
    )
    agent._refusal_recall_quarantine_pending = False


def current_user_ordinal_from_tail(
    messages: list[dict] | None,
    current_turn_user_idx: int,
    *,
    keep_user_turns: int = 5,
) -> int | None:
    """Return the current user row's 1-based user ordinal from the tail.

    Synthetic recovery/nudge users may be appended after the real turn user,
    so "the last user" is not a safe anchor. ``keep_user_turns`` bounds this
    reverse search; a stale or out-of-range anchor fails closed with ``None``.
    """
    if not messages or not isinstance(current_turn_user_idx, int):
        return None
    if not 0 <= current_turn_user_idx < len(messages):
        return None
    if messages[current_turn_user_idx].get("role") != "user":
        return None
    try:
        limit = int(keep_user_turns)
    except (TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    ordinal = 1 + sum(
        1
        for message in messages[current_turn_user_idx + 1 :]
        if isinstance(message, dict) and message.get("role") == "user"
    )
    return ordinal if ordinal <= limit else None


def user_anchor_from_tail(
    messages: list[dict] | None,
    user_from_tail: int,
    *,
    keep_user_turns: int = 5,
) -> int | None:
    """Find a bounded Nth user-role anchor from the end of ``messages``."""
    if not messages:
        return None
    try:
        ordinal = int(user_from_tail)
        limit = int(keep_user_turns)
    except (TypeError, ValueError):
        return None
    if ordinal <= 0 or limit <= 0 or ordinal > limit:
        return None
    seen = 0
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, dict) and message.get("role") == "user":
            seen += 1
            if seen == ordinal:
                return index
    return None


def apply_refusal_clean_fork(
    agent, messages, api_messages, current_turn_user_idx: int,
) -> int:
    """Clean both durable and in-flight history after a refusal fallback.

    ``api_messages`` is a provider-shaped copy built before the retry loop, so
    cleaning only ``messages`` would still resend the refusal-contaminated
    request on an in-block ``continue``. Keep every completed earlier turn and
    truncate only rows generated after this turn's real user anchor.

    Returns the number of durable messages dropped, or zero when disabled.
    """
    try:
        from hermes_cli.config import load_config
        from hermes_cli.model_routes import load_routes

        refusal = load_routes(load_config() or {}).router.refusal
        clean_fork = bool(refusal.clean_fork)
        keep_user_turns = int(refusal.keep_user_turns)
    except Exception:
        logger.debug("Refusal clean-fork config load failed; using defaults", exc_info=True)
        clean_fork = True
        keep_user_turns = 5

    if not clean_fork:
        return 0

    user_from_tail = current_user_ordinal_from_tail(
        messages,
        current_turn_user_idx,
        keep_user_turns=keep_user_turns,
    )
    if user_from_tail is None:
        return 0
    api_anchor = user_anchor_from_tail(
        api_messages,
        user_from_tail,
        keep_user_turns=keep_user_turns,
    )
    if api_anchor is None:
        return 0

    original_count = len(messages)
    messages[:] = messages[: current_turn_user_idx + 1]
    api_messages[:] = api_messages[: api_anchor + 1]

    agent._session_messages = messages
    agent._refusal_clean_fork_active = True
    agent._refusal_recall_quarantine = True
    dropped = original_count - len(messages)
    agent._buffer_status(f"⚠️ Refusal fallback clean_fork=yes dropped={dropped}")
    return dropped
