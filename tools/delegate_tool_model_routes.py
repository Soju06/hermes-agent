"""Model-route delegation (ADR-003 Phase 3a): the ``route`` parameter of ``delegate_task``.

When ``model_routes.routes`` declares at least one route, ``delegate_task`` grows a ``route`` enum
(top-level, and per task in ``tasks[]``) built from the catalog at ``get_definitions()`` time; with
no routes declared the schema and the spawn path are upstream's. Precedence for one task: its own
``route`` > the top-level ``route`` > ``delegation.default_route`` > the active delegation provider
(``delegation.backend``). ``default_route`` answers only for the DEFAULT backend: a session's
``/delegation`` pick or a configured non-default ``delegation.backend`` is an explicit backend choice,
and a session override beats config, so that backend answers unrouted tasks. A routed task runs on
the route's health-checked provider/model (the default → fallbacks walk of
``hermes_cli.model_routes.resolve_route``) and the route's ``reasoning_effort`` beats
``delegation.reasoning_effort``. An unknown route, or one whose whole chain is unhealthy, fails the
call with a tool error naming the declared routes: never a silent fallback.

A resolved route is handed to core in the delegation provider's route vocabulary
(``agent/delegation_provider.py``) and turned into a ``TaskRoute`` by the same code a provider's
answer takes (``tools/delegate_tool_routes.py``), so credentials, fallback inheritance and per-call
caching stay upstream's. The provider is only asked about the tasks no route claims, so a broken
``delegation.provider`` cannot fail a fully routed call. Internal ``credentials_cfg`` callers
(/review) bypass this module the way they bypass the provider.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("tools.delegate_tool")  # log-record parity with the origin module

# ``TaskRoute.backend`` of a routed task: its result entry reports ``{"backend": "model_routes", "label": <route>}``.
_ROUTE_BACKEND = "model_routes"


def _load_full_config() -> dict:
    """Load the FULL active config (not just the ``delegation`` block).

    Route resolution needs the top-level ``model_routes`` and ``providers`` sections. Loader preference
    mirrors ``tools.delegate_tool_config._load_config()`` so routes are read from the same config source as
    the rest of delegate_task. Read-only — do NOT mutate the returned dict."""
    if os.environ.get("HERMES_IGNORE_USER_CONFIG") != "1":
        try:
            from hermes_cli.config import load_config_readonly
            full = load_config_readonly()
            if isinstance(full, dict):
                return full
        except Exception:
            pass
    try:
        from cli import CLI_CONFIG
        return CLI_CONFIG if isinstance(CLI_CONFIG, dict) else {}
    except Exception:
        return {}


def _route_catalog_pairs() -> list[tuple]:
    """(name, description) pairs for declared model_routes; [] when dormant."""
    try:
        from hermes_cli.model_routes import route_catalog_for_schema
        return route_catalog_for_schema(_load_full_config())
    except Exception:
        logger.debug("delegate_task: route catalog unavailable", exc_info=True)
        return []


def _build_route_param_schema(route_pairs: list[tuple]) -> dict:
    """Schema for the ``route`` param — enum tracks the declared route names."""
    lines = "\n".join(
        f"{name}: {description}" if description else name
        for name, description in route_pairs
    )
    return {
        "type": "string",
        "enum": [name for name, _ in route_pairs],
        "description": "Delegation runtime route. " + lines,
    }


def inject_route_param(overrides_params: dict, description: str) -> str:
    """Add the ``route`` enum (top-level + per-task) to a rebuilt delegate_task schema and return the
    description to use. Injected ONLY when the config declares at least one valid route; otherwise both
    stay exactly as given (dormant)."""
    route_pairs = _route_catalog_pairs()
    if not route_pairs:
        return description
    route_schema = _build_route_param_schema(route_pairs)
    overrides_params["properties"]["route"] = route_schema
    # The shallow per-property copy may still share tasks["items"] with the static schema — rebuild it
    # before injecting the per-task route so DELEGATE_TASK_SCHEMA itself is never mutated.
    tasks_prop = overrides_params["properties"]["tasks"]
    items = dict(tasks_prop["items"])
    items["properties"] = dict(items["properties"])
    items["properties"]["route"] = {
        **route_schema,
        "description": "Per-task route override; wins over the top-level "
        "'route'. See top-level 'route' for the catalog.",
    }
    tasks_prop["items"] = items
    return description + (
        "\n- EXCEPTION to the model-pinning note above: this install "
        "declares delegation routes. Pass 'route' (top-level, or per-task "
        "in tasks[]; per-task wins) to run a subagent on a declared "
        "route's provider/model."
    )


def _declared_route(name: str, cfg: dict, full_cfg: dict, catalog: Any) -> dict[str, Any]:
    """A declared route as a provider-vocabulary route: the first healthy runtime of its default → fallbacks
    chain, its reasoning effort, and the ``delegation`` section's ``request_overrides``. Raises ValueError —
    surfaced to the model through ``tool_error`` so it can retry with a different route — when the route is
    not declared or its whole chain is unhealthy."""
    from hermes_cli.model_routes import resolve_route
    declared = ", ".join(catalog.routes) or "(none declared)"
    if not any(spec.name.strip().lower() == name.lower() for spec in catalog.routes.values()):
        raise ValueError(f"Unknown delegation route '{name}'. Declared routes: {declared}.")
    resolved = resolve_route(name, full_cfg, catalog=catalog)
    if resolved is None:
        raise ValueError(
            f"Delegation route '{name}' has no healthy runtime (its default and "
            f"every fallback failed the provider health check). Declared routes: "
            f"{declared}. Retry with a different route, or omit 'route' to use "
            f"the default delegation runtime."
        )
    route: dict[str, Any] = {"provider": resolved["provider"], "model": resolved["model"], "label": name}
    if resolved.get("reasoning_effort"):
        route["reasoning_effort"] = resolved["reasoning_effort"]
    if isinstance(cfg.get("request_overrides"), dict):
        route["request_overrides"] = cfg["request_overrides"]
    return route


def _default_route(cfg: dict[str, Any], parent_agent: Any) -> str:
    """``delegation.default_route`` while the calling session delegates on the default backend, else
    ``""``: a chosen backend (the session's ``/delegation`` pick, else ``delegation.backend``) answers."""
    from agent.delegation_backends import session_delegation_config
    from agent.delegation_registry import DEFAULT_BACKEND, configured_backend
    name = str(cfg.get("default_route") or "").strip()
    if name and configured_backend(session_delegation_config(cfg, parent_agent)) != DEFAULT_BACKEND:
        return ""
    return name


def _without_route(task: dict[str, Any]) -> dict[str, Any]:
    """The task as the delegation provider sees it: ``route`` is this module's parameter, never the provider's."""
    return {k: v for k, v in task.items() if k != "route"} if "route" in task else task


def resolve_routed_tasks(task_list: list[dict[str, Any]], cfg: dict[str, Any], parent_agent: Any,
                         top_route: Optional[str] = None) -> tuple[list[Any], Optional[str]]:
    """One ``TaskRoute`` per task, like ``tools.delegate_tool_routes.resolve_task_routes``: a task naming a model
    route (its own > ``top_route`` > ``delegation.default_route`` on the default backend) takes that route, every
    other task the active delegation provider's. ``([], error)`` when a named route cannot be used. Each distinct
    route resolves once."""
    from tools.delegate_tool_routes import _route_to_task_route, resolve_task_routes
    top = str(top_route or "").strip()
    default_route = _default_route(cfg, parent_agent)
    names = [str(task.get("route") or "").strip() or top or default_route for task in task_list]
    provider_tasks = [_without_route(task) for task in task_list]
    if not any(names):
        return resolve_task_routes(provider_tasks, cfg, parent_agent)

    from hermes_cli.model_routes import load_routes
    full_cfg = _load_full_config()
    catalog = load_routes(full_cfg)
    cache: dict[str, Any] = {}
    claimed: dict[str, Any] = {}
    try:
        for name in dict.fromkeys(n for n in names if n):
            claimed[name] = _route_to_task_route(
                _declared_route(name, cfg, full_cfg, catalog), parent_agent, cache, cfg, _ROUTE_BACKEND)
    except ValueError as exc:
        return [], str(exc)
    if all(names):
        return [claimed[name] for name in names], None
    # The provider answers for the whole list so its task indices stay the model's; claimed tasks take their route.
    routes, err = resolve_task_routes(provider_tasks, cfg, parent_agent)
    if err:
        return [], err
    return [claimed[name] if name else route for name, route in zip(names, routes)], None
