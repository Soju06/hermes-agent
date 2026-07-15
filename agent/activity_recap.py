"""Current-turn activity snapshot for the gateway's LLM recap (``display.long_running_notifications: recap``).

``AIAgent.get_activity_recap_context`` forwards here; the gateway side that turns the snapshot into
a one-line status update is ``gateway/run_turn_activity_recap.py``.
"""

from __future__ import annotations

from typing import Any


def build_activity_recap_context(agent: Any) -> dict:
    """Snapshot of current-turn activity for the gateway's LLM recap.

    Read-only view over the live message list (GIL-consistent snapshot;
    called from the gateway event loop while the turn runs on a worker
    thread). Bracket-prefixed user messages are gateway notes, not the
    user's ask — skip them when picking the goal line.
    """
    msgs = list(getattr(agent, "_session_messages", None) or [])
    goal = ""
    for m in reversed(msgs):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str) and content.strip() and not content.lstrip().startswith("["):
            goal = content.strip()[:300]
            break
    recent_tools: list = []
    for m in reversed(msgs):
        if len(recent_tools) >= 5:
            break
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            try:
                if isinstance(tc, dict):
                    fn = tc.get("function", {}).get("name", "?")
                    args = tc.get("function", {}).get("arguments", "")
                else:
                    fn = tc.function.name
                    args = tc.function.arguments
            except Exception:
                continue
            recent_tools.append(f"{fn}({str(args)[:80]})")
            if len(recent_tools) >= 5:
                break
    summary = agent.get_activity_summary()
    return {
        "goal": goal,
        "recent_tools": list(reversed(recent_tools)),
        "current_tool": summary.get("current_tool"),
        "seconds_since_activity": summary.get("seconds_since_activity"),
        "last_activity_desc": summary.get("last_activity_desc"),
        "iteration": summary.get("api_call_count"),
        "max_iterations": summary.get("max_iterations"),
    }
