"""Periodic todo-list reminder (Claude Code attachment parity).

Claude Code re-surfaces the todo list mid-conversation as a system-reminder attachment when
TodoWrite has gone unused for TURNS_SINCE_WRITE iterations, repeating at most every
TURNS_BETWEEN_REMINDERS. Without the equivalent, a plan captured at turn 3 of a 100-turn session
silently ages out of the model's attention and unfinished items get abandoned: the todo list only
ever re-entered context at compression boundaries.

Constants mirror Claude Code v2.1.226 (TURNS_SINCE_WRITE: 10, TURNS_BETWEEN_REMINDERS: 10); the
reminder text is CC's verbatim. The counters live on the agent: ``agent_init`` initialises them,
``turn_iteration_prep.prepare_iteration`` bumps both once per tool-loop iteration, and the
``todo_list`` inline executor (``agent/inline_tool_executors.py``) zeroes
``_todo_loop_iterations_since_write``. ``_maybe_inject_todo_reminder`` runs from
``prepare_iteration`` and from ``tool_executor._flush_session_db_after_tool_progress``, the same
two sites as the run-budget wrap-up.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agent.conversation_loop")  # same channel as the other loop notices

TODO_REMINDER_TURNS_SINCE_WRITE = 10
TODO_REMINDER_TURNS_BETWEEN = 10

TODO_REMINDER_HEADER = (
    "[Automatic reminder — not from the user]\n"
    "The todo tool hasn't been used recently. If you're working on tasks that "
    "would benefit from tracking progress, consider using the todo tool to "
    "track progress. Also consider cleaning up the todo list if it has become "
    "stale and no longer matches what you are working on. Only use it if it's "
    "relevant to the current work. This is just a gentle reminder - ignore if "
    "not applicable."
)


def _maybe_inject_todo_reminder(agent: Any, messages: list[dict[str, Any]]) -> bool:
    """Re-surface unfinished todos after a stretch of tool-loop iterations.

    Cache-safe delivery: appended to the NEWEST ``role:"tool"`` message, the same channel as the
    run-budget wrap-up, so role alternation and the prompt-cache prefix survive. Fires only when
    the todo store holds pending/in_progress items, the todo tool has not been called for
    ``TODO_REMINDER_TURNS_SINCE_WRITE`` loop iterations, and at least
    ``TODO_REMINDER_TURNS_BETWEEN`` iterations have passed since the previous reminder. Like
    ``_maybe_inject_run_budget_wrapup``, only the current (not yet DB-persisted) tool tail is
    mutable: an already-written row must never be rewritten, so the reminder rides the next
    checkpoint flush instead.
    """
    store = getattr(agent, "_todo_store", None)
    if store is None or not store.has_items():
        return False
    snapshot = store.format_for_injection()
    if not snapshot:
        # Nothing pending/in_progress — a fully completed list needs no nudge.
        return False
    since_write = getattr(agent, "_todo_loop_iterations_since_write", 0)
    since_reminder = getattr(agent, "_todo_loop_iterations_since_reminder", 0)
    if since_write < TODO_REMINDER_TURNS_SINCE_WRITE:
        return False
    if since_reminder < TODO_REMINDER_TURNS_BETWEEN:
        return False
    # Strip the compression header line — this is a live nudge, not a
    # post-compaction snapshot restore.
    from agent.context_compressor import _DB_PERSISTED_MARKER
    from tools.todo_tool import TODO_INJECTION_HEADER

    body = snapshot
    if body.startswith(TODO_INJECTION_HEADER):
        body = body[len(TODO_INJECTION_HEADER):].lstrip("\n")
    reminder = (
        f"{TODO_REMINDER_HEADER}\n\n"
        f"Here are the existing contents of your todo list:\n{body}"
    )
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, dict) and msg.get("role") == "tool":
            if msg.get(_DB_PERSISTED_MARKER):
                return False
            existing = msg.get("content", "")
            if isinstance(existing, str):
                msg["content"] = existing + f"\n\n{reminder}"
            else:
                # Multimodal content blocks — append a text block.
                try:
                    blocks = list(existing) if existing else []
                    blocks.append({"type": "text", "text": reminder})
                    msg["content"] = blocks
                except Exception:
                    return False
            agent._todo_loop_iterations_since_reminder = 0
            logger.debug(
                "Todo reminder injected (%d iterations since last todo write)",
                since_write,
            )
            return True
    return False
