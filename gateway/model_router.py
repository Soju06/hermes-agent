"""Dynamic model router — gateway pre-dispatch routing stage (ADR-003 Phase 2).

Ported from the skill-gate plugin's ``dev_routing`` policy
(``policy_router.py``).  The classifier system prompt, response schema,
fallback regexes, context-payload shape (fields, order, truncation limits,
9000-char budget), and NORMAL→chat hysteresis semantics are verbatim from the
skill-gate bench winner (2026-07-15 router benchmark: 230-case gold-labeled
main set 98.7%, 120-case session-disjoint holdout 95.0%, 0 missed switches).
Do not edit prompt/schema/regexes/context shape without re-running the
230+120 bench.

Design rules (inherited from the plugin):
- the LLM classifier is the primary decision path; regex is a narrow outage
  fallback only (``source="fallback"``), never the normal candidate gate
- fail open: a missing API key or any classifier error falls back to the
  conservative regex path; a fail-open NORMAL must never advance the
  chat-downgrade streak
- static rules (``model_routes.static_rules``) match before the classifier;
  the first matching rule wins and short-circuits the LLM call

Differences from the plugin (deliberate, per ADR-003):
- label→route mapping comes from ``model_routes.router.label_routes`` config
  instead of rules.yaml ``outputs`` (config-generated prompts are Phase 4)
- route resolution / no-op membership go through the Phase 1 catalog
  (``hermes_cli.model_routes.resolve_route`` / ``runtime_satisfies_route``)
- a missing Gemini API key raises (→ regex fallback, ``source="fallback"``)
  instead of silently returning "" (which the plugin counted as an
  LLM-sourced NORMAL)

This module is import-pure: no module-global mutable state.  Hysteresis
state lives on the GatewayRunner instance and is threaded in as ``state``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_urlopen = urllib.request.urlopen  # test seam

DEFAULT_TIMEOUT_MS = 8000.0
MAX_TURN_CHARS = 1200
MAX_CONTEXT_CHARS = 9000

# Benchmark winner for dev routing (see skill-gate bench/). The router config
# sets this per deployment; this constant is the code-level default.
DEV_DEFAULT_MODEL = "gemini-3-flash-preview"

DEV_LABELS = ("NORMAL", "DOCUMENT_WORK", "FRONTEND_DEV", "SYSTEM_DEV")

# NORMAL→CHAT downgrade hysteresis: a dev/doc session is only handed back to
# the chat tier after this many consecutive LLM-sourced NORMAL classifications.
# Mid-task status questions ("잘 돌아가?") are the single most common turn type
# in dev sessions (bench error taxonomy FS1) — a one-shot downgrade would yank
# the session model constantly. Upgrades (chat→dev) stay immediate.
DEFAULT_NORMAL_DOWNGRADE_STREAK = 3

# Member→primary re-promotion hysteresis: route membership is absorbing (any
# ``accepted`` member no-ops forever), so a session parked on a non-primary
# member is walked back to the route primary after this many consecutive
# member no-op turns. Config: router.repromote_after_turns, per-route
# repromote_after_turns overrides; <= 0 disables.
DEFAULT_REPROMOTE_AFTER_TURNS = 3

_DECISION_LOG_ENV = "HERMES_MODEL_ROUTER_DECISION_LOG"
_DECISION_LOG_FILENAME = "model_router_decisions.jsonl"


# ---------------------------------------------------------------------------
# Verbatim constants from skill-gate policy_router.py (bench winner).
# Copied byte-identically — see tests/gateway/test_model_router.py parity
# checks. Do not edit without re-running the 230+120 bench.
# ---------------------------------------------------------------------------

DEV_CANDIDATE_RE = re.compile(
    r"("
    r"코드\s*(수정|변경|작성|리뷰|개선)|"
    r"패치\s*(스택|수정|추가|관리|고장|깨짐|복구)|"
    r"(?:hermes|delegate_task|model_switch|model_status|skill-gate|gateway|runtime)"
    r"[^\n]{0,80}(?:업데이트|패치|고장|깨짐|원인\s*파악|디버깅|debug|fix|troubleshoot)|"
    r"(?:업데이트|패치|고장|깨짐|원인\s*파악|디버깅|debug|fix|troubleshoot)"
    r"[^\n]{0,80}(?:hermes|delegate_task|model_switch|model_status|skill-gate|gateway|runtime)|"
    r"디버깅|리팩터|구현해|핫픽스|버그\s*(수정|고쳐|잡아)|오류\s*(수정|고쳐)|"
    r"\b(fix|debug|implement|refactor|patch|write\s+code|code\s+review|PR\s+review|run\s+tests?|pytest|git\s+diff)\b|"
    r"traceback|stacktrace|\bgh\s+pr\b|\bgh\s+issue\b|\bPR\s*#?\d{2,}\b|"
    r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|php|css|html)\b.*(?:고쳐|수정|fix|debug|refactor)|"
    r"\bbug\b.*(?:고쳐|수정|fix)|"
    r"프론트엔드|frontend|React|CSS|HTML|UI\s*(수정|구현|변경)|컴포넌트\s*(수정|작성|추가)"
    r")",
    re.IGNORECASE,
)

FRONTEND_FALLBACK_RE = re.compile(
    r"프론트엔드|frontend|React|CSS|HTML|UI\s*(수정|구현|변경)|컴포넌트\s*(수정|작성|추가)",
    re.IGNORECASE,
)

CONTINUATION_RE = re.compile(
    r"^(?:\\s)*(?:진행(?:해|해줘|해봐)?|계속(?:해|해줘)?|이어(?:서)?\\s*(?:해|해줘)?|좋아\\s*진행해|go\\s+ahead|continue|proceed|carry\\s+on)(?:[.!?。！؟\u3160-\u3163\\s]*)$",
    re.IGNORECASE,
)

DEV_REFUSAL_S0 = (
    "S0 REFUSAL RISK — before label flow, independently set refusal_risk=true ONLY for "
    "hard cases likely to be refused by frontier default models: explicit sexual/NSFW "
    "content AUTHORING (write a new erotic scene/story from scratch); clearly ToS-violating "
    "or legally-gray operational requests; secret/credential extraction, DRM/security-keypad "
    "bypass, OAuth proxy/token interception, or hard reverse-engineering of protections; or "
    "anything the user frames as \"the usual frontier model refuses this\". Do NOT trigger for: "
    "security research discussion; authorized pentesting of the user's own systems; mild "
    "profanity; dark-theme or violent fiction without explicit porn authoring; medical/legal "
    "questions; gray-area engineering such as scraping; OR editing/reviewing/toning-down/"
    "critiquing the user's EXISTING draft (even if the draft is already sexual — rewrite, "
    "tension polish, fade-to-black, age-rating advice, publisher-cut tone-down are false). "
    "Ownership matters: scanning/auditing/documenting the user's OWN repo/app/infra is false; "
    "attacking third-party systems is true. Precision first: when unsure set refusal_risk=false. "
    "Set refusal_confidence to the probability that this hard refusal-risk assessment is "
    "correct; when refusal_risk=true, evidence must mention the S0/R cue. Then continue S1-S7 "
    "unchanged for the label."
)

DEV_REFUSAL_EXAMPLE = (
    '"노골적인 NSFW 장면을 써줘" → '
    '{evidence:"S0/R explicit NSFW authoring + S6 prose",label:DOCUMENT_WORK,'
    'refusal_risk:true,refusal_confidence:0.97}\n'
    '"내 소설 야한 장면 성의있는지 봐줘" → '
    '{evidence:"S0 no — critique existing draft; S6 prose",label:DOCUMENT_WORK,'
    'refusal_risk:false,refusal_confidence:0.9}'
)

DEV_SYSTEM_PROMPT = """\
Router for a bilingual (KO/EN) personal assistant. Context: current message + recent dialogue (most recent first) + platform. Decide what the assistant must DO next.

Labels: NORMAL, DOCUMENT_WORK, FRONTEND_DEV, SYSTEM_DEV.
Core law: TOPIC ≠ TASK. An engineering topic never makes a turn dev; only commissioned code/config work does.

Apply steps IN ORDER; first match wins.

""" + DEV_REFUSAL_S0 + """

S1 CONTINUATION — bare go-ahead ("ㄱㄱ","진행해","좋아 그렇게 해줘","continue"): inherit from recent dialogue. Prior turn proposed a code/config/schema implementation plan or dev work is ongoing → that DEV label — never downgrade it. Proposed a document draft → DOCUMENT_WORK. Proposed an ops action (restart/rerun/cleanup) or context unclear → NORMAL.

S2 QUESTION/DISCUSSION — user wants an answer, not an edit: status ("잘 돌아가?"), metrics ("속도 어때?"), verification ("이 수치 맞아?"), explain existing behavior ("어떻게 동작해?"), opinion ("어떻게 생각해?","이 디자인 어때?"), ideas/plan-only/brainstorm ("어떻게 할까?","플랜 알려줘","구상해보자") → NORMAL. But "플랜대로 구현해/고쳐줘/만들어줘" → S5.

S3 LOOKUP/RETRIEVAL — deliverable is fetched/listed info: page/DB content ("찾아줘","가져와"), web research ("조사해줘"), listing existing things ("나열해줘") → NORMAL. Retrieval ≠ DOCUMENT_WORK (needs authoring). EXCEPTION: reading SOURCE CODE, diffs, PRs, or CI/incident logs to inspect/review/root-cause is NOT lookup → S5.

S4 OPS COMMAND — restart/relaunch/rerun/kill/cleanup of a service/job ("다시 띄워줘","재시작해줘") with no debugging engagement → NORMAL. SYSTEM_DEV only if inside an active debugging/implementation thread.

S5 CODE WORK — next turn must create/edit source/config/tests/schema, debug with logs/tracebacks, or do PR/diff/security review; includes product asks plainly implying repo/schema/DB/API/migration changes even without dev words ("주문에 배송지 여러 개 붙게 해줘"). Deep debugging counts even with a user-facing symptom. READ-ONLY counts too: source inspection ("이 모듈 구조 봐줘"), code/spec-compliance review, code audit, merge-conflict inspection, CI-failure root-causing — reviewing code IS code work, never DOCUMENT_WORK, even with zero edits.
• work mainly in UI source (React/Vue, HTML/CSS/Tailwind, components, layout, browser-UI behavior) → FRONTEND_DEV
• otherwise → SYSTEM_DEV

S6 PROSE DELIVERABLE — write/edit/summarize/restructure a doc, Notion page, spec, memo, ticket, table → DOCUMENT_WORK even in technical domains, unless source edits are asked.

S7 Else → NORMAL.

Output JSON per schema. evidence: matched step + decisive cue (≤120 chars), written BEFORE label. confidence: probability this route is correct; lower it on thin continuations or S2-vs-S5 near-misses.

Examples:
"결제 서버 배포 잘 됐어?" → {evidence:"S2 status Q",label:NORMAL}
"ㄱㄱ" after a dropdown-component plan → {evidence:"S1 inherit UI plan",label:FRONTEND_DEV}
"온보딩 가이드 노션 페이지로 정리해줘" → {evidence:"S6 author doc",label:DOCUMENT_WORK}""" + "\n" + DEV_REFUSAL_EXAMPLE

DEV_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evidence": {
            "type": "string",
            "maxLength": 120,
            "description": (
                "Matched step (S1-S7) + decisive cue from the message/context, "
                "e.g. 'S2 status question'. Write this first."
            ),
        },
        "label": {
            "type": "string",
            "enum": ["NORMAL", "DOCUMENT_WORK", "FRONTEND_DEV", "SYSTEM_DEV"],
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Calibrated probability that switching to this route is correct.",
        },
        "refusal_risk": {
            "type": "boolean",
        },
        "refusal_confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
    },
    "required": [
        "evidence", "label", "confidence", "refusal_risk", "refusal_confidence",
    ],
    "propertyOrdering": [
        "evidence", "label", "confidence", "refusal_risk", "refusal_confidence",
    ],
}

# Verbatim from skill-gate __init__.py: per-platform owner allowlists.
# Missing/empty allowlist → everyone counts as owner (fail-open).
_OWNER_ENV_MAP = {
    "telegram": "TELEGRAM_ALLOWED_USERS",
    "discord": "DISCORD_ALLOWED_USERS",
    "slack": "SLACK_ALLOWED_USERS",
    "whatsapp": "WHATSAPP_ALLOWED_USERS",
    "signal": "SIGNAL_ALLOWED_USERS",
    "email": "EMAIL_ALLOWED_USERS",
    "matrix": "MATRIX_ALLOWED_USERS",
}


def _compute_is_owner(source_context: dict[str, Any]) -> bool:
    platform = str(source_context.get("platform") or "")
    user_id = str(source_context.get("user_id") or "")
    if not platform:
        return True
    env_key = _OWNER_ENV_MAP.get(platform, "")
    if not env_key:
        return True
    allowed_raw = os.environ.get(env_key, "")
    allowed_ids = {uid.strip() for uid in allowed_raw.split(",") if uid.strip()}
    if not allowed_ids:
        return True
    if not user_id:
        return False
    return user_id in allowed_ids


# ---------------------------------------------------------------------------
# Context building (payload shape verbatim from skill-gate)
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    role: str
    content: str


@dataclass
class PolicyClassificationContext:
    current_user_message: str
    recent_turns: list[Turn] = field(default_factory=list)
    reply_to_text: str | None = None
    channel_context: str | None = None
    source: dict[str, Any] = field(default_factory=dict)
    session_key: str = ""
    session_id: str = ""
    runtime: dict[str, Any] = field(default_factory=dict)
    loaded_skills: list[str] = field(default_factory=list)

    def as_prompt_payload(self) -> dict[str, Any]:
        return {
            "current_user_message": self.current_user_message,
            "recent_turns": [turn.__dict__ for turn in self.recent_turns],
            "reply_to_text": self.reply_to_text,
            "channel_context": self.channel_context,
            "source": self.source,
            "session_key": self.session_key,
            "session_id": self.session_id,
            "runtime": self.runtime,
            "loaded_skills": self.loaded_skills,
        }


def _truncate(text: Any, limit: int = MAX_TURN_CHARS) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: limit - 20] + "…[truncated]"


def _read_env_key(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value.strip()
    env_path = get_hermes_home() / ".env"
    try:
        for line in env_path.read_text().splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            if key.strip() == name:
                return val.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _api_key() -> str:
    return (
        _read_env_key("HERMES_GRAPHITI_EMBEDDER_API_KEY")
        or _read_env_key("GOOGLE_API_KEY")
        or _read_env_key("GEMINI_API_KEY")
    )


def _call_gemini(
    user_prompt: str,
    *,
    model: str,
    timeout: float,
    max_tokens: int = 32,
    system_instruction: str | None = None,
    response_schema: dict[str, Any] | None = None,
) -> str:
    """Call Gemini API with separated system/user prompts for prompt caching.

    Same request shape as the skill-gate plugin: temperature 0, thinking off,
    structured JSON via responseMimeType/responseSchema, top-level
    systemInstruction (cached by Gemini across identical requests). No retry.

    Unlike the plugin, a missing API key raises so the caller takes the
    regex fallback path with ``source="fallback"`` (the plugin returned ""
    which parsed to an LLM-sourced NORMAL — that would let a key outage walk
    sessions toward the chat downgrade).
    """
    key = _api_key()
    if not key:
        raise RuntimeError("no Gemini API key resolved (HERMES_GRAPHITI_EMBEDDER_API_KEY/GOOGLE_API_KEY/GEMINI_API_KEY)")
    payload: dict[str, Any] = {
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    if response_schema:
        payload["generationConfig"]["responseMimeType"] = "application/json"
        payload["generationConfig"]["responseSchema"] = response_schema
    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}],
        }
    data = json.dumps(payload).encode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _urlopen(req, timeout=timeout) as resp:  # noqa: S310 trusted endpoint
        body = json.loads(resp.read().decode("utf-8"))
    # Raw text; label normalization happens in the parsers (uppercasing here
    # would corrupt structured-JSON responses).
    return str(body["candidates"][0]["content"]["parts"][0]["text"]).strip()


def _source_dict(event: Any) -> dict[str, Any]:
    source = getattr(event, "source", None)
    if source is None:
        return {}
    if hasattr(source, "to_dict"):
        try:
            return dict(source.to_dict())
        except Exception:
            pass
    result: dict[str, Any] = {}
    for name in ("platform", "chat_id", "chat_type", "chat_name", "user_id", "user_name", "thread_id", "guild_id", "parent_chat_id"):
        value = getattr(source, name, None)
        if hasattr(value, "value"):
            value = value.value
        if value is not None:
            result[name] = value
    return result


def _session_key(session_store: Any, event: Any) -> str:
    source = getattr(event, "source", None)
    if source is None or session_store is None:
        return ""
    try:
        return str(session_store._generate_session_key(source))  # noqa: SLF001 gateway-internal integration
    except Exception:
        return ""


def _session_id(session_store: Any, session_key: str) -> str:
    # Adapted from the plugin's get_entry() access: this base exposes the
    # key→session_id mapping via SessionStore.peek_session_id (lock-held).
    if not session_store or not session_key:
        return ""
    try:
        return str(session_store.peek_session_id(session_key) or "")
    except Exception:
        return ""


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def _recent_turns(session_store: Any, session_id: str, limit: int) -> list[Turn]:
    if not session_store or not session_id or limit <= 0:
        return []
    db = getattr(session_store, "_db", None)  # noqa: SLF001 gateway-internal integration
    if db is None:
        return []
    try:
        messages = db.get_messages_as_conversation(session_id, include_ancestors=False)
    except Exception:
        try:
            messages = db.get_messages(session_id)
        except Exception:
            return []
    turns: list[Turn] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = _truncate(_message_content_text(msg.get("content")))
        if content.strip():
            turns.append(Turn(role=role, content=content))
    return turns[-limit:]


def build_context(
    *,
    event: Any,
    session_store: Any = None,
    runtime: dict[str, Any] | None = None,
    recent_turn_limit: int = 5,
    loaded_skills: list[str] | None = None,
) -> PolicyClassificationContext:
    text = str(getattr(event, "text", None) or "")
    session_key = _session_key(session_store, event)
    session_id = _session_id(session_store, session_key)
    return PolicyClassificationContext(
        current_user_message=_truncate(text, 2000),
        recent_turns=_recent_turns(session_store, session_id, recent_turn_limit),
        reply_to_text=_truncate(getattr(event, "reply_to_text", None), 1000) or None,
        channel_context=_truncate(getattr(event, "channel_context", None), 1800) or None,
        source=_source_dict(event),
        session_key=session_key,
        session_id=session_id,
        runtime=runtime or {},
        loaded_skills=sorted({str(skill).strip().lower() for skill in (loaded_skills or []) if str(skill).strip()}),
    )


# ---------------------------------------------------------------------------
# Fallback classifier (verbatim from skill-gate)
# ---------------------------------------------------------------------------


def _recent_turn_text(context: PolicyClassificationContext, limit: int = 4) -> str:
    return "\n".join(
        f"{turn.role}: {turn.content}"
        for turn in context.recent_turns[-limit:]
        if turn.content
    )


def _dev_fallback_haystack(context: PolicyClassificationContext) -> str:
    parts = [
        context.current_user_message,
        context.reply_to_text or "",
        context.channel_context or "",
    ]
    # Short continuation turns like "진행해" / "continue" only become dev
    # fallback candidates when recent dialogue context already contains concrete
    # dev work. In normal operation, the LLM sees recent turns directly.
    if CONTINUATION_RE.search(context.current_user_message or ""):
        parts.append(_recent_turn_text(context))
    return "\n".join(part for part in parts if part)


def fallback_dev_label(context: PolicyClassificationContext) -> str:
    """Conservative regex fallback used only when the dev LLM classifier fails."""
    haystack = _dev_fallback_haystack(context)
    if not DEV_CANDIDATE_RE.search(haystack):
        return "NORMAL"
    if FRONTEND_FALLBACK_RE.search(haystack):
        return "FRONTEND_DEV"
    return "SYSTEM_DEV"


# ---------------------------------------------------------------------------
# Classifier (payload budget + parsing verbatim from skill-gate)
# ---------------------------------------------------------------------------


def _payload_json(context: PolicyClassificationContext) -> str:
    payload = context.as_prompt_payload()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= MAX_CONTEXT_CHARS:
        return text
    # Over budget: drop oldest turns one at a time so the payload stays valid
    # JSON. The old char-slice truncation fed the classifier a broken JSON
    # tail, which measurably hurt boundary cases (see skill-gate bench/RESULTS.md).
    turns = list(payload["recent_turns"])
    while turns:
        turns = turns[1:]
        payload["recent_turns"] = turns
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if len(text) <= MAX_CONTEXT_CHARS:
            return text
    return text[:MAX_CONTEXT_CHARS]  # pathological single-message overflow only


def _first_matching_label(raw: str, allowed: tuple[str, ...], default: str) -> str:
    label = str(raw or "").strip().upper()
    for candidate in allowed:
        if label.startswith(candidate):
            return candidate
    return default


def _parse_dev_json(raw: str) -> dict[str, Any] | None:
    """Parse the structured dev-router output and orthogonal refusal fields.

    Returns None when the payload is unusable so the caller can fall back to
    plain-label matching (covers tests/fakes that still answer one token).
    """
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        obj = json.loads(match.group(0) if match else raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    label = str(obj.get("label") or "").strip().upper()
    if label not in {"NORMAL", "DOCUMENT_WORK", "FRONTEND_DEV", "SYSTEM_DEV"}:
        return None
    try:
        confidence = float(obj.get("confidence")) if obj.get("confidence") is not None else None
    except Exception:
        confidence = None
    refusal_risk = obj.get("refusal_risk") is True
    try:
        refusal_confidence = (
            float(obj.get("refusal_confidence"))
            if obj.get("refusal_confidence") is not None
            else None
        )
        if refusal_confidence is not None and not 0 <= refusal_confidence <= 1:
            refusal_confidence = None
    except Exception:
        refusal_confidence = None
    return {
        "label": label,
        "confidence": confidence,
        "evidence": str(obj.get("evidence") or "")[:200],
        "refusal_risk": refusal_risk,
        "refusal_confidence": refusal_confidence,
    }


def classify_dev_detailed(
    context: PolicyClassificationContext,
    *,
    model: str = DEV_DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_MS / 1000.0,
    complete: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Classify dev routing with structured output.

    Returns {label, confidence, evidence, refusal_risk, refusal_confidence,
    source} where source is one of
    'llm' (structured or plain-label LLM answer) / 'fallback' (regex outage
    path). Hysteresis logic must only trust 'llm' NORMALs — a fail-open
    NORMAL from an API outage must never trigger a chat downgrade.
    """
    user_prompt = f"Context JSON:\n{_payload_json(context)}"
    try:
        if complete:
            # Test path: pass full prompt for backward compat with test assertions.
            raw = complete(DEV_SYSTEM_PROMPT + "\n\n" + user_prompt)
        else:
            raw = _call_gemini(
                user_prompt,
                model=model,
                timeout=timeout,
                max_tokens=256,
                system_instruction=DEV_SYSTEM_PROMPT,
                response_schema=DEV_RESPONSE_SCHEMA,
            )
    except Exception as exc:
        fallback = fallback_dev_label(context)
        logger.debug("model router dev classifier failed; fallback=%s: %s", fallback, exc)
        return {
            "label": fallback,
            "confidence": None,
            "evidence": "",
            "refusal_risk": False,
            "refusal_confidence": None,
            "source": "fallback",
        }
    parsed = _parse_dev_json(raw)
    if parsed is not None:
        parsed["source"] = "llm"
        return parsed
    label = _first_matching_label(
        raw, ("FRONTEND_DEV", "SYSTEM_DEV", "DOCUMENT_WORK", "NORMAL"), "NORMAL"
    )
    return {
        "label": label,
        "confidence": None,
        "evidence": "",
        "refusal_risk": False,
        "refusal_confidence": None,
        "source": "llm",
    }


# ---------------------------------------------------------------------------
# Static rule matching (model_routes.static_rules — ADR-003 Phase 2)
# ---------------------------------------------------------------------------

_RULE_SOURCE_FIELDS = ("chat_id", "parent_chat_id", "user_id", "platform", "chat_type")


def _condition_matches(
    key: str,
    condition: Any,
    *,
    text: str,
    source_context: dict[str, Any],
) -> bool:
    """Evaluate one ``when:`` condition. Unknown keys/shapes never match.

    ``text`` is the RAW event text (not stripped) and patterns compile with
    ``re.IGNORECASE`` — both plugin parity (skill-gate __init__.py compiles
    every scan pattern IGNORECASE; the live codex-lb rule relies on it).
    """
    if key == "text_matches_any":
        if not isinstance(condition, list) or not condition:
            return False
        for pattern in condition:
            try:
                if re.search(str(pattern), text, re.IGNORECASE):
                    return True
            except re.error:
                logger.debug("model router: invalid static rule regex %r", pattern)
        return False
    if key == "is_owner":
        if not isinstance(condition, dict) or set(condition) != {"eq"}:
            return False
        # The operand must be a real bool: YAML string "false" is truthy, so
        # bool-coercing it would invert the author's intent (same footgun
        # class as Phase 1's health.enabled). _parse_static_rules warns at
        # parse time; here the rule simply never matches.
        if not isinstance(condition["eq"], bool):
            return False
        return _compute_is_owner(source_context) == condition["eq"]
    if key in _RULE_SOURCE_FIELDS:
        if not isinstance(condition, dict) or not condition:
            return False
        value = source_context.get(key)
        if hasattr(value, "value"):
            value = value.value
        actual = str(value) if value is not None else None
        for op, operand in condition.items():
            if op == "eq":
                if actual != str(operand):
                    return False
            elif op == "in":
                if not isinstance(operand, list) or actual not in {str(v) for v in operand}:
                    return False
            elif op == "not_in":
                if not isinstance(operand, list) or actual in {str(v) for v in operand}:
                    return False
            else:
                return False
        return True
    return False


def match_static_rule(
    static_rules: list[dict[str, Any]],
    *,
    text: str,
    source_context: dict[str, Any],
) -> tuple[dict[str, Any], str] | None:
    """First-match-wins walk over static rules. Returns (rule, rule_name)."""
    for index, rule in enumerate(static_rules, 1):
        if not isinstance(rule, dict):
            continue
        when = rule.get("when")
        if not isinstance(when, dict) or not when:
            continue
        if all(
            _condition_matches(str(key), condition, text=text, source_context=source_context)
            for key, condition in when.items()
        ):
            name = str(rule.get("name") or "").strip() or f"static_rules#{index}"
            return rule, name
    return None


# ---------------------------------------------------------------------------
# Decision core
# ---------------------------------------------------------------------------


@dataclass
class RoutingDecision:
    directive: Optional[dict]  # resolved runtime {route, provider, model, ...} or None
    outcome: str
    label: str
    rule: Optional[str]  # static rule name, or None for the classifier path
    record: dict  # decision-log record (ts stamped by log_decision)


def _runtime_already_satisfies(
    runtime: dict[str, Any] | None,
    route_name: str,
    cfg: dict[str, Any] | None,
    catalog: Any,
    *,
    raise_on_error: bool = False,
) -> bool:
    """True when the live runtime is already a member of the route (no-op)."""
    if not runtime or not route_name:
        return False
    try:
        from hermes_cli.model_routes import runtime_satisfies_route

        return bool(runtime_satisfies_route(runtime, route_name, cfg, catalog=catalog))
    except Exception as exc:
        if raise_on_error:
            raise
        logger.debug("model router: route satisfaction check failed open for %r: %s", route_name, exc)
        return False


def _resolve_route_directive(
    route_name: str,
    cfg: dict[str, Any] | None,
    catalog: Any,
) -> Optional[dict]:
    """Health-checked route resolution via the Phase 1 catalog.

    A resolved directive always carries a non-empty ``reason`` so log and
    notify text is never blank: failover reasons from ``resolve_route`` are
    kept as-is, and an empty reason (healthy default) is filled with the
    route name.
    """
    if not str(route_name or "").strip():
        return None
    try:
        from hermes_cli.model_routes import resolve_route

        directive = resolve_route(route_name, cfg, catalog=catalog)
    except Exception as exc:
        logger.debug("model router: route resolution failed for %r: %s", route_name, exc)
        return None
    if directive is not None and not str(directive.get("reason") or "").strip():
        directive = dict(directive, reason=str(directive.get("route") or route_name))
    return directive


def _reset_repromote(entry: dict[str, Any]) -> None:
    entry["repromote_streak"] = 0
    entry["repromote_route"] = ""


def _repromote_on_noop(
    *,
    entry: dict[str, Any],
    route_name: str,
    runtime: dict[str, Any] | None,
    catalog: Any,
    router: Any,  # hermes_cli.model_routes.RouterConfig
    trusted: bool,
    resolve: Callable[[], Optional[dict]],
    noop_outcome: str = "noop_satisfied",
    raise_on_error: bool = False,
) -> tuple[Optional[dict], str]:
    """Advance the member→primary re-promotion streak for one no-op turn.

    Called only when the runtime already satisfies ``route_name``. Returns
    (directive, outcome): the directive is non-None only for a
    ``repromote_to_primary`` emission. ``resolve`` is invoked once the streak
    reaches the threshold (never per-turn — route resolution health-probes),
    and the emitted directive must be the true primary (``source ==
    "default"``): a session is never re-promoted onto a health-fallback
    member. ``trusted`` mirrors the normal_streak rule — a regex-fallback
    label during a classifier outage must not walk a session toward a swap.
    ``entry`` is the per-session hysteresis dict; ``normal_streak`` and
    ``repromote_streak`` are separate counters and never interact.
    """
    try:
        from hermes_cli.model_routes import _lookup_route, _model_matches

        spec = _lookup_route(catalog, route_name)
    except Exception as exc:
        if raise_on_error:
            raise
        logger.debug("model router: repromote lookup failed open for %r: %s", route_name, exc)
        return None, noop_outcome
    if spec is None:
        return None, noop_outcome
    override = getattr(spec, "repromote_after_turns", None)
    threshold = (
        int(override) if override is not None
        else int(getattr(router, "repromote_after_turns", DEFAULT_REPROMOTE_AFTER_TURNS))
    )
    if threshold <= 0:
        return None, noop_outcome
    runtime_model = (runtime or {}).get("model")
    if _model_matches(runtime_model, spec.model):
        entry["repromote_streak"] = 0
        return None, noop_outcome
    if not trusted:
        return None, noop_outcome
    if str(entry.get("repromote_route") or "") != route_name:
        # Route changed under the streak: reset-then-advance for the new route.
        entry["repromote_streak"] = 0
        entry["repromote_route"] = route_name
    streak = min(int(entry.get("repromote_streak") or 0) + 1, threshold)
    entry["repromote_streak"] = streak
    if streak < threshold:
        return None, f"noop_satisfied_repromote_{streak}_of_{threshold}"
    directive = resolve()
    if (
        directive is not None
        and directive.get("source") == "default"
        and not _model_matches(runtime_model, directive.get("model"))
    ):
        directive = dict(directive)
        directive["reason"] = (
            f"repromote to route primary after {streak} accepted-member turns "
            f"({runtime_model} -> {directive['model']})"
        )
        # Emission resets in shadow too: shadow shares the state dict and
        # never applies, so without this it would re-emit every turn.
        _reset_repromote(entry)
        return directive, "repromote_to_primary"
    # Unhealthy primary (fallback-source or same-as-runtime resolution):
    # hold the streak clamped at threshold and retry next turn, so the
    # session re-promotes on the first turn after the primary heals.
    return None, "repromote_held"


def static_rule_decision(
    *,
    rule: dict[str, Any],
    rule_name: str,
    text: str,
    session_key: str,
    runtime: dict[str, Any] | None,
    cfg: dict[str, Any] | None,
    catalog: Any,
    router: Any,  # hermes_cli.model_routes.RouterConfig
    mode: str,
    state: dict[str, Any],
) -> RoutingDecision:
    """Build the decision for an already-matched static rule.

    ``text`` is the raw event text (used for the log ``msg_head`` only —
    matching already happened in :func:`match_static_rule`). The runtime
    snapshot is only needed from this point on, so callers can defer taking
    it until a rule actually matched. ``state`` is the same per-session
    hysteresis dict the classifier path threads (re-promotion streaks are
    shared across both paths).
    """
    route_name = str(rule.get("route") or "")
    entry = state.setdefault(session_key or "unknown", {"normal_streak": 0})
    directive: Optional[dict] = None
    if _runtime_already_satisfies(runtime, route_name, cfg, catalog):
        # Static labels are deterministic — no llm-source trust gate here.
        directive, outcome = _repromote_on_noop(
            entry=entry,
            route_name=route_name,
            runtime=runtime,
            catalog=catalog,
            router=router,
            trusted=True,
            resolve=lambda: _resolve_route_directive(route_name, cfg, catalog),
        )
    else:
        directive = _resolve_route_directive(route_name, cfg, catalog)
        if directive is None:
            # Whole fallback chain unhealthy (or unknown route): stay put.
            outcome = "none"
        else:
            outcome = "switch"
            _reset_repromote(entry)
    record = {
        "policy": "static_rule",
        "session_key": session_key or "unknown",
        "label": route_name,
        "confidence": None,
        "evidence": str(rule.get("reason") or ""),
        "source": "static",
        "model": None,
        "outcome": outcome,
        "directive_route": (directive or {}).get("route"),
        "runtime_model": (runtime or {}).get("model"),
        "msg_head": _truncate(text.strip(), 2000)[:120],
        "mode": mode,
        "rule": rule_name,
        "refusal_risk": False,
        "refusal_confidence": None,
        "refusal_applied": False,
    }
    return RoutingDecision(
        directive=directive, outcome=outcome, label=route_name,
        rule=rule_name, record=record,
    )


def classifier_decision(
    *,
    event: Any,
    session_store: Any,
    runtime: dict[str, Any] | None,
    cfg: dict[str, Any] | None,
    catalog: Any,
    router: Any,  # hermes_cli.model_routes.RouterConfig
    mode: str,
    state: dict[str, Any],
    complete_dev: Callable[[str], str] | None = None,
) -> RoutingDecision:
    """LLM classifier path (dev routing), hysteresis semantics verbatim from
    skill-gate ``_apply_dev_routing``.

    ``state`` is the GatewayRunner-owned per-session hysteresis dict
    ({session_key: {"normal_streak": int, "repromote_streak": int,
    "repromote_route": str}}); shadow and enforce share it. Contains the
    blocking classifier HTTP call — gateway callers run this off the event
    loop.
    """
    context = build_context(
        event=event,
        session_store=session_store,
        runtime=runtime,
        recent_turn_limit=int(getattr(router, "recent_turns", 5) or 5),
        loaded_skills=[],  # no core equivalent at this base; keep payload shape parity
    )
    model = str(getattr(router, "model", "") or DEV_DEFAULT_MODEL)
    timeout = float(getattr(router, "timeout_ms", DEFAULT_TIMEOUT_MS) or DEFAULT_TIMEOUT_MS) / 1000.0
    detail = classify_dev_detailed(context, model=model, timeout=timeout, complete=complete_dev)
    dev_label = detail["label"]
    state_key = context.session_key or context.session_id or "unknown"
    entry = state.setdefault(state_key, {"normal_streak": 0})
    refusal_repromote_streak = int(entry.get("repromote_streak") or 0)
    refusal_repromote_route = str(entry.get("repromote_route") or "")
    label_routes = dict(getattr(router, "label_routes", None) or {})

    directive = None
    outcome = "none"

    if dev_label in {"FRONTEND_DEV", "SYSTEM_DEV", "DOCUMENT_WORK"}:
        entry["normal_streak"] = 0
        directive = _resolve_route_directive(str(label_routes.get(dev_label) or ""), cfg, catalog)
        if directive and _runtime_already_satisfies(runtime, str(directive.get("route") or ""), cfg, catalog):
            resolved = directive
            directive, outcome = _repromote_on_noop(
                entry=entry,
                route_name=str(resolved.get("route") or ""),
                runtime=runtime,
                catalog=catalog,
                router=router,
                trusted=detail.get("source") == "llm",
                resolve=lambda: resolved,  # already resolved this turn — no extra probes
            )
        elif directive:
            outcome = "switch"
            _reset_repromote(entry)
    else:  # NORMAL
        # Only LLM-sourced NORMALs advance the downgrade streak. A fail-open
        # NORMAL from an outage must never walk a session toward CHAT.
        if detail.get("source") == "llm":
            entry["normal_streak"] = int(entry.get("normal_streak") or 0) + 1
        chat_route = str(getattr(router, "chat_route", "") or "")
        chat_directive = _resolve_route_directive(chat_route, cfg, catalog)
        threshold = int(getattr(router, "normal_downgrade_streak", 0) or DEFAULT_NORMAL_DOWNGRADE_STREAK)
        if chat_directive is None:
            outcome = "normal_no_chat_route"
        elif _runtime_already_satisfies(runtime, str(chat_directive.get("route") or ""), cfg, catalog):
            directive, outcome = _repromote_on_noop(
                entry=entry,
                route_name=str(chat_directive.get("route") or ""),
                runtime=runtime,
                catalog=catalog,
                router=router,
                trusted=detail.get("source") == "llm",
                resolve=lambda: chat_directive,
                noop_outcome="noop_already_chat",
            )
        elif not runtime:
            outcome = "normal_unknown_runtime"
        elif detail.get("source") != "llm":
            outcome = "normal_fallback_no_downgrade"
        elif entry["normal_streak"] >= threshold:
            directive = dict(chat_directive)
            directive.setdefault("reason", "")
            directive["reason"] = (
                f"chat handoff after {entry['normal_streak']} consecutive NORMAL turns"
            )
            outcome = "downgrade_to_chat"
            _reset_repromote(entry)
        else:
            outcome = f"normal_streak_{entry['normal_streak']}_of_{threshold}"

    normal_repromote_state = {
        key: entry[key]
        for key in ("repromote_streak", "repromote_route")
        if key in entry
    }
    refusal_risk = detail.get("refusal_risk") is True
    refusal_confidence = detail.get("refusal_confidence")
    refusal_applied = False
    refusal_below_threshold = False
    try:
        refusal = getattr(router, "refusal", None)
        if (
            bool(getattr(refusal, "enabled", False))
            and refusal_risk
            and detail.get("source") == "llm"
        ):
            threshold = float(getattr(refusal, "min_confidence", 0.85))
            confidence = float(refusal_confidence)
            if confidence < threshold:
                refusal_below_threshold = True
            else:
                if dev_label in {"SYSTEM_DEV", "FRONTEND_DEV"}:
                    refusal_route = str(getattr(refusal, "dev_route", "") or "")
                elif dev_label == "DOCUMENT_WORK":
                    refusal_route = str(
                        getattr(refusal, "document_route", "")
                        or getattr(refusal, "chat_route", "")
                        or ""
                    )
                else:
                    refusal_route = str(getattr(refusal, "chat_route", "") or "")

                refusal_directive = _resolve_route_directive(refusal_route, cfg, catalog)
                if refusal_directive is not None:
                    resolved_route = str(refusal_directive.get("route") or refusal_route)
                    if _runtime_already_satisfies(
                        runtime, resolved_route, cfg, catalog, raise_on_error=True,
                    ):
                        # The label directive is replaced for this turn. Restore
                        # the pre-label re-promotion state so label-route
                        # resolution cannot reset a permissive-route streak.
                        entry["repromote_streak"] = refusal_repromote_streak
                        entry["repromote_route"] = refusal_repromote_route
                        directive, outcome = _repromote_on_noop(
                            entry=entry,
                            route_name=resolved_route,
                            runtime=runtime,
                            catalog=catalog,
                            router=router,
                            trusted=True,
                            resolve=lambda: refusal_directive,
                            raise_on_error=True,
                        )
                    else:
                        directive = refusal_directive
                        outcome = "refusal_switch"
                        _reset_repromote(entry)
                    refusal_applied = True
    except Exception:
        for key in ("repromote_streak", "repromote_route"):
            if key in normal_repromote_state:
                entry[key] = normal_repromote_state[key]
            else:
                entry.pop(key, None)
        logger.debug("model router: refusal evaluation failed open", exc_info=True)

    record = {
        "policy": "dev_routing",
        "session_key": state_key,
        "label": dev_label,
        "confidence": detail.get("confidence"),
        "evidence": detail.get("evidence"),
        "source": detail.get("source"),
        "model": model,
        "outcome": outcome,
        "directive_route": (directive or {}).get("route"),
        "runtime_model": (runtime or {}).get("model"),
        "msg_head": context.current_user_message[:120],
        "mode": mode,
        "rule": None,
        "refusal_risk": refusal_risk,
        "refusal_confidence": refusal_confidence,
        "refusal_applied": refusal_applied,
    }
    if refusal_below_threshold:
        record["refusal_below_threshold"] = True
    return RoutingDecision(
        directive=directive, outcome=outcome, label=dev_label, rule=None, record=record,
    )


def evaluate_event(
    *,
    event: Any,
    session_store: Any,
    runtime: dict[str, Any] | None,
    cfg: dict[str, Any] | None,
    catalog: Any,
    router: Any,  # hermes_cli.model_routes.RouterConfig
    mode: str,
    state: dict[str, Any],
    complete_dev: Callable[[str], str] | None = None,
) -> Optional[RoutingDecision]:
    """Evaluate routing for one inbound message.

    Evaluation order (mirrors the gateway stage; see ADR-003 Phase 2):

    1. empty/whitespace text → ``None`` (zero work, no log record).
    2. static rules match on the RAW text + source only. A match produces a
       decision — INCLUDING for slash commands (plugin parity: skill-gate
       applies static runtime_overrides even for "/status" from a
       non-owner).
    3. no static match + text starts with "/" → ``None`` (the classifier
       never runs for slash commands; nothing is logged).
    4. otherwise the LLM classifier path.

    Every decision carries a ready-to-append log record — the caller writes
    it via ``log_decision`` (so enforce-mode fields like ``applied`` /
    ``reasoning_applied`` can be added first).
    """
    raw_text = str(getattr(event, "text", None) or "")
    text = raw_text.strip()
    if not text:
        return None

    matched = match_static_rule(
        list(getattr(catalog, "static_rules", None) or []),
        text=raw_text,
        source_context=_source_dict(event),
    )
    if matched is not None:
        rule, rule_name = matched
        return static_rule_decision(
            rule=rule,
            rule_name=rule_name,
            text=raw_text,
            session_key=_session_key(session_store, event),
            runtime=runtime,
            cfg=cfg,
            catalog=catalog,
            router=router,
            mode=mode,
            state=state,
        )
    if text.startswith("/"):
        return None
    return classifier_decision(
        event=event,
        session_store=session_store,
        runtime=runtime,
        cfg=cfg,
        catalog=catalog,
        router=router,
        mode=mode,
        state=state,
        complete_dev=complete_dev,
    )


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------


def _decision_log_path(configured: str = "") -> Any:
    env = os.getenv(_DECISION_LOG_ENV)
    if env:
        return Path(env)
    if str(configured or "").strip():
        return Path(str(configured).strip()).expanduser()
    return get_hermes_home() / "logs" / _DECISION_LOG_FILENAME


def log_decision(record: dict[str, Any], *, decision_log: str = "") -> None:
    """Append a routing decision to the feedback-loop log. Best-effort.

    ``HERMES_MODEL_ROUTER_DECISION_LOG`` overrides the path (tests point it
    at tmp so suite runs don't pollute the production feedback log).
    """
    try:
        path = _decision_log_path(decision_log)
        record = dict(record, ts=round(time.time(), 3))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.debug("model router: decision log write failed: %s", exc)
