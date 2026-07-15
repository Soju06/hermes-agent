"""LLM activity recap for ``display.long_running_notifications: recap``.

``GatewayTurnMixin._run_agent_notify_long_running`` asks this mixin for a one-line, present-tense
recap of what the running agent is doing instead of the terse "Working — N min" heartbeat. The
agent side of the snapshot is ``AIAgent.get_activity_recap_context`` (``agent/activity_recap.py``).
"""

from __future__ import annotations

import asyncio
import logging

# Log-record parity with the origin module.
logger = logging.getLogger("gateway.run")


class GatewayTurnActivityRecapMixin:
    async def _llm_activity_recap(self, agent, session_key: str):
        """One-line LLM recap of what the running agent is doing right now.

        Powers ``display.long_running_notifications: recap``. Uses the
        auxiliary LLM route (same rail as compression summaries) with a hard
        timeout; any failure falls back to the terse built-in heartbeat.
        Regenerates only when the activity context actually changed — a long
        wait on one tool reuses the cached line instead of re-billing.
        """
        try:
            ctx = agent.get_activity_recap_context()
        except Exception:
            return None
        cache = getattr(self, "_activity_recap_cache", None)
        if cache is None:
            cache = {}
            self._activity_recap_cache = cache
        ctx_key = hash((
            ctx.get("goal"),
            tuple(ctx.get("recent_tools") or ()),
            ctx.get("current_tool"),
            ctx.get("last_assistant_text"),
        ))
        cached = cache.get(session_key)
        if cached and cached[0] == ctx_key:
            return cached[1]

        def _generate():
            from agent.auxiliary_client import call_llm

            tools_str = "; ".join(ctx.get("recent_tools") or []) or "(none yet)"
            current = ctx.get("current_tool") or ctx.get("last_activity_desc") or "thinking"
            lang = ctx.get("language_hint")
            lang_rule = (
                f"Write the line in {lang}."
                if lang
                else "Write in the same language as the goal and the agent's own words."
            )
            extra = ""
            if ctx.get("last_assistant_text"):
                extra += f"Agent's own last words: {ctx['last_assistant_text']}\n"
            if ctx.get("last_tool_result"):
                extra += f"Last tool result (truncated): {ctx['last_tool_result']}\n"
            prompt = (
                "You are narrating an AI agent's progress to its user while a "
                "long turn runs. Reply with ONE line (max 100 chars), present "
                f"tense, no prefix/quotes. {lang_rule}\n"
                f"Goal: {ctx.get('goal') or '(unknown)'}\n"
                f"Recent tool calls (oldest first): {tools_str}\n"
                + extra +
                f"Currently: {current} "
                f"({int(ctx.get('seconds_since_activity') or 0)}s since last activity, "
                f"iteration {ctx.get('iteration')}/{ctx.get('max_iterations')})"
            )
            response = call_llm(
                task="activity_recap",
                main_runtime={
                    "model": getattr(agent, "model", None),
                    "provider": getattr(agent, "provider", None),
                    "base_url": getattr(agent, "base_url", None),
                    "api_key": getattr(agent, "api_key", None),
                    "api_mode": getattr(agent, "api_mode", None),
                },
                messages=[{"role": "user", "content": prompt}],
                max_tokens=80,
                timeout=8,
            )
            message = response.choices[0].message
            content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
            line = str(content or "").strip().splitlines()
            return line[0][:140].strip() if line else ""

        try:
            text = await asyncio.wait_for(asyncio.to_thread(_generate), timeout=10)
        except Exception as exc:
            logger.debug("activity recap generation failed: %s", exc)
            return None
        if not text:
            return None
        cache[session_key] = (ctx_key, text)
        return text
