"""Dynamic model router (ADR-003 Phase 2): the pre-dispatch routing stage of the inbound pipeline.

Bound onto ``GatewayRunner`` through ``GatewayInboundMixin``; ``_handle_message`` calls
``_hm_model_router_gate`` (which runs ``_model_router_stage``) after the running-agent PRIORITY block
and before idle slash dispatch, and ``_hm_admit_event`` takes ``router_pre_hook_text`` before the
``pre_gateway_dispatch`` hook.
``gateway.run`` internals are imported lazily inside function bodies (import cycle), so
``patch("gateway.run._load_gateway_config")`` keeps intercepting them at call time.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from typing import TYPE_CHECKING, Optional

from gateway.session import SessionSource

if TYPE_CHECKING:  # string annotations only
    from gateway.platforms.event import MessageEvent  # noqa: F401

# Log-record parity with the origin module.
logger = logging.getLogger("gateway.run")


def _model_router_mode() -> str:
    """Effective dynamic-model-router mode: "off" | "shadow" | "enforce" (ADR-003 Phase 2).

    Config-first (``model_routes.router.mode``) with an env escape hatch: ``HERMES_MODEL_ROUTER_MODE``
    WINS when set (mirrors the gateway.platform_connect_timeout bridge semantics). Kept to one cheap
    config-dict read so mode=off costs nothing per message — the router module is only imported once a
    non-off mode is active.
    """
    env = os.environ.get("HERMES_MODEL_ROUTER_MODE", "").strip().lower()
    if env:
        return env if env in ("shadow", "enforce") else "off"
    try:
        from gateway.run import _load_gateway_config
        cfg = _load_gateway_config()
        section = cfg.get("model_routes") if isinstance(cfg, dict) else None
        router = section.get("router") if isinstance(section, dict) else None
        raw = router.get("mode") if isinstance(router, dict) else None
    except Exception:
        return "off"
    mode = str(raw).strip().lower() if isinstance(raw, str) else ""
    return mode if mode in ("shadow", "enforce") else "off"


def router_pre_hook_text(event: "MessageEvent") -> str:
    """The message text the router classifies, snapshot before ``pre_gateway_dispatch`` runs.

    Plugin hooks may rewrite (or prepend to) ``event.text``, but the router must classify the same text
    the plugin-era classifier saw: injected advisories are label noise the bench dataset had to strip.
    An admission the drain executes keeps the snapshot its ingress committed (``restore_native``).
    ``_hm_admit_event`` sets the result on the post-hook event as ``_hermes_router_pre_hook_text``, a
    private attribute like the runtime override directives.
    """
    snapshot = getattr(event, "_hermes_router_pre_hook_text", None)
    return str(getattr(event, "text", None) or "") if snapshot is None else snapshot


class GatewayInboundModelRouterMixin:
    """Route evaluation and enforce-mode apply for the dynamic model router."""

    async def _hm_model_router_gate(
        self, event: "MessageEvent", source: SessionSource, session_key: str, is_internal: bool,
    ) -> None:
        """``_handle_message``'s router step for authorized, non-internal messages on the fresh-agent
        dispatch leg. Placed AFTER the running-agent PRIORITY block so steer/interrupt/stop messages
        against a running agent gain zero latency and no suspension point precedes the running-session
        check, and BEFORE slash-command dispatch so static rules still apply to slash commands (see
        ``_model_router_stage``). mode=off (the default) costs a single config check. Always fails
        open: routing must never block dispatch."""
        if is_internal:
            return
        mode = _model_router_mode()
        if mode == "off":
            return
        try:
            await self._model_router_stage(
                event, source, session_key, mode=mode,
                original_text=getattr(event, "_hermes_router_pre_hook_text", None),
            )
        except Exception:
            logger.warning("model router stage failed open", exc_info=True)

    def _model_router_runtime_snapshot(self, source: SessionSource, session_key: str) -> dict:
        """Non-secret {provider, model, base_url, reasoning_effort} snapshot.

        Equivalent of skill-gate's ``_current_gateway_runtime``: built from the gateway-owned session
        override / config resolution so the router can suppress pure no-op switches. Secrets (api_key)
        never enter the snapshot — it is embedded in the classifier prompt payload.

        Callers must take this snapshot ON THE EVENT LOOP (never inside asyncio.to_thread):
        ``_resolve_session_agent_runtime`` runs ``_rehydrate_session_model_override``, whose
        check-then-assign on ``_session_model_overrides`` is only safe because every writer of that
        dict runs loop-atomically between awaits. A read-only lookup (``record_notice=False``, as
        /status): the runner-wide pre-agent fallback notice is another turn's one-shot slot.
        """
        try:
            model, runtime_kwargs = self._resolve_session_agent_runtime(
                source=source, session_key=session_key, record_notice=False)
        except Exception:
            logger.debug("model router: runtime snapshot failed", exc_info=True)
            return {}
        snapshot: dict = {}
        if model:
            snapshot["model"] = model
        for name in ("provider", "base_url"):
            value = (runtime_kwargs or {}).get(name)
            if value:
                snapshot[name] = value
        # Effective reasoning effort (session override → per-model override for the EFFECTIVE model →
        # config), needed by runtime_satisfies_route's effort-aware membership (plugin parity with
        # runtime_matches_spec). Cheap: dict lookup + config read.
        try:
            reasoning = self._resolve_session_reasoning_config(
                source=source, session_key=session_key, model=model or "",
            )
        except Exception:
            logger.debug("model router: reasoning snapshot failed", exc_info=True)
            reasoning = None
        if isinstance(reasoning, dict):
            if reasoning.get("enabled") is False:
                snapshot["reasoning_effort"] = "none"
            else:
                effort = str(reasoning.get("effort") or "").strip()
                if effort:
                    snapshot["reasoning_effort"] = effort
        return snapshot

    async def _model_router_stage(
        self, event: "MessageEvent", source: SessionSource, session_key: str, *, mode: str,
        original_text: Optional[str] = None,
    ) -> None:
        """Evaluate (and in enforce mode apply) the dynamic model router.

        Runs AFTER the running-agent PRIORITY/interrupt/steer interception block, immediately before
        the idle slash-command dispatch and the fresh-agent leg. Messages the running turn consumes
        (a steer, an interrupt, /stop) are therefore NOT routed — a deliberate deviation from the
        skill-gate plugin, which classified them; the next fresh message routes instead. Expected diff
        class during shadow soak. The placement also means no router suspension point precedes the
        running-session check.

        With a session authority (every gateway boot) an ordinary message is admitted to the durable
        FIFO at ingress and this stage runs when the drain executes it, on the restored event: a
        follow-up queued behind a running turn IS routed, at the start of its own turn. ``original_text``
        is then the pre-hook snapshot the admission committed (``_hm_admit_event``), since the
        ``pre_gateway_dispatch`` hook ran once, at ingress.

        Evaluation order:

        0. a LOCAL session (CLI, TUI, Desktop served by this gateway) — return with zero work. It runs
           its frozen launch policy: ``_resolve_session_agent_runtime`` answers from the policy before
           any messaging override is read (``gateway/session_runtime_switch.py``), so an enforce apply
           would be a no-op logged as applied, repeated every turn. The router routes messaging
           sessions, the traffic its plugin-era classifier was benched on.
        1. empty/whitespace text — return with zero work (no catalog parse, no runtime snapshot, no
           thread hop, no log record).
        2. static rules match on the RAW text + source only; only a match takes the runtime snapshot
           and resolves/applies/logs. This path runs for slash commands too (plugin parity: skill-gate
           applies static runtime_overrides even for "/status" from a non-owner).
        3. no static match + slash command — return (the classifier never runs for slash commands;
           nothing is logged).
        4. plain text — runtime snapshot + LLM classifier path.

        The runtime snapshot is taken ON THE EVENT LOOP before any asyncio.to_thread hop (see
        ``_model_router_runtime_snapshot`` — it keeps ``_rehydrate_session_model_override``
        loop-atomic); only blocking work runs in the thread: the classifier HTTP call, health probes
        inside route resolution, switch_model credential resolution (enforce), and the decision-log
        append.

        shadow: full evaluation — static rules, classifier, hysteresis streak mutation, health-checked
        resolution — but NEVER applies a directive. Its only writes are the runner-owned streak dict
        and the decision log, so it can soak alongside a live skill-gate plugin without interfering.

        enforce: applies ``switch``/``downgrade_to_chat``/``refusal_switch``/``repromote_to_primary``
        directives the same way /model does (session override + store persist + agent eviction), and
        stamps ``applied: true/false`` on every decision record.
        """
        from gateway import model_router as _model_router
        from gateway.config import Platform
        from gateway.run import _load_gateway_config
        from hermes_cli.model_routes import load_routes as _load_model_routes

        if getattr(source, "platform", None) == Platform.LOCAL:  # (0) policy-bound: see docstring
            return
        # Route on the PRE-HOOK text: plugin hooks may have prepended advisories or rewritten
        # event.text before this stage runs, but the classifier was benched on (and the plugin-era
        # router classified) the original message — injected advisories are label noise.
        if original_text and original_text != getattr(event, "text", None):
            event = dataclasses.replace(event, text=original_text)
        raw_text = str(getattr(event, "text", None) or "")
        text = raw_text.strip()
        if not text:  # (1) zero work for empty/whitespace events
            return

        state = getattr(self, "_model_router_state", None)
        if state is None:
            state = {}
            self._model_router_state = state

        cfg = _load_gateway_config()
        catalog = _load_model_routes(cfg)

        # (2) Static-rule matching needs only text + source — no snapshot.
        matched = _model_router.match_static_rule(
            list(catalog.static_rules or []), text=raw_text, source_context=_model_router._source_dict(event),
        )
        if matched is None and text.startswith("/"):
            return  # (3) classifier never runs for slash; no log record

        # Runtime snapshot on the event loop — see docstring.
        runtime = self._model_router_runtime_snapshot(source, session_key)
        # A /model override the router did not write (typed /model, picker) is the owner's pick:
        # exempt from re-promotion (ADR-003 escape hatch). Read after the snapshot rehydrated it.
        override = self._session_model_overrides.get(session_key) or {}
        user_pick = bool(override) and not override.get("router_applied")

        if matched is not None:
            rule, rule_name = matched
            # Satisfaction check + route resolution can hit health probes (blocking urllib) — keep the
            # event loop free.
            decision = await asyncio.to_thread(
                _model_router.static_rule_decision,
                rule=rule, rule_name=rule_name, text=raw_text,
                session_key=_model_router._session_key(self.session_store, event),
                runtime=runtime, cfg=cfg, catalog=catalog, router=catalog.router, mode=mode, state=state,
                user_pick=user_pick,
            )
        else:
            # (4) The classifier is a blocking urllib call (up to timeout_ms) — keep the event loop free.
            decision = await asyncio.to_thread(
                _model_router.classifier_decision,
                event=event, session_store=self.session_store, runtime=runtime, cfg=cfg, catalog=catalog,
                router=catalog.router, mode=mode, state=state, complete_dev=None, user_pick=user_pick,
            )

        directive = decision.directive
        if mode == "enforce":
            # Log fidelity: every enforce-mode record carries applied true/false (false when nothing
            # was applied, switch_model failed, or the apply raised). Shadow records never carry it.
            decision.record["applied"] = False
        if (
            mode == "enforce"
            and directive
            and decision.outcome in (
                "switch", "downgrade_to_chat", "repromote_to_primary", "refusal_switch",
            )
        ):
            reasoning_effort = str(directive.get("reasoning_effort") or "")
            try:
                applied, reasoning_applied = await self._apply_model_router_directive(
                    session_key, directive, cfg, source=source,
                )
            except Exception:
                logger.warning("model router: applying directive %s failed", directive, exc_info=True)
                applied, reasoning_applied = False, False
            decision.record["applied"] = bool(applied)
            if reasoning_effort:
                decision.record["reasoning_applied"] = bool(reasoning_applied)
            if applied:
                logger.info(
                    "model router: %s session=%s -> route=%s model=%s (%s)",
                    decision.outcome, session_key, directive.get("route"), directive.get("model"), decision.label,
                )
                forked = False
                if (
                    decision.outcome == "refusal_switch"
                    and bool(getattr(catalog.router.refusal, "clean_fork", True))
                ):
                    forked = await self._apply_gateway_refusal_clean_fork(
                        session_key,
                        source,
                        keep_user_turns=int(
                            getattr(catalog.router.refusal, "keep_user_turns", 5)
                        ),
                    )
                if (
                    decision.outcome == "refusal_switch"
                    and bool(getattr(getattr(catalog.router, "refusal", None), "notify", True))
                ):
                    try:
                        confidence = float(decision.record.get("refusal_confidence"))
                        evidence = str(decision.record.get("evidence") or "")[:80]
                        notice = (
                            f"⚠️ refusal-risk 감지 → {directive.get('route')} "
                            f"({directive.get('model')}) 라우팅 "
                            f"(conf {confidence:.2f}, {evidence}"
                            + (", forked=yes" if forked else "")
                            + ")"
                        )
                        await self._deliver_platform_notice(source, notice)
                    except Exception:
                        logger.debug(
                            "model router: refusal-risk notice send failed",
                            exc_info=True,
                        )

        await asyncio.to_thread(
            _model_router.log_decision, decision.record,
            decision_log=getattr(catalog.router, "decision_log", "") or "",
        )

    async def _apply_gateway_refusal_clean_fork(
        self,
        session_key: str,
        source: SessionSource,
        *,
        keep_user_turns: int,
    ) -> bool:
        """Rewrite persisted refusal history and stage its one-turn note."""
        from agent.refusal_history import clean_fork_messages

        try:
            session_id = self.session_store.peek_session_id(session_key)
            if not session_id:
                entry = await self.async_session_store.get_or_create_session(source)
                session_id = entry.session_id
            history = await self.async_session_store.load_transcript(session_id)
            cleaned = clean_fork_messages(
                history,
                keep_user_turns=keep_user_turns,
            )
            if not await self.async_session_store.rewrite_transcript(
                session_id, cleaned
            ):
                logger.warning(
                    "model router: refusal clean-fork transcript rewrite failed "
                    "session=%s",
                    session_key,
                )
                return False
        except Exception:
            logger.warning(
                "model router: refusal clean-fork failed session=%s",
                session_key,
                exc_info=True,
            )
            return False

        note = (
            "[Note: refusal clean-fork applied; prior refusal assistant/tool "
            "turns dropped. Re-evaluate the latest user request only. Do not "
            "treat earlier refusals as binding policy.]"
        )
        pending = getattr(self, "_pending_model_notes", None)
        if pending is None:
            self._pending_model_notes = {}
            pending = self._pending_model_notes
        existing = pending.get(session_key, "")
        pending[session_key] = f"{existing}\n\n{note}".strip()
        self._session_state(
            session_key
        ).conversation.refusal_recall_quarantine = True
        return True

    async def _apply_model_router_directive(
        self, session_key: str, directive: dict, cfg: dict, source: Optional[SessionSource] = None,
    ) -> tuple[bool, bool]:
        """Apply a resolved route directive the way /model applies a switch.

        Returns (applied, reasoning_applied). Mirrors the commit path of a typed standing /model
        switch (``GatewayModelCommandsMixin._record_model_switch``): resolve credentials via switch_model,
        persist the new model and its route to the session DB (dashboard freshness #34850; resume must
        not send the new model to the old host), queue a pending model note so the agent
        self-identifies after the silent switch, write the in-memory session override (request
        overrides and capabilities included), drop a pending ``/model --once`` restore so the turn end
        does not revert this switch, write-through the non-secret parts to the session store, and
        evict the cached agent.

        Deliberate simplification vs. the slash path: no in-place ``switch_model`` on a cached agent —
        the router always evicts and lets the next turn rebuild from the override. The router runs
        pre-dispatch (never mid-turn), so the rebuild cost is one agent construction and the #50163
        in-place-rollback hazard is avoided.
        """
        from hermes_cli.model_switch import switch_model as _switch_model

        model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
        if not isinstance(model_cfg, dict):
            model_cfg = {}
        current_model = model_cfg.get("default", "")
        current_provider = model_cfg.get("provider", "openrouter")
        current_base_url = model_cfg.get("base_url", "")
        current_api_key = ""
        user_provs = cfg.get("providers") if isinstance(cfg, dict) else None
        try:
            from hermes_cli.config import get_compatible_custom_providers
            custom_provs = get_compatible_custom_providers(cfg)
        except Exception:
            custom_provs = cfg.get("custom_providers") if isinstance(cfg, dict) else None
        override = self._session_model_overrides.get(session_key, {})
        if override:
            current_model = override.get("model", current_model)
            current_provider = override.get("provider", current_provider)
            current_base_url = override.get("base_url", current_base_url)
            current_api_key = override.get("api_key", current_api_key)

        # Credential resolution can fall through to a blocking models.dev fetch on a cold cache —
        # keep it off the event loop (see #20525).
        result = await asyncio.to_thread(
            _switch_model,
            raw_input=str(directive.get("model") or ""),
            current_provider=current_provider, current_model=current_model,
            current_base_url=current_base_url, current_api_key=current_api_key,
            is_global=False, explicit_provider=str(directive.get("provider") or ""),
            user_providers=user_provs, custom_providers=custom_provs,
        )
        if not result.success:
            logger.warning(
                "model router: switch to %s@%s failed: %s",
                directive.get("model"), directive.get("provider"), result.error_message,
            )
            return False, False

        # Persist the new model to the session DB so the dashboard shows the updated model — same
        # persist the slash path performs (#34850).
        _store = getattr(self, "persistence", None)
        if _store is not None and source is not None:
            try:
                _sess_entry = await self.async_session_store.get_or_create_session(source)
                await _store.aio.sessions.update_model(
                    _sess_entry.session_id, result.new_model, provider=result.target_provider,
                    base_url=result.base_url, api_mode=result.api_mode,
                )
            except Exception:
                logger.debug("model router: persist model switch to session DB failed", exc_info=True)

        # Queue a note for the next agent turn so the model self-identifies correctly after the
        # silent switch (mirrors the /model note).
        _route_name = str(directive.get("route") or "")
        _route_reason = str(directive.get("reason") or "").strip()
        if not hasattr(self, "_pending_model_notes"):
            self._pending_model_notes = {}
        self._pending_model_notes[session_key] = (
            f"[Note: model was just switched from {current_model} to "
            f"{result.new_model} by the model router (route '{_route_name}'"
            + (f": {_route_reason}" if _route_reason and _route_reason != _route_name else "")
            + "). Adjust your self-identification accordingly.]"
        )

        self._session_model_overrides[session_key] = {
            "model": result.new_model, "provider": result.target_provider, "api_key": result.api_key,
            "base_url": result.base_url, "api_mode": result.api_mode,
            "owns_credentials": result.alias_endpoint_applied,
            "request_overrides": dict(result.request_overrides or {}),
            "capabilities": dict(result.runtime_capabilities or {}),
            # Tells this switch from a user's /model pick (the stage's re-promotion exemption); the
            # store persists it, and a later /model replaces the record without it.
            "router_applied": True,
        }
        if (_otr_state := self._peek_session_state(session_key)) is not None:  # a standing switch, as a typed /model
            _otr_state.conversation.one_turn_restore = None
        # Mark this session as freshly router-applied so the was_auto_reset conversation-boundary
        # cleanup later in this same inbound turn does not silently revert the switch (#48031 class)
        # — see _consume_auto_reset_boundary, which consumes the flag. A dict (not a set) so the
        # _clear_conversation_scope funnel pops it at every other conversation boundary automatically.
        if not hasattr(self, "_model_router_fresh_applies"):
            self._model_router_fresh_applies = {}
        self._model_router_fresh_applies[session_key] = True
        # Write-through the non-secret parts so the override survives a gateway restart (same as
        # the /model path).
        try:
            await self.async_session_store.set_model_override(session_key, self._session_model_overrides[session_key])
        except Exception:
            logger.debug("model router: persist session override failed", exc_info=True)

        reasoning_applied = False
        effort = str(directive.get("reasoning_effort") or "")
        if effort:
            from hermes_constants import parse_reasoning_effort
            parsed = parse_reasoning_effort(effort)
            if parsed is not None:
                self._set_session_reasoning_override(session_key, parsed)
                reasoning_applied = True

        # Evict cached agent so the next turn creates a fresh agent from the override (same
        # invalidation the /model path performs).
        self._evict_cached_agent(session_key)
        return True, reasoning_applied
