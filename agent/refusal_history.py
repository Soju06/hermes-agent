"""History shaping for refusal-driven model hops."""

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


def clean_fork_messages(
    messages: list[dict] | None,
    *,
    keep_user_turns: int = 5,
) -> list[dict]:
    """Return leading system messages plus the last requested user turns.

    Assistant and tool messages are deliberately excluded so a model selected
    after a safety refusal does not inherit the prior model's refusal framing
    or tool-derived policy narrative.  The input list and its dictionaries are
    never mutated.
    """
    if not messages:
        return []

    try:
        user_limit = max(0, int(keep_user_turns))
    except (TypeError, ValueError):
        user_limit = 0

    leading_system: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            break
        leading_system.append(dict(message))

    users = [
        dict(message)
        for message in messages
        if isinstance(message, dict) and message.get("role") == "user"
    ]
    if user_limit == 0:
        users = []
    else:
        users = users[-user_limit:]

    return [*leading_system, *users]


def apply_refusal_clean_fork(agent, messages, api_messages) -> int:
    """Clean both durable and in-flight history after a refusal fallback.

    ``api_messages`` is a provider-shaped copy built before the retry loop, so
    cleaning only ``messages`` would still resend the refusal-contaminated
    request on an in-block ``continue``.  Keep the request's leading system
    message and the provider-shaped copies of the same surviving user turns.

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

    original_count = len(messages)
    cleaned_messages = clean_fork_messages(
        messages,
        keep_user_turns=keep_user_turns,
    )
    messages[:] = cleaned_messages

    # Prefill messages may also use the user role and sit immediately after
    # the system prompt.  Count surviving durable users, then take that many
    # provider-shaped users from the tail so prefills cannot displace the live
    # request while api_content/context-engine transformations are preserved.
    surviving_users = sum(
        1 for message in cleaned_messages if message.get("role") == "user"
    )
    api_messages[:] = clean_fork_messages(
        api_messages,
        keep_user_turns=surviving_users,
    )

    agent._session_messages = messages
    agent._refusal_clean_fork_active = True
    agent._refusal_recall_quarantine = True
    dropped = original_count - len(cleaned_messages)
    agent._buffer_status(f"⚠️ Refusal fallback clean_fork=yes dropped={dropped}")
    return dropped
