"""Current-turn activity snapshot for the gateway's LLM recap (``display.long_running_notifications: recap``).

``AIAgent.get_activity_recap_context`` forwards here; the gateway side that turns the snapshot into
a one-line status update is ``gateway/run_turn_activity_recap.py``.
"""

from __future__ import annotations

import re
from typing import Any


def _recap_tool_label(fn: str, args: str) -> str:
    """Semantic one-liner for a tool call — raw JSON args are noise."""
    import json as _json

    parsed = None
    try:
        parsed = _json.loads(args) if isinstance(args, str) else args
    except Exception:
        pass
    if isinstance(parsed, dict):
        if fn == "terminal" and parsed.get("command"):
            return f"terminal: {str(parsed['command'])[:70]}"
        for key in ("path", "file_path", "file"):
            if parsed.get(key):
                return f"{fn}: {str(parsed[key])[:70]}"
        if fn == "search_files" and (parsed.get("pattern") or parsed.get("query")):
            return f"search: {str(parsed.get('pattern') or parsed.get('query'))[:60]}"
        if parsed.get("action"):
            return f"{fn} {parsed['action']}"
        if parsed.get("query"):
            return f"{fn}: {str(parsed['query'])[:60]}"
    return f"{fn}({str(args)[:50]})"


def build_activity_recap_context(agent: Any) -> dict:
    """Snapshot of current-turn activity for the gateway's LLM recap.

    Read-only view over the live message list (GIL-consistent snapshot;
    called from the gateway event loop while the turn runs on a worker
    thread). Real gateway user messages are frequently wrapped in
    bracketed note/coordination prefixes — strip those rather than skip
    the message, and fall back to the bracketed text itself (inbox
    threads' whole goal IS the coordinator note) so the goal is never
    empty when any user text exists.
    """
    msgs = list(getattr(agent, "_session_messages", None) or [])
    goal = ""
    goal_fallback = ""
    for m in reversed(msgs):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        text = content.strip()
        if not goal_fallback:
            goal_fallback = text[:300]
        # Strip leading bracketed note blocks ("[Note: ...]\n\n" etc.).
        stripped = re.sub(r"^(\[[^\]]*\]\s*)+", "", text).strip()
        if stripped:
            goal = stripped[:300]
            break
    if not goal:
        goal = goal_fallback

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
            recent_tools.append(_recap_tool_label(fn, args))
            if len(recent_tools) >= 5:
                break

    # The agent's own narration is the strongest recap signal.
    last_say = ""
    for m in reversed(msgs):
        if (
            isinstance(m, dict)
            and m.get("role") == "assistant"
            and isinstance(m.get("content"), str)
            and m["content"].strip()
        ):
            last_say = " ".join(m["content"].strip().split())[:200]
            break
    last_result = ""
    for m in reversed(msgs):
        if (
            isinstance(m, dict)
            and m.get("role") == "tool"
            and isinstance(m.get("content"), str)
            and m["content"].strip()
        ):
            last_result = " ".join(m["content"].strip().split())[:150]
            break

    # Language hint from a wide conversation tail — the goal alone is
    # unreliable (bracket-wrapped English coordinator notes and injected
    # reminders in Korean conversations broke language mirroring in live
    # evaluation; post-interrupt snapshots have English-only tails).
    # The conversation language is defined by what the USER writes —
    # tool results and injected templates flood the tail with English.
    _tail_parts = [goal, last_say, goal_fallback]
    _seen_users = 0
    for m in reversed(msgs):
        if _seen_users >= 30:
            break
        if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str):
            _tail_parts.append(m["content"][:400])
            _seen_users += 1
    _tail_text = " ".join(_tail_parts)
    language_hint = (
        "Korean" if re.search(r"[\uac00-\ud7a3]", _tail_text) else ""
    )

    summary = agent.get_activity_summary()
    return {
        "goal": goal,
        "recent_tools": list(reversed(recent_tools)),
        "last_assistant_text": last_say,
        "last_tool_result": last_result,
        "language_hint": language_hint,
        "current_tool": summary.get("current_tool"),
        "seconds_since_activity": summary.get("seconds_since_activity"),
        "last_activity_desc": summary.get("last_activity_desc"),
        "iteration": summary.get("api_call_count"),
        "max_iterations": summary.get("max_iterations"),
    }
