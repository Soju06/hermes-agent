"""Runtime-state republish at a compression split for ``agent.conversation_compression``.

When compression rotates the session id, a fresh ``runtime_state`` snapshot keyed by the NEW
session id (``task_id`` / ``parent_session_id`` = the archived parent) is published before later
gates observe the rotated id, so guardrail plugins keep their per-session runtime scope across
the split. In-place compaction keeps the id and publishes nothing.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

# Log-record parity with the origin module.
logger = logging.getLogger("agent.conversation_compression")


def _reasoning_effort(agent: Any) -> str:
    reasoning_config = getattr(agent, "reasoning_config", None)
    if not isinstance(reasoning_config, dict):
        return ""
    if reasoning_config.get("enabled") is False:
        return "none"
    return str(reasoning_config.get("effort") or "")


def publish_runtime_state_after_split(agent: Any, old_session_id: Optional[str]) -> None:
    """Invoke the ``runtime_state`` hook for the session a compression split rotated into."""
    if not old_session_id:
        return
    try:
        from hermes_cli.lifecycle import invoke_hook

        invoke_hook(
            "runtime_state",
            session_id=agent.session_id or "",
            task_id=old_session_id,
            state={
                "session_id": agent.session_id or "",
                "task_id": old_session_id,
                "model": getattr(agent, "model", "") or "",
                "provider": getattr(agent, "provider", "") or "",
                "base_url": getattr(agent, "base_url", "") or "",
                "api_mode": getattr(agent, "api_mode", "") or "",
                "platform": getattr(agent, "platform", "") or "",
                "reasoning_effort": _reasoning_effort(agent),
                "parent_session_id": old_session_id,
                "boundary_reason": "compression",
            },
        )
    except Exception:
        logger.debug("runtime_state hook after compression split failed", exc_info=True)
