"""Route-aware outage fallback (model_routes).

An outage-shaped failure walks the CURRENT route's declared ``fallbacks:``
(healthy, non-self entries) before the global ``fallback_providers:`` chain, so
a dev-routed session degrades to another dev-tier member first. Called from
``chat_completion_helpers.try_activate_fallback``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.error_classifier import FailoverReason

logger = logging.getLogger(__name__)

OUTAGE_ROUTE_FALLBACK_REASONS = frozenset({
    FailoverReason.rate_limit,
    FailoverReason.billing,
    FailoverReason.upstream_rate_limit,
    FailoverReason.overloaded,
    FailoverReason.server_error,
})


def build_outage_route_fallback_chain(agent: Any) -> tuple[str, list[dict]]:
    """Return the current route's healthy fallback entries, in declaration order."""
    try:
        from agent.backend_identity import BackendIdentity, should_skip_candidate
        from hermes_cli.config import load_config
        from hermes_cli.model_routes import (
            _cfg_runtime_fallback,
            _model_matches,
            load_routes,
            provider_health,
            resolve_route,
        )

        cfg = load_config() or {}
        catalog = load_routes(cfg)
        current_model = str(getattr(agent, "model", "") or "").strip()
        spec = next(
            (
                candidate
                for candidate in catalog.routes.values()
                if any(
                    _model_matches(current_model, member)
                    for member in (candidate.accepted or (candidate.model,))
                )
            ),
            None,
        )
        if spec is None:
            return "", []

        # resolve_route owns route-health semantics. Its source tells us which
        # declared fallback is the first healthy candidate, so entries it has
        # already rejected are not retried by the live fallback path.
        resolved = resolve_route(spec.name, cfg, catalog=catalog)
        if not resolved:
            return spec.name, []
        first_index = 0
        source = str(resolved.get("source") or "")
        if source.startswith("fallback:"):
            try:
                first_index = max(int(source.split(":", 1)[1]) - 1, 0)
            except (TypeError, ValueError):
                first_index = 0

        current_ident = BackendIdentity.build(
            provider=getattr(agent, "provider", ""),
            model=getattr(agent, "model", ""),
            base_url=str(getattr(agent, "base_url", "") or ""),
        )
        chain = []
        for index, fallback in enumerate(spec.fallbacks):
            if index < first_index:
                continue
            healthy, _ = provider_health(
                fallback.provider,
                fallback.model,
                cfg=cfg,
                health=catalog.health,
            )
            if not healthy:
                continue
            runtime_cfg = _cfg_runtime_fallback(fallback.provider, cfg)
            entry = {
                "provider": fallback.provider,
                "model": fallback.model,
                "reasoning_effort": fallback.reasoning_effort or "",
                "base_url": str(runtime_cfg.get("base_url") or ""),
                "api_mode": str(runtime_cfg.get("api_mode") or ""),
            }
            candidate_ident = BackendIdentity.build(
                provider=entry["provider"],
                model=entry["model"],
                base_url=entry["base_url"],
            )
            if should_skip_candidate(candidate_ident, current_ident):
                logger.warning(
                    "Route fallback skip: route %s entry %s/%s resolves to "
                    "the current backend",
                    spec.name,
                    entry["provider"],
                    entry["model"],
                )
                continue
            chain.append(entry)
        return spec.name, chain
    except Exception:
        # Route-aware fallback is additive. Any config/import/resolution
        # failure leaves the established global fallback chain untouched.
        logger.debug("Route fallback unavailable; using global chain", exc_info=True)
        return "", []


def next_outage_route_fallback(agent: Any) -> Optional[dict]:
    """Advance the transient route-prefixed cursor for one outage walk."""
    if getattr(agent, "_outage_route_fallback_walk_active", False):
        chain = getattr(agent, "_outage_route_fallback_chain", [])
        index = int(getattr(agent, "_outage_route_fallback_index", 0) or 0)
    else:
        route_name, fresh_chain = build_outage_route_fallback_chain(agent)
        continuing = (
            bool(getattr(agent, "_fallback_activated", False))
            and getattr(agent, "_fallback_reason", None)
            in {reason.value for reason in OUTAGE_ROUTE_FALLBACK_REASONS}
            and route_name
            == getattr(agent, "_outage_route_fallback_name", "")
        )
        if continuing:
            chain = getattr(agent, "_outage_route_fallback_chain", fresh_chain)
            index = int(getattr(agent, "_outage_route_fallback_index", 0) or 0)
        else:
            chain = fresh_chain
            index = 0
            agent._outage_route_fallback_name = route_name
            agent._outage_route_fallback_chain = chain
            agent._outage_route_fallback_index = 0
        # Keep this true through recursive skip/unresolvable calls so an
        # exhausted route prefix falls into the global chain exactly once.
        agent._outage_route_fallback_walk_active = True

    if index >= len(chain):
        return None
    agent._outage_route_fallback_index = index + 1
    return chain[index]
