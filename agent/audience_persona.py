"""Audience-mode persona selection for the system prompt.

Optional, config-off-by-default feature: when ``HERMES_HOME/personas/
modes.yaml`` exists, an "audience mode" is selected per session as a pure
deterministic function of session-constant inputs (platform, chat type/id/
name, user id) and the selected mode's persona markdown is injected into
the system prompt as stable-tier slot #2, right after the identity block
(SOUL.md / DEFAULT_AGENT_IDENTITY).

When the file is absent the feature is a strict no-op — prompt assembly is
byte-identical to a build without this module.  Every failure path
(unreadable YAML, bad schema, missing persona file) degrades to the same
no-op, logged at DEBUG, so a broken persona pack can never take down
prompt assembly.

``modes.yaml`` contract (owner-agnostic):

* ``rules``   — ordered list; first match wins.  Each rule's ``match``
  maps any of ``platform`` / ``chat_type`` / ``chat_id`` / ``chat_name``
  to a string or list of strings.  Exact match (``chat_name``
  case-insensitive); a missing key is a wildcard; all present keys must
  match (AND).
* ``guards.non_owner_mode`` — applied AFTER rules: when ``user_id`` is a
  non-empty string and not listed in ``owners[platform]``, the mode is
  forced to this value.  An empty ``user_id`` (cli/cron surfaces) skips
  the guard; a non-empty ``user_id`` on a platform with no ``owners``
  entry fails safe (guard applies).
* ``default_mode`` — used when no rule matches, or when the selected mode
  name is not declared under ``modes``.
* ``modes.<name>.persona`` — persona markdown filename, relative to the
  ``personas`` directory.
* ``base_persona`` — optional shared-prefix markdown filename, relative to
  the ``personas`` directory.

Persona content passes through the same threat scanner and truncation cap
as SOUL.md (see ``agent.prompt_builder``) before entering the prompt.

The ``artifact_register`` section of ``modes.yaml`` is intentionally NOT
consumed here — it is a contract for an external tone-gate plugin.
"""

from __future__ import annotations

import logging
import re
import stat as stat_module
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# Session-constant inputs a rule's ``match`` block may constrain.
_MATCH_KEYS = ("platform", "chat_type", "chat_id", "chat_name")

# The selected mode name is echoed verbatim into the volatile prompt tail
# ("AudienceMode: <mode>") and compared by the staleness resolver, so it
# must be a single safe token: no newlines (volatile-tail injection), no
# exotic whitespace (rebuild loops from normalization drift).  Enforced in
# ``select_audience_mode`` — the ONE selection point both the loader and
# the resolver share — so the two can never disagree about a bad name.
_MODE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def persona_injection_enabled(agent: Any) -> bool:
    """Whether this agent's prompt build may inject an audience persona.

    Mirrors the SOUL.md identity gate in ``system_prompt.
    build_system_prompt_parts`` (``agent.load_soul_identity or not
    agent.skip_context_files``): execution modes that skip HERMES_HOME
    context files without opting into the persona identity (batch_runner)
    must not receive the audience persona either.  Shared by the build
    path and the stored-prompt staleness resolver so the two always agree
    — a disagreement would rebuild the prompt every turn.
    """
    return bool(getattr(agent, "load_soul_identity", False)) or not bool(
        getattr(agent, "skip_context_files", False)
    )


def current_speaker_user_id(agent: Any) -> str:
    """User id of the CURRENT message speaker for audience-mode selection.

    Shared thread sessions (e.g. discord ``thread_sessions_per_user:
    false``) reuse one cached agent object across participants, so
    ``agent._user_id`` can be stale — the thread creator, not whoever is
    speaking now.  Prefer the gateway's per-message session context
    (``HERMES_SESSION_USER_ID``) and fall back to ``agent._user_id`` when
    no session context is available (CLI, cron, tests).

    Uses the same ``sys.modules`` guard as ``prompt_builder.
    _current_session_platform_hint`` so importing this module never drags
    the gateway package into CLI startup.  Both the prompt build
    (``system_prompt.build_system_prompt_parts``) and the staleness
    resolver (``conversation_loop._stored_prompt_matches_runtime``) MUST
    resolve the speaker through this one helper, or a speaker change in a
    shared thread would flip one but not the other.
    """
    session_context = sys.modules.get("gateway.session_context")
    get_session_env = (
        getattr(session_context, "get_session_env", None) if session_context else None
    )
    if get_session_env is not None:
        try:
            uid = _norm(get_session_env("HERMES_SESSION_USER_ID"))
            if uid:
                return uid
        except Exception:
            pass
    return _norm(getattr(agent, "_user_id", "") or "")


def _modes_yaml_path() -> Path:
    return get_hermes_home() / "personas" / "modes.yaml"


def _load_modes_config() -> Optional[Dict[str, Any]]:
    """Read and parse ``personas/modes.yaml``.  Any problem → ``None`` (off)."""
    path = _modes_yaml_path()
    try:
        if not path.is_file():
            return None
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.debug("personas/modes.yaml is not a mapping; ignoring")
            return None
        return data
    except Exception as exc:
        logger.debug("Could not load %s: %s", path, exc)
        return None


def _norm(value: Any) -> str:
    """Normalize a session input to a comparable string ('' when unset)."""
    if value is None:
        return ""
    return str(value).strip()


def _match_values(raw: Any) -> list[str]:
    """A match key's value as a list of strings (string-or-list contract)."""
    if isinstance(raw, (list, tuple)):
        return [str(v) for v in raw]
    return [str(raw)]


def _rule_matches(match: Any, inputs: Dict[str, str]) -> bool:
    """AND-match *inputs* against a rule's ``match`` block.

    Missing key = wildcard; ``chat_name`` compares case-insensitively;
    everything else is exact.  A non-dict ``match`` never matches (bad
    schema fails closed for that rule, the walk continues).
    """
    if match is None:
        return True  # no match block = unconditional rule
    if not isinstance(match, dict):
        return False
    for key in _MATCH_KEYS:
        if key not in match:
            continue
        candidates = _match_values(match[key])
        actual = inputs.get(key, "")
        if key == "chat_name":
            if actual.lower() not in {c.strip().lower() for c in candidates}:
                return False
        else:
            if actual not in {c.strip() for c in candidates}:
                return False
    return True


def select_audience_mode(
    cfg: Dict[str, Any],
    platform: Any = None,
    chat_type: Any = None,
    chat_id: Any = None,
    chat_name: Any = None,
    user_id: Any = None,
) -> str:
    """Pure mode selection: rules (first match wins) → non-owner guard →
    default fallback.

    Returns the selected mode name, or ``""`` when no usable mode exists
    (no ``modes`` mapping, or neither the selected mode nor
    ``default_mode`` is declared).
    """
    if not isinstance(cfg, dict):
        return ""
    inputs = {
        "platform": _norm(platform),
        "chat_type": _norm(chat_type),
        "chat_id": _norm(chat_id),
        "chat_name": _norm(chat_name),
    }
    uid = _norm(user_id)

    modes = cfg.get("modes")
    if not isinstance(modes, dict) or not modes:
        return ""

    default_mode = _norm(cfg.get("default_mode"))

    # ── Rules: first match wins, top to bottom ─────────────────────
    selected = ""
    rules = cfg.get("rules")
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if _rule_matches(rule.get("match"), inputs):
                selected = _norm(rule.get("mode"))
                break
    if not selected:
        selected = default_mode

    # ── Non-owner guard (applies AFTER rules) ──────────────────────
    # Only when the speaker is identified (non-empty user_id).  A platform
    # with no owners entry fails safe: an identified non-listed speaker is
    # a non-owner.  Empty user_id (cli/cron) skips the guard entirely.
    guards = cfg.get("guards")
    if uid and isinstance(guards, dict):
        non_owner_mode = _norm(guards.get("non_owner_mode"))
        if non_owner_mode:
            owners = cfg.get("owners")
            owner_ids: set[str] = set()
            if isinstance(owners, dict):
                owner_ids = {
                    _norm(v) for v in _match_values(owners.get(inputs["platform"], []))
                }
            if uid not in owner_ids:
                selected = non_owner_mode

    # ── Validate against declared modes; fall back to default ──────
    # A declared-but-unsafe name (fails _MODE_NAME_RE) is feature-off, NOT
    # a fallback: the name would be echoed verbatim into the volatile
    # prompt tail, so a newline (or a 65-char blob) in it is an injection
    # vector, and silently swapping modes would mask the config bug.
    if selected and selected in modes:
        if not _MODE_NAME_RE.fullmatch(selected):
            logger.debug("audience mode name %r fails sanitization; feature off", selected)
            return ""
        return selected
    if default_mode and default_mode in modes:
        if not _MODE_NAME_RE.fullmatch(default_mode):
            logger.debug("default_mode name %r fails sanitization; feature off", default_mode)
            return ""
        return default_mode
    return ""


def _contained_persona_path(persona: str, label: str) -> Optional[Path]:
    """Resolve a persona filename while keeping it inside ``personas``."""
    # Containment: the resolved persona file must live inside the personas
    # directory.  A traversal, an absolute path, or a symlinked escape is
    # treated as feature-off (None), never an error.
    personas_dir = (get_hermes_home() / "personas").resolve()
    try:
        path = (personas_dir / persona).resolve()
        if not path.is_relative_to(personas_dir):
            logger.debug(
                "persona path %r for %s escapes %s; feature off",
                persona, label, personas_dir,
            )
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    return path


def _persona_path(cfg: Dict[str, Any], mode: str) -> Optional[Path]:
    """Resolve the persona markdown path for *mode* (personas-dir relative)."""
    modes = cfg.get("modes")
    if not isinstance(modes, dict):
        return None
    spec = modes.get(mode)
    if not isinstance(spec, dict):
        return None
    persona = _norm(spec.get("persona"))
    if not persona:
        return None
    return _contained_persona_path(persona, f"mode {mode!r}")


def _read_persona_raw(cfg: Dict[str, Any], mode: str) -> Optional[Tuple[Path, str]]:
    """Raw persona text for *mode*, or ``None`` when missing/empty."""
    path = _persona_path(cfg, mode)
    if path is None or not path.is_file():
        return None
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return None
    return path, content


def _persona_stat_ok(cfg: Dict[str, Any], mode: str) -> bool:
    """Cheap existence check for the resolver: regular file, size > 0.

    ``resolve_audience_mode`` runs on the per-turn staleness path, so it
    must not read the persona body — a single ``stat()`` is enough to
    agree with the loader on every real-world activation state (present /
    absent / truncated-to-empty).
    """
    path = _persona_path(cfg, mode)
    if path is None:
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    return stat_module.S_ISREG(st.st_mode) and st.st_size > 0


def resolve_audience_mode(
    platform: Any = None,
    chat_type: Any = None,
    chat_id: Any = None,
    chat_name: Any = None,
    user_id: Any = None,
) -> Optional[str]:
    """Mode-only resolution — no persona reads, scanning, or truncation.

    Cheap enough for per-turn staleness checks
    (``conversation_loop._stored_prompt_matches_runtime``): the persona
    file is only ``stat()``-ed (regular file, size > 0), never read.
    Returns the mode name exactly when :func:`load_audience_persona`
    would inject a persona for the same inputs, else ``None`` (feature
    off / no usable persona) — the two functions must stay in agreement
    or stored prompts would rebuild every turn.  (Sole tolerated skew: a
    whitespace-only persona file has size > 0 but loads empty; that is a
    broken pack, not a reachable steady state.)
    """
    try:
        cfg = _load_modes_config()
        if cfg is None:
            return None
        mode = select_audience_mode(
            cfg,
            platform=platform,
            chat_type=chat_type,
            chat_id=chat_id,
            chat_name=chat_name,
            user_id=user_id,
        )
        if not mode:
            return None
        if not _persona_stat_ok(cfg, mode):
            return None
        return mode
    except Exception as exc:
        logger.debug("resolve_audience_mode failed (feature off): %s", exc)
        return None


def load_audience_persona(
    platform: Any = None,
    chat_type: Any = None,
    chat_id: Any = None,
    chat_name: Any = None,
    user_id: Any = None,
    context_length: Optional[int] = None,
) -> Optional[Tuple[str, str]]:
    """Select the audience mode and load its persona markdown.

    Returns ``(mode_name, persona_text)`` ready for prompt injection, or
    ``None`` when the feature is off (no ``personas/modes.yaml``) or any
    step fails — the caller then behaves byte-identically to a build
    without this feature.

    Persona content is threat-scanned and truncated exactly like SOUL.md
    (same scanner scope, same cap) before it can enter the system prompt.
    """
    try:
        cfg = _load_modes_config()
        if cfg is None:
            return None
        mode = select_audience_mode(
            cfg,
            platform=platform,
            chat_type=chat_type,
            chat_id=chat_id,
            chat_name=chat_name,
            user_id=user_id,
        )
        if not mode:
            return None
        raw = _read_persona_raw(cfg, mode)
        if raw is None:
            return None
        path, content = raw

        # Same protection pipeline as SOUL.md (prompt_builder.load_soul_md):
        # context-scope threat scan, then head/tail truncation at the
        # context-file cap (scales with the model window).
        from agent.prompt_builder import _scan_context_content, _truncate_content

        label = f"personas/{path.name}"
        content = _scan_context_content(content, label)
        content = _truncate_content(
            content, label, context_length=context_length, read_path=str(path),
        )
        if not content.strip():
            return None

        # ``base_persona`` is optional.  Keep the absent-key path exactly as
        # before: the selected mode is the complete injected content.
        if "base_persona" not in cfg:
            return mode, content
        base_persona = _norm(cfg.get("base_persona"))
        if not base_persona:
            return mode, content

        base_path = _contained_persona_path(base_persona, "base_persona")
        if base_path is None or base_path == path:
            return mode, content

        # A broken optional base must never suppress a usable mode persona.
        try:
            if not base_path.is_file():
                logger.debug("base persona file %s is missing; using mode persona alone", base_path)
                return mode, content
            base_content = base_path.read_text(encoding="utf-8").strip()
            if not base_content:
                logger.debug("base persona file %s is empty; using mode persona alone", base_path)
                return mode, content

            base_label = f"personas/{base_path.name}"
            base_content = _scan_context_content(base_content, base_label)
            base_content = _truncate_content(
                base_content,
                base_label,
                context_length=context_length,
                read_path=str(base_path),
            )
            if not base_content.strip():
                logger.debug(
                    "base persona file %s became empty after protection; using mode persona alone",
                    base_path,
                )
                return mode, content
        except Exception as exc:
            logger.debug(
                "Could not load base persona %s; using mode persona alone: %s",
                base_path,
                exc,
            )
            return mode, content

        return mode, base_content + "\n\n" + content
    except Exception as exc:
        logger.debug("load_audience_persona failed (feature off): %s", exc)
        return None
