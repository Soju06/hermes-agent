"""Agent model inspection and switching helpers.

This module is intentionally core-owned (not a plugin) because model/runtime
inspection/switching needs live ``AIAgent`` state and must never reach into
GatewayRunner/CLI private fields from a plugin.  Public tool handlers in
``tools.runtime_control_tool`` provide schemas only; execution is intercepted
by the agent loop and routed here.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

from hermes_constants import parse_reasoning_effort

logger = logging.getLogger(__name__)

_ALLOWED_SCOPES = {"turn", "session"}


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _sanitize_base_url(raw_url: Any) -> str:
    """Return a display-safe endpoint URL with credentials/query stripped."""
    raw = _safe_str(raw_url).strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except Exception:
        return ""
    if not parsed.scheme or not parsed.hostname:
        return ""
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def _reasoning_state(agent: Any) -> Dict[str, Any]:
    cfg = getattr(agent, "reasoning_config", None)
    source = getattr(agent, "_runtime_reasoning_source", None) or "agent"
    if isinstance(cfg, dict):
        if cfg.get("enabled") is False:
            return {"enabled": False, "effort": "none", "source": source}
        effort = str(cfg.get("effort") or "").strip().lower() or None
        return {"enabled": True, "effort": effort, "source": source}
    return {"enabled": None, "effort": None, "source": "default"}


def get_runtime_state(agent: Any) -> Dict[str, Any]:
    """Return a secret-free snapshot of the agent's current effective runtime."""
    return {
        "model": _safe_str(getattr(agent, "model", "")),
        "provider": _safe_str(getattr(agent, "provider", "")),
        "base_url": _sanitize_base_url(getattr(agent, "base_url", "")),
        "api_mode": _safe_str(getattr(agent, "api_mode", "")),
        "session_id": _safe_str(getattr(agent, "session_id", "")),
        "platform": _safe_str(getattr(agent, "platform", "")),
        "has_gateway_session": bool(_safe_str(getattr(agent, "_gateway_session_key", ""))),
        "reasoning": _reasoning_state(agent),
        "turn_override_active": hasattr(agent, "_runtime_turn_restore_snapshot"),
        "model_source": getattr(agent, "_runtime_model_source", None) or "agent",
    }


def model_status(agent: Any) -> str:
    """Tool-facing JSON wrapper for :func:`get_runtime_state`."""
    state = get_runtime_state(agent)
    state["success"] = True
    return json.dumps(state, ensure_ascii=False)


def snapshot_runtime(agent: Any) -> Dict[str, Any]:
    """Capture enough live state to restore a turn-scoped runtime switch."""
    return {
        "model": getattr(agent, "model", ""),
        "provider": getattr(agent, "provider", ""),
        "api_key": getattr(agent, "api_key", ""),
        "base_url": getattr(agent, "base_url", ""),
        "api_mode": getattr(agent, "api_mode", ""),
        "reasoning_config": copy.deepcopy(getattr(agent, "reasoning_config", None)),
        "runtime_model_source": getattr(agent, "_runtime_model_source", None),
        "runtime_reasoning_source": getattr(agent, "_runtime_reasoning_source", None),
        "fallback_chain": copy.deepcopy(getattr(agent, "_fallback_chain", None)),
        "fallback_model": copy.deepcopy(getattr(agent, "_fallback_model", None)),
        "fallback_index": getattr(agent, "_fallback_index", None),
        "fallback_activated": getattr(agent, "_fallback_activated", None),
    }


def restore_runtime(agent: Any, snapshot: Dict[str, Any]) -> None:
    """Restore a runtime snapshot captured by :func:`snapshot_runtime`."""
    if not isinstance(snapshot, dict):
        return

    old_model = snapshot.get("model", "")
    old_provider = snapshot.get("provider", "")
    old_api_key = snapshot.get("api_key", "")
    old_base_url = snapshot.get("base_url", "")
    old_api_mode = snapshot.get("api_mode", "")

    model_changed = (
        getattr(agent, "model", "") != old_model
        or getattr(agent, "provider", "") != old_provider
        or getattr(agent, "base_url", "") != old_base_url
        or getattr(agent, "api_mode", "") != old_api_mode
    )
    if model_changed and hasattr(agent, "switch_model"):
        agent.switch_model(
            new_model=old_model,
            new_provider=old_provider,
            api_key=old_api_key,
            base_url=old_base_url,
            api_mode=old_api_mode,
        )
    else:
        if old_model is not None:
            agent.model = old_model
        if old_provider is not None:
            agent.provider = old_provider
        if old_base_url is not None:
            agent.base_url = old_base_url
        if old_api_mode is not None:
            agent.api_mode = old_api_mode
        if old_api_key is not None:
            agent.api_key = old_api_key

    agent.reasoning_config = copy.deepcopy(snapshot.get("reasoning_config"))

    for attr, key in (
        ("_fallback_chain", "fallback_chain"),
        ("_fallback_model", "fallback_model"),
        ("_fallback_index", "fallback_index"),
        ("_fallback_activated", "fallback_activated"),
    ):
        if key in snapshot:
            setattr(agent, attr, copy.deepcopy(snapshot.get(key)))

    if snapshot.get("runtime_model_source") is None:
        if hasattr(agent, "_runtime_model_source"):
            delattr(agent, "_runtime_model_source")
    else:
        agent._runtime_model_source = snapshot.get("runtime_model_source")

    if snapshot.get("runtime_reasoning_source") is None:
        if hasattr(agent, "_runtime_reasoning_source"):
            delattr(agent, "_runtime_reasoning_source")
    else:
        agent._runtime_reasoning_source = snapshot.get("runtime_reasoning_source")


def restore_pending_turn_runtime(agent: Any) -> bool:
    """Restore and clear a pending turn-scoped runtime override, if any."""
    snapshot = getattr(agent, "_runtime_turn_restore_snapshot", None)
    if not snapshot:
        return False
    try:
        restore_runtime(agent, snapshot)
        return True
    finally:
        try:
            delattr(agent, "_runtime_turn_restore_snapshot")
        except AttributeError:
            pass


def resolve_model_switch(
    *,
    raw_input: str,
    current_provider: str,
    current_model: str,
    current_base_url: str,
    current_api_key: str,
    explicit_provider: str,
):
    """Resolve a requested model/provider switch via Hermes' shared resolver."""
    from hermes_cli.config import get_compatible_custom_providers, load_config
    from hermes_cli.model_switch import switch_model as _switch_model

    cfg = load_config()
    user_providers = cfg.get("providers") or {} if isinstance(cfg, dict) else {}
    custom_providers = get_compatible_custom_providers(cfg) if isinstance(cfg, dict) else None
    return _switch_model(
        raw_input=raw_input,
        current_provider=current_provider,
        current_model=current_model,
        current_base_url=current_base_url,
        current_api_key=current_api_key,
        is_global=False,
        explicit_provider=explicit_provider,
        user_providers=user_providers,
        custom_providers=custom_providers,
    )


def _notify_runtime_update(
    agent: Any,
    *,
    scope: str,
    model_override: Optional[Dict[str, Any]],
    reasoning_config: Optional[Dict[str, Any]],
) -> Optional[str]:
    callback = getattr(agent, "runtime_update_callback", None)
    if not callable(callback):
        return None
    try:
        callback(
            scope=scope,
            model_override=model_override,
            reasoning_config=copy.deepcopy(reasoning_config),
        )
        return None
    except Exception as exc:  # pragma: no cover - defensive surface callback guard
        logger.warning("runtime_update_callback failed: %s", exc)
        return str(exc) or exc.__class__.__name__


def model_switch(
    agent: Any,
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    scope: str = "turn",
    reason: Optional[str] = None,
) -> str:
    """Switch the live agent model/reasoning for this turn or session.

    ``global`` scope is deliberately unsupported.  Persisting to config.yaml is
    user-command territory; this tool can only affect the current turn or
    current session.
    """
    scope = str(scope or "turn").strip().lower()
    if scope not in _ALLOWED_SCOPES:
        return json.dumps(
            {
                "success": False,
                "error": "Unsupported scope. Use 'turn' or 'session'; global model switching is not available to agents.",
            },
            ensure_ascii=False,
        )

    requested_model = str(model or "").strip()
    requested_provider = str(provider or "").strip()
    requested_reasoning = str(reasoning_effort or "").strip().lower()
    if not requested_model and not requested_provider and not requested_reasoning:
        return json.dumps(
            {"success": False, "error": "No runtime change requested."},
            ensure_ascii=False,
        )

    parsed_reasoning = None
    if requested_reasoning:
        parsed_reasoning = parse_reasoning_effort(requested_reasoning)
        if parsed_reasoning is None:
            return json.dumps(
                {
                    "success": False,
                    "error": "Invalid reasoning_effort. Use none, minimal, low, medium, high, or xhigh.",
                },
                ensure_ascii=False,
            )

    if scope == "turn" and not hasattr(agent, "_runtime_turn_restore_snapshot"):
        agent._runtime_turn_restore_snapshot = snapshot_runtime(agent)

    model_override = None
    changed = []
    persistence_error = None

    if requested_model or requested_provider:
        current_api_key = getattr(agent, "api_key", "")
        if not isinstance(current_api_key, str):
            current_api_key = ""
        result = resolve_model_switch(
            raw_input=requested_model,
            current_provider=str(getattr(agent, "provider", "") or ""),
            current_model=str(getattr(agent, "model", "") or ""),
            current_base_url=str(getattr(agent, "base_url", "") or ""),
            current_api_key=current_api_key,
            explicit_provider=requested_provider,
        )
        if not getattr(result, "success", False):
            # If the model part failed for a turn-scoped call that captured a
            # snapshot, restore immediately so a simultaneous reasoning request
            # cannot leave partial state behind.
            if scope == "turn":
                restore_pending_turn_runtime(agent)
            return json.dumps(
                {
                    "success": False,
                    "error": getattr(result, "error_message", "Model switch failed") or "Model switch failed",
                },
                ensure_ascii=False,
            )

        agent.switch_model(
            new_model=result.new_model,
            new_provider=result.target_provider,
            api_key=getattr(result, "api_key", "") or "",
            base_url=getattr(result, "base_url", "") or "",
            api_mode=getattr(result, "api_mode", "") or "",
        )
        agent._runtime_model_source = f"model_switch:{scope}"
        model_override = {
            "model": result.new_model,
            "provider": result.target_provider,
            "api_key": getattr(result, "api_key", "") or "",
            "base_url": getattr(result, "base_url", "") or "",
            "api_mode": getattr(result, "api_mode", "") or "",
        }
        changed.append("model")

    if parsed_reasoning is not None:
        agent.reasoning_config = copy.deepcopy(parsed_reasoning)
        agent._runtime_reasoning_source = f"model_switch:{scope}"
        changed.append("reasoning")

    if scope == "session" and hasattr(agent, "_runtime_turn_restore_snapshot"):
        snapshot = agent._runtime_turn_restore_snapshot
        if model_override:
            snapshot.update(
                {
                    "model": model_override.get("model", ""),
                    "provider": model_override.get("provider", ""),
                    "api_key": model_override.get("api_key", ""),
                    "base_url": model_override.get("base_url", ""),
                    "api_mode": model_override.get("api_mode", ""),
                    "runtime_model_source": getattr(agent, "_runtime_model_source", None),
                    "fallback_chain": copy.deepcopy(getattr(agent, "_fallback_chain", None)),
                    "fallback_model": copy.deepcopy(getattr(agent, "_fallback_model", None)),
                    "fallback_index": getattr(agent, "_fallback_index", None),
                    "fallback_activated": getattr(agent, "_fallback_activated", None),
                }
            )
        if parsed_reasoning is not None:
            snapshot["reasoning_config"] = copy.deepcopy(parsed_reasoning)
            snapshot["runtime_reasoning_source"] = getattr(
                agent, "_runtime_reasoning_source", None
            )

    if scope == "session":
        persistence_error = _notify_runtime_update(
            agent,
            scope=scope,
            model_override=model_override,
            reasoning_config=parsed_reasoning,
        )

    state = get_runtime_state(agent)
    response = {
        "success": True,
        "scope": scope,
        "changed": changed,
        "reason": str(reason or ""),
        "runtime": state,
    }
    warning = getattr(locals().get("result", None), "warning_message", "") if "result" in locals() else ""
    if warning:
        response["warning"] = warning
    if persistence_error:
        response["persistence_warning"] = (
            "Runtime changed on the live agent, but the session persistence callback failed: "
            f"{persistence_error}"
        )
    return json.dumps(response, ensure_ascii=False)


__all__ = [
    "get_runtime_state",
    "model_status",
    "model_switch",
    "snapshot_runtime",
    "restore_runtime",
    "restore_pending_turn_runtime",
    "resolve_model_switch",
]
