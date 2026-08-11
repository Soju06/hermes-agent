"""API-level refusal fallback onto PERMISSIVE model routes (model_routes).

A ``content_policy_blocked`` failover may walk the routes configured under
``model_routes.router.refusal`` (``dev_route`` / ``chat_route``, opt-in via
``enabled`` + ``api_fallback``) through the same activator as the generic
``fallback_providers`` chain. Called from
``chat_completion_helpers.try_activate_fallback``.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.error_classifier import FailoverReason

logger = logging.getLogger(__name__)


def is_content_policy_blocked(reason: Any) -> bool:
    """Accept the local enum and compatible reason objects from adapters."""
    reason_value = getattr(reason, "value", None)
    return (
        reason == FailoverReason.content_policy_blocked
        or reason_value == FailoverReason.content_policy_blocked
        or reason_value == FailoverReason.content_policy_blocked.value
    )


def current_runtime_is_dev_route(agent: Any, cfg: dict, catalog: Any) -> bool:
    """Whether refusal recovery should prefer the configured dev route."""
    provider = str(getattr(agent, "provider", "") or "").strip().lower()
    model = str(getattr(agent, "model", "") or "").strip().lower()
    if provider == "claude-lb" and (
        "claude-fable" in model or "claude-opus" in model
    ):
        return True

    try:
        from hermes_cli.model_routes import runtime_satisfies_route

        label_routes = catalog.router.label_route_map()
        runtime = {
            "provider": provider,
            "model": model,
            "base_url": str(getattr(agent, "base_url", "") or ""),
        }
        for label in ("SYSTEM_DEV", "FRONTEND_DEV"):
            route_name = str(label_routes.get(label) or "")
            if route_name and runtime_satisfies_route(
                runtime, route_name, cfg, catalog=catalog
            ):
                return True
    except Exception:
        logger.debug(
            "Refusal fallback: dev-route membership check failed", exc_info=True
        )
    return False


def build_refusal_fallback_chain(agent: Any) -> list[dict]:
    """Resolve the short PERMISSIVE route chain for API-level refusals."""
    try:
        from agent.backend_identity import BackendIdentity, should_skip_candidate
        from hermes_cli.config import load_config
        from hermes_cli.model_routes import (
            _cfg_runtime_fallback,
            load_routes,
            resolve_route,
        )

        cfg = load_config() or {}
        catalog = load_routes(cfg)
        refusal = catalog.router.refusal
        if not (refusal.enabled and refusal.api_fallback):
            return []

        if current_runtime_is_dev_route(agent, cfg, catalog):
            route_names = (refusal.dev_route, refusal.chat_route)
        else:
            route_names = (refusal.chat_route, refusal.dev_route)

        current_ident = BackendIdentity.build(
            provider=getattr(agent, "provider", ""),
            model=getattr(agent, "model", ""),
            base_url=str(getattr(agent, "base_url", "") or ""),
        )
        chain = []
        for route_name in route_names:
            if not str(route_name or "").strip():
                continue
            entry = resolve_route(route_name, cfg, catalog=catalog)
            if not entry:
                continue
            entry = dict(entry)
            entry["base_url"] = str(
                _cfg_runtime_fallback(entry.get("provider", ""), cfg).get(
                    "base_url", ""
                )
                or ""
            )
            candidate_ident = BackendIdentity.build(
                provider=entry.get("provider", ""),
                model=entry.get("model", ""),
                base_url=entry.get("base_url", ""),
            )
            if should_skip_candidate(candidate_ident, current_ident):
                logger.warning(
                    "Refusal fallback skip: route %s resolves to the current backend",
                    route_name,
                )
                continue
            chain.append(entry)
        return chain
    except Exception:
        # Refusal routing is an optional preference. Config or route-resolution
        # failures must leave the established fallback chain available.
        logger.debug("Refusal fallback route resolution failed", exc_info=True)
        return []


def walk_refusal_routes(agent: Any, reason: FailoverReason) -> bool:
    """Re-enter the activator with a temporary route-derived chain.

    Its index survives successful hops within the active refusal sequence, so
    repeated refusals progress DEV -> CHAT -> generic instead of rebuilding and
    cycling between PERMISSIVE routes. True when a PERMISSIVE hop activated.
    """
    if getattr(agent, "_refusal_fallback_walk_active", False):
        return False
    continuing_refusal = (
        bool(getattr(agent, "_fallback_activated", False))
        and getattr(agent, "_fallback_reason", None)
        == FailoverReason.content_policy_blocked.value
    )
    if continuing_refusal:
        refusal_chain = getattr(agent, "_refusal_fallback_chain", [])
        refusal_index = int(getattr(agent, "_refusal_fallback_index", 0) or 0)
    else:
        refusal_chain = build_refusal_fallback_chain(agent)
        refusal_index = 0
        agent._refusal_fallback_chain = refusal_chain
        agent._refusal_fallback_index = 0

    if refusal_index >= len(refusal_chain):
        return False
    normal_chain = agent._fallback_chain
    normal_index = agent._fallback_index
    cooldown_before = getattr(agent, "_rate_limited_until", 0)
    agent._fallback_chain = refusal_chain
    agent._fallback_index = refusal_index
    agent._refusal_fallback_walk_active = True
    try:
        if agent._try_activate_fallback(reason):
            return True
        # Exhausting only the temporary preference chain is not a full
        # fallback exhaustion; let the generic walk own any cross-turn
        # cooldown side effect.
        agent._rate_limited_until = cooldown_before
    finally:
        agent._refusal_fallback_index = agent._fallback_index
        agent._fallback_chain = normal_chain
        agent._fallback_index = normal_index
        agent._refusal_fallback_walk_active = False
    return False
