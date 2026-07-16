"""Model routing catalog — ADR-003 Phase 1.

Purpose-based route catalog: each route in ``model_routes.routes`` maps a
purpose name (``dev``, ``chat``, …) to a concrete runtime (provider/model/
reasoning_effort) plus an ordered, health-checked fallback chain.

Phase 1 scope is the config schema, loader/validation, resolver, and
provider health probing only.  Health-probe semantics are ported from the
skill-gate plugin's ``runtime_catalog.py`` and are deliberately fail-open:
only signals that indicate the PROVIDER cannot serve completions (credit/
quota exhaustion, 402/429, 5xx, connection failures) count as unhealthy;
auth-scoped 401/403 (or a malformed probe 400) are treated as healthy so a
probe defect can never freeze routing.

``static_rules`` are parsed and validated here but NOT matched or enforced —
condition semantics land in Phase 2.
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

try:
    import fcntl
except ImportError:  # pragma: no cover — non-POSIX; merge-on-write still applies
    fcntl = None  # type: ignore[assignment]
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hermes_constants import get_hermes_home, parse_reasoning_effort, VALID_REASONING_EFFORTS
from hermes_cli.config import ConfigIssue, get_compatible_custom_providers, load_config
from utils import atomic_json_write

logger = logging.getLogger(__name__)

_urlopen = urllib.request.urlopen  # test seam


def _now() -> float:  # test seam
    return time.time()


# =============================================================================
# Constants
# =============================================================================

DEFAULT_OK_TTL_SECONDS = 300.0
DEFAULT_FAIL_TTL_SECONDS = 120.0
DEFAULT_PROBE_TIMEOUT_SECONDS = 2.5
_HEALTH_CACHE_FILENAME = "model_route_health.json"  # under get_hermes_home()/"state"/
_CREDIT_SNIFF_KEYWORDS = ("credit", "insufficient", "quota", "billing")
_HEALTH_ENV = "HERMES_MODEL_ROUTES_HEALTH"
_HEALTH_TEST_ENV = "HERMES_MODEL_ROUTES_HEALTH_TEST"

_SECTION_KEYS = {"routes", "health", "static_rules"}
_ROUTE_KEYS = {"description", "provider", "model", "reasoning_effort", "accepted", "fallbacks"}
_FALLBACK_KEYS = {"provider", "model", "reasoning_effort"}
_HEALTH_KEYS = {"enabled", "cache_path", "ok_ttl_seconds", "fail_ttl_seconds", "probe_timeout_seconds"}
_HEALTH_NUMERIC_KEYS = ("ok_ttl_seconds", "fail_ttl_seconds", "probe_timeout_seconds")
_RULE_KEYS = {"route", "when", "reason"}


# =============================================================================
# Data types
# =============================================================================


@dataclass(frozen=True)
class FallbackSpec:
    provider: str
    model: str
    reasoning_effort: str = ""  # "" = unspecified (NOT inherited from the route)


@dataclass(frozen=True)
class RouteSpec:
    name: str  # as declared in YAML
    description: str  # "" if absent (warning issued)
    provider: str
    model: str
    reasoning_effort: str = ""  # "" = unspecified
    accepted: Tuple[str, ...] = ()  # model ids; empty → legacy membership
    fallbacks: Tuple["FallbackSpec", ...] = ()


@dataclass(frozen=True)
class HealthConfig:
    enabled: bool = True
    cache_path: str = ""  # "" → get_hermes_home()/"state"/model_route_health.json
    ok_ttl_seconds: float = DEFAULT_OK_TTL_SECONDS
    fail_ttl_seconds: float = DEFAULT_FAIL_TTL_SECONDS
    probe_timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS

    def resolved_cache_path(self) -> Path:
        if self.cache_path:
            return Path(self.cache_path).expanduser()
        return get_hermes_home() / "state" / _HEALTH_CACHE_FILENAME


@dataclass
class RouteCatalog:
    # Only VALID routes; declaration order preserved (dict insertion order).
    routes: Dict[str, RouteSpec] = field(default_factory=dict)
    health: HealthConfig = field(default_factory=HealthConfig)
    static_rules: List[Dict[str, Any]] = field(default_factory=list)  # parse-only in Phase 1
    issues: List[ConfigIssue] = field(default_factory=list)


# =============================================================================
# Matching helpers (ported from skill-gate runtime_catalog.py)
# =============================================================================


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _model_alias_candidates(model: str) -> List[str]:
    candidates = [model]
    dotted_version = re.sub(r"(?<=\d)\.(?=\d)", "-", model)  # dots between digits only
    if dotted_version != model:
        candidates.append(dotted_version)
    all_dots = model.replace(".", "-")
    if all_dots != model:
        candidates.append(all_dots)
    return list(dict.fromkeys(candidates))


def _model_matches(current: Any, expected: Any) -> bool:
    # DIRECTIONAL: only the CURRENT (live runtime) model is alias-expanded —
    # catalog should declare dash forms (claude-opus-4-8 matches live
    # claude-opus-4.8, not vice versa).
    target = _norm(expected)
    if not target:
        return True
    current_text = _norm(current)
    if current_text == target:
        return True
    return target in {_norm(candidate) for candidate in _model_alias_candidates(current_text)}


# =============================================================================
# Loader / validation
# =============================================================================


def _known_provider_names(cfg: Dict[str, Any]) -> set:
    """Names a route/fallback ``provider`` may legally reference.

    Union of the ``providers:`` dict keys/names (plus legacy
    ``custom_providers``) and the built-in canonical provider ids/aliases —
    built-ins (anthropic, openrouter, …) are credential-resolvable without a
    ``providers:`` entry, so rejecting them would be wrong.
    """
    from hermes_cli.runtime_provider import _normalize_custom_provider_name

    names: set = set()
    providers = cfg.get("providers")
    if isinstance(providers, dict):
        for key, entry in providers.items():
            names.add(_normalize_custom_provider_name(str(key)))
            if isinstance(entry, dict):
                raw_name = entry.get("name")
                if isinstance(raw_name, str) and raw_name.strip():
                    names.add(_normalize_custom_provider_name(raw_name))
    try:
        for entry in get_compatible_custom_providers(cfg):
            for name_key in ("name", "provider_key"):
                value = entry.get(name_key)
                if isinstance(value, str) and value.strip():
                    names.add(_normalize_custom_provider_name(value))
    except Exception:
        logger.debug("model_routes: custom provider enumeration failed", exc_info=True)
    try:
        from hermes_cli.models import _KNOWN_PROVIDER_NAMES  # heavy module — deferred

        names |= {_norm(name) for name in _KNOWN_PROVIDER_NAMES}
    except Exception:
        logger.debug("model_routes: built-in provider names unavailable", exc_info=True)
    names.discard("")
    return names


def _declared_provider_models(cfg: Dict[str, Any], provider_norm: str) -> Optional[Dict[str, Any]]:
    """Return the matched ``providers:`` entry's ``models:`` mapping, if any."""
    from hermes_cli.runtime_provider import _normalize_custom_provider_name

    providers = cfg.get("providers")
    if not isinstance(providers, dict):
        return None
    for key, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        entry_names = {_normalize_custom_provider_name(str(key))}
        raw_name = entry.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            entry_names.add(_normalize_custom_provider_name(raw_name))
        if provider_norm not in entry_names:
            continue
        models = entry.get("models")
        if isinstance(models, dict) and models:
            return models
        if isinstance(models, list) and models:
            return {str(m): {} for m in models if isinstance(m, str) and m.strip()}
        return None
    return None


def _validate_effort(value: Any) -> Tuple[bool, str]:
    """Return (valid, normalized_effort). "" and "none"/valid levels pass.

    YAML 1.1 booleans mirror ``parse_reasoning_effort`` semantics: ``off``/
    ``no``/``false`` (bool False) means reasoning disabled ("none"); ``on``/
    ``yes``/``true`` (bool True) is treated as unspecified, same as omitting
    the key — never a route-dropping error.
    """
    if value is None or value is True:
        return True, ""
    if value is False:
        return True, "none"
    if not isinstance(value, str):
        return False, ""
    text = value.strip()
    if not text:
        return True, ""
    if parse_reasoning_effort(text) is None:
        return False, text
    return True, text.lower()


def _effort_hint() -> str:
    return "Use one of: " + "|".join(VALID_REASONING_EFFORTS) + "|none, or omit to leave unset"


def _parse_fallback(
    route_name: str,
    index: int,
    item: Any,
    cfg: Dict[str, Any],
    known_providers: set,
    issues: List[ConfigIssue],
) -> Optional[FallbackSpec]:
    prefix = f"model_routes: route '{route_name}' fallback #{index}"
    if not isinstance(item, dict):
        issues.append(ConfigIssue(
            "error",
            f"{prefix}: must be a mapping (got {type(item).__name__})",
            "Each fallback needs at least: provider, model",
        ))
        return None
    for key in sorted(set(item) - _FALLBACK_KEYS):
        issues.append(ConfigIssue(
            "warning",
            f"{prefix}: unknown key '{key}' ignored",
            f"Supported fallback keys: {', '.join(sorted(_FALLBACK_KEYS))}",
        ))
    provider = item.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        issues.append(ConfigIssue(
            "error",
            f"{prefix}: missing 'provider'",
            "Add: provider: <name declared under providers: or a built-in id>",
        ))
        return None
    provider = provider.strip()
    from hermes_cli.runtime_provider import _normalize_custom_provider_name

    if _normalize_custom_provider_name(provider) not in known_providers:
        issues.append(ConfigIssue(
            "error",
            f"{prefix}: unknown provider '{provider}'",
            "Declare it under providers: in config.yaml (or use a built-in provider id)",
        ))
        return None
    model = item.get("model")
    if not isinstance(model, str) or not model.strip():
        issues.append(ConfigIssue(
            "error",
            f"{prefix}: missing 'model'",
            "Add: model: <model-id>",
        ))
        return None
    effort_ok, effort = _validate_effort(item.get("reasoning_effort"))
    if not effort_ok:
        issues.append(ConfigIssue(
            "error",
            f"{prefix}: invalid reasoning_effort {item.get('reasoning_effort')!r}",
            _effort_hint(),
        ))
        return None
    return FallbackSpec(provider=provider, model=model.strip(), reasoning_effort=effort)


def _parse_route(
    name: str,
    entry: Any,
    cfg: Dict[str, Any],
    known_providers: set,
    issues: List[ConfigIssue],
) -> Optional[RouteSpec]:
    prefix = f"model_routes: route '{name}'"
    if not isinstance(entry, dict):
        issues.append(ConfigIssue(
            "error",
            f"{prefix}: must be a mapping (got {type(entry).__name__})",
            "Each route needs at least: provider, model",
        ))
        return None

    has_error = False
    for key in sorted(set(entry) - _ROUTE_KEYS):
        issues.append(ConfigIssue(
            "warning",
            f"{prefix}: unknown key '{key}' ignored",
            f"Supported route keys: {', '.join(sorted(_ROUTE_KEYS))}",
        ))

    from hermes_cli.runtime_provider import _normalize_custom_provider_name

    provider = entry.get("provider")
    provider_norm = ""
    if not isinstance(provider, str) or not provider.strip():
        issues.append(ConfigIssue(
            "error",
            f"{prefix}: missing 'provider'",
            "Add: provider: <name declared under providers: or a built-in id>",
        ))
        has_error = True
        provider = ""
    else:
        provider = provider.strip()
        provider_norm = _normalize_custom_provider_name(provider)
        if provider_norm not in known_providers:
            issues.append(ConfigIssue(
                "error",
                f"{prefix}: unknown provider '{provider}'",
                "Declare it under providers: in config.yaml (or use a built-in provider id)",
            ))
            has_error = True

    model = entry.get("model")
    if not isinstance(model, str) or not model.strip():
        issues.append(ConfigIssue(
            "error",
            f"{prefix}: missing 'model'",
            "Add: model: <model-id>",
        ))
        has_error = True
        model = ""
    else:
        model = model.strip()
        if not has_error and provider_norm:
            declared = _declared_provider_models(cfg, provider_norm)
            if declared and not any(_model_matches(model, key) for key in declared):
                issues.append(ConfigIssue(
                    "warning",
                    f"{prefix}: model '{model}' is not in provider '{provider}' declared models",
                    "Check for a typo, or add the model under the provider's models: mapping",
                ))

    effort_ok, effort = _validate_effort(entry.get("reasoning_effort"))
    if not effort_ok:
        issues.append(ConfigIssue(
            "error",
            f"{prefix}: invalid reasoning_effort {entry.get('reasoning_effort')!r}",
            _effort_hint(),
        ))
        has_error = True

    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        issues.append(ConfigIssue(
            "warning",
            f"{prefix}: missing 'description'",
            "Add a short purpose description — later phases surface it in tool schemas",
        ))
        description = ""
    else:
        description = description.strip()

    accepted: Tuple[str, ...] = ()
    raw_accepted = entry.get("accepted")
    if raw_accepted is not None:
        if not isinstance(raw_accepted, list):
            issues.append(ConfigIssue(
                "error",
                f"{prefix}: 'accepted' must be a list of model-id strings "
                f"(got {type(raw_accepted).__name__})",
                "Change to:\n  accepted:\n    - <model-id>",
            ))
            has_error = True
        else:
            items: List[str] = []
            for i, item in enumerate(raw_accepted, 1):
                if not isinstance(item, str) or not item.strip():
                    issues.append(ConfigIssue(
                        "error",
                        f"{prefix}: accepted #{i} must be a non-empty model-id string",
                        "List plain model ids (dash forms match live dotted models)",
                    ))
                    has_error = True
                    continue
                items.append(item.strip())
            accepted = tuple(items)

    fallbacks: Tuple[FallbackSpec, ...] = ()
    raw_fallbacks = entry.get("fallbacks")
    if raw_fallbacks is not None:
        if not isinstance(raw_fallbacks, list):
            issues.append(ConfigIssue(
                "error",
                f"{prefix}: 'fallbacks' must be a list (got {type(raw_fallbacks).__name__})",
                "Change to:\n  fallbacks:\n    - provider: <name>\n      model: <model-id>",
            ))
            has_error = True
        else:
            parsed: List[FallbackSpec] = []
            for i, item in enumerate(raw_fallbacks, 1):
                fb = _parse_fallback(name, i, item, cfg, known_providers, issues)
                if fb is None:
                    # A broken chain is worse than no route.
                    has_error = True
                    continue
                parsed.append(fb)
            fallbacks = tuple(parsed)

    if has_error:
        return None
    return RouteSpec(
        name=name,
        description=description,
        provider=provider,
        model=model,
        reasoning_effort=effort,
        accepted=accepted,
        fallbacks=fallbacks,
    )


def _parse_routes(raw: Any, cfg: Dict[str, Any], issues: List[ConfigIssue]) -> Dict[str, RouteSpec]:
    routes: Dict[str, RouteSpec] = {}
    if raw is None:
        return routes
    if not isinstance(raw, dict):
        issues.append(ConfigIssue(
            "error",
            f"model_routes: 'routes' must be a mapping (got {type(raw).__name__})",
            "Change to:\n  routes:\n    <route-name>:\n      provider: <name>\n      model: <model-id>",
        ))
        return routes

    # YAML silently merges exact duplicate keys, so duplicates are only
    # detectable as case-insensitive collisions (dev vs DEV).
    by_lower: Dict[str, List[str]] = {}
    for name in raw:
        by_lower.setdefault(_norm(name), []).append(str(name))
    collided: set = set()
    for lowered, group in by_lower.items():
        if len(group) > 1:
            collided.add(lowered)
            issues.append(ConfigIssue(
                "error",
                f"model_routes: route names {sorted(group)} collide case-insensitively — all dropped",
                "Route lookup is case-insensitive; keep exactly one spelling per route",
            ))

    known_providers = _known_provider_names(cfg)
    for name, entry in raw.items():
        if _norm(name) in collided:
            continue
        spec = _parse_route(str(name), entry, cfg, known_providers, issues)
        if spec is not None:
            routes[spec.name] = spec
    return routes


def _parse_health(raw: Any, issues: List[ConfigIssue]) -> HealthConfig:
    if raw is None:
        return HealthConfig()
    if not isinstance(raw, dict):
        issues.append(ConfigIssue(
            "warning",
            f"model_routes: 'health' must be a mapping (got {type(raw).__name__}) — defaults used",
            f"Supported health keys: {', '.join(sorted(_HEALTH_KEYS))}",
        ))
        return HealthConfig()

    kwargs: Dict[str, Any] = {}
    for key in sorted(set(raw) - _HEALTH_KEYS):
        issues.append(ConfigIssue(
            "warning",
            f"model_routes: unknown key '{key}' under health ignored",
            f"Supported health keys: {', '.join(sorted(_HEALTH_KEYS))}",
        ))
    if "enabled" in raw:
        enabled = raw["enabled"]
        if isinstance(enabled, bool):
            kwargs["enabled"] = enabled
        else:
            # bool("false") is True — silently coercing would keep probing
            # enabled against the author's intent, so warn + default instead
            # (same treatment as cache_path / the numeric keys).
            issues.append(ConfigIssue(
                "warning",
                f"model_routes: health.enabled must be a boolean "
                f"(got {enabled!r}) — default (true) used",
                "Use an unquoted YAML boolean: enabled: false",
            ))
    if "cache_path" in raw:
        cache_path = raw["cache_path"]
        if isinstance(cache_path, str):
            kwargs["cache_path"] = cache_path.strip()
        else:
            issues.append(ConfigIssue(
                "warning",
                f"model_routes: health.cache_path must be a string "
                f"(got {type(cache_path).__name__}) — default used",
                'Use "" for the default <hermes home>/state/model_route_health.json',
            ))
    for key in _HEALTH_NUMERIC_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            kwargs[key] = float(value)
        else:
            issues.append(ConfigIssue(
                "warning",
                f"model_routes: health.{key} must be a number > 0 ({value!r}) — default used",
                f"Example: {key}: {getattr(HealthConfig(), key)}",
            ))
    return HealthConfig(**kwargs)


def _parse_static_rules(
    raw: Any,
    routes: Dict[str, RouteSpec],
    issues: List[ConfigIssue],
) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        issues.append(ConfigIssue(
            "error",
            f"model_routes: 'static_rules' must be a list (got {type(raw).__name__})",
            "Change to:\n  static_rules:\n    - route: <route-name>\n      when: {<condition>: <value>}",
        ))
        return []

    valid_names = {_norm(name) for name in routes}
    rules: List[Dict[str, Any]] = []
    for i, item in enumerate(raw, 1):
        prefix = f"model_routes: static_rules #{i}"
        if not isinstance(item, dict):
            issues.append(ConfigIssue(
                "error",
                f"{prefix}: must be a mapping (got {type(item).__name__})",
                "Each rule needs: route (a declared route) and when (a non-empty mapping)",
            ))
            continue
        dropped = False
        for key in sorted(set(item) - _RULE_KEYS):
            issues.append(ConfigIssue(
                "warning",
                f"{prefix}: unknown key '{key}' ignored",
                f"Supported rule keys: {', '.join(sorted(_RULE_KEYS))}",
            ))
        route = item.get("route")
        if not isinstance(route, str) or _norm(route) not in valid_names:
            issues.append(ConfigIssue(
                "error",
                f"{prefix}: 'route' {route!r} does not name a declared valid route",
                "Point the rule at a route declared under model_routes.routes",
            ))
            dropped = True
        when = item.get("when")
        if not isinstance(when, dict) or not when:
            issues.append(ConfigIssue(
                "error",
                f"{prefix}: 'when' must be a non-empty mapping",
                "Condition keys are opaque in Phase 1; matching semantics land in Phase 2",
            ))
            dropped = True
        reason = item.get("reason")
        if reason is not None and not isinstance(reason, str):
            issues.append(ConfigIssue(
                "warning",
                f"{prefix}: 'reason' must be a string (got {type(reason).__name__})",
                "Use a short human-readable explanation, or omit it",
            ))
        if not dropped:
            rules.append(item)
    return rules


def load_routes(cfg: Optional[Dict[str, Any]] = None) -> RouteCatalog:
    """Parse+validate ``cfg["model_routes"]`` into a :class:`RouteCatalog`.

    Absent/empty section → dormant catalog (no routes, default health, no
    issues).  Routes with any error-severity violation are dropped.
    """
    if cfg is None:
        cfg = load_config()

    catalog = RouteCatalog()
    section = cfg.get("model_routes")
    if not section:
        return catalog
    if not isinstance(section, dict):
        catalog.issues.append(ConfigIssue(
            "error",
            f"model_routes must be a mapping (got {type(section).__name__})",
            "See cli-config.yaml.example for the model_routes schema",
        ))
        return catalog

    for key in sorted(set(section) - _SECTION_KEYS):
        catalog.issues.append(ConfigIssue(
            "warning",
            f"model_routes: unknown key '{key}' under model_routes ignored",
            f"Supported keys: {', '.join(sorted(_SECTION_KEYS))}",
        ))

    catalog.routes = _parse_routes(section.get("routes"), cfg, catalog.issues)
    catalog.health = _parse_health(section.get("health"), catalog.issues)
    catalog.static_rules = _parse_static_rules(section.get("static_rules"), catalog.routes, catalog.issues)
    return catalog


def validate_model_routes(cfg: Optional[Dict[str, Any]] = None) -> List[ConfigIssue]:
    """Config-validation hook — called by ``config.validate_config_structure``."""
    return load_routes(cfg).issues


# =============================================================================
# Resolution
# =============================================================================


def _lookup_route(catalog: RouteCatalog, route_name: str) -> Optional[RouteSpec]:
    name = str(route_name or "").strip().lower()
    if not name:
        return None
    for spec in catalog.routes.values():
        if spec.name.strip().lower() == name:
            return spec
    return None


def resolve_route(
    route_name: str,
    cfg: Optional[Dict[str, Any]] = None,
    *,
    catalog: Optional[RouteCatalog] = None,
) -> Optional[Dict[str, str]]:
    """Walk default → fallbacks; return the first healthy runtime as flat strings.

    Returns ``None`` for unknown routes or when the whole chain is unhealthy
    (callers emit no switch and stay put — never route to a dead provider).
    """
    catalog = catalog or load_routes(cfg)
    spec = _lookup_route(catalog, route_name)
    if spec is None:
        logger.warning("model_routes: unknown route %r", route_name)
        return None

    chain: List[Tuple[str, str, str, str]] = [
        (spec.provider, spec.model, spec.reasoning_effort, "default")
    ]
    for i, fb in enumerate(spec.fallbacks, 1):  # source index is 1-based
        chain.append((fb.provider, fb.model, fb.reasoning_effort, f"fallback:{i}"))

    failures: List[str] = []
    for provider, model, effort, source in chain:
        healthy, reason = provider_health(provider, model, cfg=cfg, health=catalog.health)
        if healthy:
            return {
                "route": spec.name,
                "provider": provider,
                "model": model,
                "reasoning_effort": effort or "",
                "source": source,
                "reason": f"failover — {'; '.join(failures)}" if failures else "",
            }
        failures.append(f"{provider} unhealthy ({reason})")

    logger.warning(
        "model_routes: route %r has no healthy runtime: %s",
        spec.name, "; ".join(failures),
    )
    return None


# =============================================================================
# Provider health probing (ported from skill-gate runtime_catalog.py)
# =============================================================================


def _health_checks_enabled(health: HealthConfig) -> bool:
    override = os.environ.get(_HEALTH_ENV, "").strip().lower()
    if override in ("0", "false", "off"):
        return False
    if override in ("1", "true", "on"):
        return True
    return health.enabled


def _cfg_runtime_fallback(provider: str, cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Minimal probe runtime straight from the ``providers:`` entry, key-less.

    Preserves skill-gate semantics: a known base_url with a missing key still
    probes (a 401 answer then counts healthy via fail-open); no entry → {}.
    """
    from hermes_cli.runtime_provider import _normalize_custom_provider_name

    if cfg is None:
        try:
            cfg = load_config()
        except Exception:
            logger.debug("model_routes: config load failed during probe fallback", exc_info=True)
            return {}
    providers = cfg.get("providers") if isinstance(cfg, dict) else None
    if not isinstance(providers, dict):
        return {}
    target = _normalize_custom_provider_name(str(provider or ""))
    if not target:
        return {}
    for key, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        entry_names = {_normalize_custom_provider_name(str(key))}
        raw_name = entry.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            entry_names.add(_normalize_custom_provider_name(raw_name))
        if target not in entry_names:
            continue
        base_url = ""
        for url_key in ("base_url", "url", "api"):
            value = entry.get(url_key)
            if isinstance(value, str) and value.strip():
                base_url = value.strip()
                break
        return {
            "base_url": base_url,
            "api_mode": str(entry.get("api_mode") or ""),
            "api_key": "",
            "default_model": str(entry.get("default_model") or entry.get("model") or ""),
        }
    return {}


def _probe_provider(
    provider: str,
    model: str,
    cfg: Optional[Dict[str, Any]],
    health: HealthConfig,
) -> Tuple[bool, str]:
    """One live probe. anthropic_messages mode sends a 1-token message (also
    catches credit exhaustion); OpenAI-compatible modes GET /models."""
    runtime: Dict[str, Any] = {}
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        resolved = resolve_runtime_provider(requested=provider, target_model=model or None)
        if isinstance(resolved, dict) and resolved.get("base_url"):
            runtime = resolved
    except Exception:
        logger.debug("model_routes: runtime resolution failed for %r", provider, exc_info=True)
    if not runtime:
        runtime = _cfg_runtime_fallback(provider, cfg)

    base = str(runtime.get("base_url") or "").rstrip("/")
    if not base:
        return False, "no base_url resolved"
    key = str(runtime.get("api_key") or "")
    api_mode = str(runtime.get("api_mode") or "")
    try:
        if api_mode == "anthropic_messages":
            url = f"{base}/v1/messages"
            body = json.dumps({
                "model": str(model or runtime.get("default_model") or runtime.get("model") or ""),
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "ping"}],
            }).encode()
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": "application/json",
                "x-api-key": key,
                "Authorization": f"Bearer {key}",
                "anthropic-version": "2023-06-01",
            })
        else:
            url = base + ("/models" if base.endswith("/v1") else "/v1/models")
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        with _urlopen(req, timeout=health.probe_timeout_seconds) as resp:
            code = getattr(resp, "status", 200)
        return (200 <= code < 300), f"HTTP {code}"
    except urllib.error.HTTPError as exc:
        # Fail-open semantics: only signals that indicate the PROVIDER cannot
        # serve completions count as unhealthy — credit/quota exhaustion
        # (body sniff; Anthropic reports low credit as HTTP 400), 402/429,
        # and 5xx. Auth-scoped 401/403 (or a malformed probe 400) usually
        # means OUR probe credentials/shape are off, not that the provider is
        # down — treat as healthy so a probe defect can never freeze routing.
        body = ""
        try:
            body = exc.read(500).decode("utf-8", "replace").lower()
        except Exception:
            body = ""
        if any(word in body for word in _CREDIT_SNIFF_KEYWORDS):
            return False, f"HTTP {exc.code} (credit/quota)"
        if exc.code in (402, 429) or exc.code >= 500:
            return False, f"HTTP {exc.code}"
        return True, f"assumed healthy (auth-scoped HTTP {exc.code})"
    except Exception as exc:  # noqa: BLE001
        # Connection refused / DNS / timeout — provider is genuinely unreachable.
        return False, str(exc)[:80]


def _read_health_cache(path: Path) -> Dict[str, Any]:
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
        return cache if isinstance(cache, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _store_health_verdict(path: Path, key: str, entry: Dict[str, Any]) -> None:
    """Merge one verdict into the shared cache under an exclusive flock.

    Concurrent hermes processes (gateway + interactive CLI) share this file;
    a whole-file read-modify-write from a pre-probe snapshot would let one
    process clobber the other's fresh verdict (lost update), dropping its
    fail_ttl suppression and re-blocking on a dead provider.  Re-reading
    inside the lock means every merge starts from the latest snapshot.
    Best-effort: any failure is logged and the verdict is simply not cached.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(path.name + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            locked = False
            if fcntl is not None:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                    locked = True
                except OSError:
                    # e.g. ENOLCK on NFS without lockd, or FUSE mounts that
                    # reject flock — degrade to the lock-less re-read+merge
                    # rather than skipping the cache write entirely.
                    logger.debug(
                        "model_routes: flock unavailable for %s; writing lock-less",
                        lock_path, exc_info=True,
                    )
            try:
                cache = _read_health_cache(path)
                cache[key] = entry
                atomic_json_write(path, cache)
            finally:
                if locked:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception:
        logger.debug("model_routes: health cache write failed for %s", path, exc_info=True)


def provider_health(
    provider: str,
    model: str = "",
    *,
    cfg: Optional[Dict[str, Any]] = None,
    health: Optional[HealthConfig] = None,
) -> Tuple[bool, str]:
    """Cached, fail-open health verdict for a provider (keyed by provider name)."""
    if health is None:
        health = load_routes(cfg).health
    if not _health_checks_enabled(health):
        return True, "health checks disabled"
    if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get(_HEALTH_TEST_ENV):
        return True, "pytest"

    path = health.resolved_cache_path()
    cache = _read_health_cache(path)

    now = _now()
    key = str(provider or "")
    entry = cache.get(key)
    if isinstance(entry, dict):
        try:
            ts = float(entry.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        ttl = health.ok_ttl_seconds if entry.get("healthy") else health.fail_ttl_seconds
        if now - ts < ttl:
            return bool(entry.get("healthy")), str(entry.get("reason") or "cached")

    healthy, reason = _probe_provider(key, str(model or ""), cfg, health)
    _store_health_verdict(path, key, {"healthy": healthy, "reason": reason, "ts": now})
    return healthy, reason


# =============================================================================
# Membership / schema exposure
# =============================================================================


def runtime_satisfies_route(
    runtime: Dict[str, Any],
    route_name: str,
    cfg: Optional[Dict[str, Any]] = None,
    *,
    catalog: Optional[RouteCatalog] = None,
) -> bool:
    """True when the live runtime's model is already a member of the route.

    Membership matching is model-only by design: reasoning_effort/provider/
    base_url are delivery details and never change tier membership.
    """
    if not isinstance(runtime, dict):
        return False
    catalog = catalog or load_routes(cfg)
    spec = _lookup_route(catalog, route_name)
    if spec is None:
        return False
    membership = spec.accepted or (spec.model,) + tuple(fb.model for fb in spec.fallbacks)
    current = runtime.get("model")
    return any(_model_matches(current, candidate) for candidate in membership)


def route_catalog_for_schema(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    catalog: Optional[RouteCatalog] = None,
) -> List[Tuple[str, str]]:
    """(name, description) pairs for valid routes, in declaration order."""
    catalog = catalog or load_routes(cfg)
    return [(spec.name, spec.description) for spec in catalog.routes.values()]
