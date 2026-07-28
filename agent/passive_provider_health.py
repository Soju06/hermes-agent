"""Passive provider health (model_routes).

Real completion traffic is the health signal for the model-routes resolver:
abandoning a provider for an outage-shaped reason files an unhealthy verdict
(so fresh route resolutions fail over without an active probe), and the next
live success on that provider clears it. See hermes_cli.model_routes —
provider_health() only probes as a recovery check on stale-unhealthy
verdicts.

Called from the completion success paths (``chat_completion_helpers``,
``chat_completion_nonstream``) and from ``try_activate_fallback``.
"""

from __future__ import annotations

import logging

from agent.error_classifier import FailoverReason

logger = logging.getLogger(__name__)

# FailoverReasons that mean "this PROVIDER cannot serve completions right
# now" — mirrors the probe's fail-open classification (5xx, 402/429/credit,
# connection failures). Deliberately excluded: auth (our credentials),
# content_policy_blocked (this prompt), context/format errors (this request),
# upstream_rate_limit (one model at an aggregator, not the provider).
PASSIVE_UNHEALTHY_REASONS = frozenset({
    FailoverReason.billing,
    FailoverReason.rate_limit,
    FailoverReason.overloaded,
    FailoverReason.server_error,
    FailoverReason.timeout,
})


def record_passive_provider_outcome(agent, healthy: bool, reason: str) -> None:
    """Best-effort verdict for the agent's CURRENT runtime; never raises."""
    try:
        from hermes_cli.model_routes import provider_key_for_runtime, record_provider_outcome

        key = provider_key_for_runtime(
            provider=getattr(agent, "provider", "") or "",
            base_url=getattr(agent, "base_url", "") or "",
        )
        if key:
            record_provider_outcome(key, healthy, reason)
    except Exception:
        logger.debug("passive provider health record failed", exc_info=True)


def note_provider_success(agent) -> None:
    """Completion succeeded on the current runtime — clear any unhealthy verdict.

    Runs on every successful API call, so it is gated to one os.stat in
    steady state (has_unhealthy_verdicts memo); config load + key mapping
    only happen while some provider is actually marked unhealthy.
    """
    try:
        from hermes_cli.model_routes import has_unhealthy_verdicts

        if not has_unhealthy_verdicts():
            return
    except Exception:
        return
    record_passive_provider_outcome(agent, True, "recovered (live completion succeeded)")
