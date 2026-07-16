"""Tests for the dynamic model router (ADR-003 Phase 2).

Covers gateway/model_router.py (context payload parity, classifier fallback,
hysteresis, static rules) and the GatewayRunner pre-dispatch wiring
(shadow/enforce/off modes, env bridge, decision log isolation).

No network: the classifier is exercised either via the ``complete_dev`` seam
or by monkeypatching ``gateway.model_router._call_gemini`` /
``gateway.model_router._urlopen``. Health probes are neutralized by the
model_routes pytest guard. Decision logs go to tmp via
``HERMES_MODEL_ROUTER_DECISION_LOG``.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.model_router as mr_mod
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource
from hermes_cli.model_routes import load_routes


SKILL_GATE_DIR = Path("/home/ubuntu/.hermes/plugins/skill-gate")

EXPECTED_RECORD_FIELDS = {
    "policy", "session_key", "label", "confidence", "evidence", "source",
    "model", "outcome", "directive_route", "runtime_model", "msg_head",
    "mode", "rule",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _providers():
    return {
        "p1": {"base_url": "https://p1.example/v1"},
        "p2": {"base_url": "https://p2.example/v1"},
    }


def _cfg(*, router=None, static_rules=None):
    section = {
        "routes": {
            "dev": {
                "description": "dev route",
                "provider": "p1",
                "model": "model-a",
                "reasoning_effort": "xhigh",
            },
            "chat": {"description": "chat route", "provider": "p2", "model": "model-b"},
        },
    }
    if static_rules is not None:
        section["static_rules"] = static_rules
    section["router"] = dict(
        {
            "mode": "shadow",
            "model": "gemini-3-flash-preview",
            "timeout_ms": 8000,
            "recent_turns": 5,
            "normal_downgrade_streak": 3,
            "chat_route": "chat",
            "label_routes": {"SYSTEM_DEV": "dev", "FRONTEND_DEV": "dev", "DOCUMENT_WORK": "dev"},
        },
        **(router or {}),
    )
    return {"providers": _providers(), "model_routes": section}


def _catalog(cfg):
    catalog = load_routes(cfg)
    assert [i for i in catalog.issues if i.severity == "error"] == []
    return catalog


def _source(**kwargs) -> SessionSource:
    defaults = dict(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )
    defaults.update(kwargs)
    return SessionSource(**defaults)


def _event(text="hermes gateway 고장났어 디버깅해줘", **kwargs) -> MessageEvent:
    source = kwargs.pop("source", None) or _source()
    return MessageEvent(text=text, message_id="m1", source=source, **kwargs)


class _FakeDB:
    def __init__(self, messages):
        self.messages = list(messages)

    def get_messages_as_conversation(self, session_id, include_ancestors=False):
        return list(self.messages)


class _FakeStore:
    def __init__(self, key="tg:c1", session_id="sid-1", messages=()):
        self._key = key
        self._sid = session_id
        self._db = _FakeDB(messages)

    def _generate_session_key(self, source):
        return self._key

    def peek_session_id(self, session_key):
        return self._sid


def _complete(label, confidence=0.9, evidence="S5 test"):
    def _fn(prompt):
        return json.dumps({"evidence": evidence, "label": label, "confidence": confidence})
    return _fn


def _evaluate(
    *,
    text="status?",
    complete_dev=None,
    runtime=None,
    state=None,
    cfg=None,
    mode="shadow",
    store=None,
    source=None,
    event=None,
):
    cfg = cfg if cfg is not None else _cfg()
    catalog = _catalog(cfg)
    return mr_mod.evaluate_event(
        event=event or _event(text, source=source),
        session_store=store or _FakeStore(),
        # Default runtime is a full member of the "dev" route: the route
        # declares reasoning_effort xhigh, and legacy membership matches
        # effort-declaring specs against the runtime effort (B3 semantics).
        runtime=(
            {"model": "model-a", "provider": "p1", "reasoning_effort": "xhigh"}
            if runtime is None else runtime
        ),
        cfg=cfg,
        catalog=catalog,
        router=catalog.router,
        mode=mode,
        state={} if state is None else state,
        complete_dev=complete_dev,
    )


# ---------------------------------------------------------------------------
# Byte-identity parity with the skill-gate porting source (skipped where the
# read-only plugin checkout is not present)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (SKILL_GATE_DIR / "policy_router.py").exists(),
    reason="skill-gate plugin source not present on this host",
)
def test_verbatim_parity_with_skill_gate_plugin():
    import importlib.util
    import re as _re
    import sys

    spec = importlib.util.spec_from_file_location(
        "_sg_policy_router_for_parity", SKILL_GATE_DIR / "policy_router.py"
    )
    sg = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = sg
    try:
        spec.loader.exec_module(sg)
        assert mr_mod.DEV_SYSTEM_PROMPT == sg.DEV_SYSTEM_PROMPT
        assert mr_mod.DEV_RESPONSE_SCHEMA == sg.DEV_RESPONSE_SCHEMA
        assert mr_mod.DEV_CANDIDATE_RE.pattern == sg.DEV_CANDIDATE_RE.pattern
        assert mr_mod.DEV_CANDIDATE_RE.flags == sg.DEV_CANDIDATE_RE.flags
        assert mr_mod.FRONTEND_FALLBACK_RE.pattern == sg.FRONTEND_FALLBACK_RE.pattern
        assert mr_mod.CONTINUATION_RE.pattern == sg.CONTINUATION_RE.pattern
    finally:
        sys.modules.pop(spec.name, None)

    init_src = (SKILL_GATE_DIR / "__init__.py").read_text(encoding="utf-8")
    match = _re.search(r"_OWNER_ENV_MAP = \{(.*?)\}", init_src, _re.DOTALL)
    assert match is not None
    assert eval("{" + match.group(1) + "}") == mr_mod._OWNER_ENV_MAP


# ---------------------------------------------------------------------------
# Context payload shape + truncation budget
# ---------------------------------------------------------------------------


def test_payload_field_order_and_truncation():
    store = _FakeStore(messages=[
        {"role": "user", "content": "질문" * 700},          # > 1200 chars, truncated
        {"role": "tool", "content": "ignored role"},
        {"role": "assistant", "content": "답변"},
        {"role": "user", "content": ""},                    # empty, dropped
    ])
    event = _event(
        "x" * 3000,
        reply_to_text="r" * 2000,
        channel_context="c" * 4000,
    )
    context = mr_mod.build_context(
        event=event, session_store=store, runtime={"model": "m"}, recent_turn_limit=5,
    )
    payload = context.as_prompt_payload()
    assert list(payload) == [
        "current_user_message", "recent_turns", "reply_to_text", "channel_context",
        "source", "session_key", "session_id", "runtime", "loaded_skills",
    ]
    # _truncate keeps limit-20 chars + the 12-char "…[truncated]" marker.
    assert len(payload["current_user_message"]) == 2000 - 8
    assert payload["current_user_message"].endswith("…[truncated]")
    assert len(payload["reply_to_text"]) == 1000 - 8
    assert len(payload["channel_context"]) == 1800 - 8
    assert [t["role"] for t in payload["recent_turns"]] == ["user", "assistant"]
    assert len(payload["recent_turns"][0]["content"]) == 1200 - 8
    assert payload["session_key"] == "tg:c1"
    assert payload["session_id"] == "sid-1"
    assert payload["runtime"] == {"model": "m"}
    assert payload["loaded_skills"] == []  # always present, [] at this base


def test_payload_budget_drops_oldest_turns_and_stays_valid_json():
    turns = [
        {"role": "user", "content": f"turn-{i} " + "가" * 1100} for i in range(12)
    ]
    store = _FakeStore(messages=turns)
    context = mr_mod.build_context(
        event=_event("최근 메시지"), session_store=store, recent_turn_limit=12,
    )
    text = mr_mod._payload_json(context)
    assert len(text) <= mr_mod.MAX_CONTEXT_CHARS
    payload = json.loads(text)  # stays valid JSON (no char-slice tail)
    remaining = [t["content"].split()[0] for t in payload["recent_turns"]]
    assert remaining  # something survived
    # Oldest turns were dropped: the survivors are a suffix of the originals.
    assert remaining == [f"turn-{i}" for i in range(12 - len(remaining), 12)]


def test_recent_turns_limit_applied():
    store = _FakeStore(messages=[
        {"role": "user", "content": f"m{i}"} for i in range(10)
    ])
    context = mr_mod.build_context(event=_event("hi"), session_store=store, recent_turn_limit=3)
    assert [t.content for t in context.recent_turns] == ["m7", "m8", "m9"]


# ---------------------------------------------------------------------------
# Classifier + fallback
# ---------------------------------------------------------------------------


def test_classifier_llm_json_parsed():
    detail = mr_mod.classify_dev_detailed(
        mr_mod.PolicyClassificationContext(current_user_message="fix the bug"),
        complete=_complete("SYSTEM_DEV", confidence=0.83, evidence="S5 debug"),
    )
    assert detail == {
        "label": "SYSTEM_DEV", "confidence": 0.83, "evidence": "S5 debug", "source": "llm",
    }


def test_classifier_failure_falls_back_to_regex():
    def _boom(prompt):
        raise TimeoutError("classifier down")

    context = mr_mod.PolicyClassificationContext(
        current_user_message="gateway 고장났어 디버깅 좀"
    )
    detail = mr_mod.classify_dev_detailed(context, complete=_boom)
    assert detail["source"] == "fallback"
    assert detail["label"] == "SYSTEM_DEV"

    frontend = mr_mod.PolicyClassificationContext(current_user_message="React 컴포넌트 수정해줘")
    assert mr_mod.classify_dev_detailed(frontend, complete=_boom)["label"] == "FRONTEND_DEV"

    normal = mr_mod.PolicyClassificationContext(current_user_message="오늘 날씨 어때?")
    detail = mr_mod.classify_dev_detailed(normal, complete=_boom)
    assert detail == {"label": "NORMAL", "confidence": None, "evidence": "", "source": "fallback"}


def test_missing_api_key_takes_fallback_path(monkeypatch, tmp_path):
    # No complete seam → real _call_gemini path; no key anywhere → fallback.
    for name in ("HERMES_GRAPHITI_EMBEDDER_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    called = []
    monkeypatch.setattr(mr_mod, "_urlopen", lambda *a, **k: called.append(1))
    detail = mr_mod.classify_dev_detailed(
        mr_mod.PolicyClassificationContext(current_user_message="pytest 돌려서 fix 해줘")
    )
    assert called == []  # never reached the network seam
    assert detail["source"] == "fallback"


def test_call_gemini_request_shape(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({
                "candidates": [{"content": {"parts": [{"text": "NORMAL"}]}}]
            }).encode()

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(mr_mod, "_urlopen", fake_urlopen)
    raw = mr_mod._call_gemini(
        "Context JSON:\n{}",
        model="gemini-3-flash-preview",
        timeout=8.0,
        max_tokens=256,
        system_instruction=mr_mod.DEV_SYSTEM_PROMPT,
        response_schema=mr_mod.DEV_RESPONSE_SCHEMA,
    )
    assert raw == "NORMAL"
    assert "v1beta/models/gemini-3-flash-preview:generateContent" in captured["url"]
    assert captured["timeout"] == 8.0
    gen = captured["body"]["generationConfig"]
    assert gen["temperature"] == 0
    assert gen["thinkingConfig"] == {"thinkingBudget": 0}
    assert gen["maxOutputTokens"] == 256
    assert gen["responseMimeType"] == "application/json"
    assert gen["responseSchema"] == mr_mod.DEV_RESPONSE_SCHEMA
    assert captured["body"]["systemInstruction"] == {
        "parts": [{"text": mr_mod.DEV_SYSTEM_PROMPT}]
    }


def test_parse_dev_json_plain_token_fallback():
    assert mr_mod._parse_dev_json("FRONTEND_DEV") is None
    detail = mr_mod.classify_dev_detailed(
        mr_mod.PolicyClassificationContext(current_user_message="x"),
        complete=lambda prompt: "FRONTEND_DEV",
    )
    assert detail == {"label": "FRONTEND_DEV", "confidence": None, "evidence": "", "source": "llm"}


# ---------------------------------------------------------------------------
# Hysteresis ladder
# ---------------------------------------------------------------------------


def test_normal_streak_downgrades_after_threshold():
    state = {}
    outcomes = []
    for _ in range(3):
        decision = _evaluate(complete_dev=_complete("NORMAL"), state=state)
        outcomes.append(decision.outcome)
    assert outcomes == ["normal_streak_1_of_3", "normal_streak_2_of_3", "downgrade_to_chat"]
    final = _evaluate(complete_dev=_complete("NORMAL"), state=state)
    assert final.outcome == "downgrade_to_chat"
    assert final.directive["route"] == "chat"
    assert final.directive["model"] == "model-b"
    assert final.directive["reason"].startswith("chat handoff after 4 consecutive NORMAL turns")


def test_fallback_normal_never_advances_streak():
    state = {}

    def _boom(prompt):
        raise TimeoutError("down")

    for _ in range(5):
        decision = _evaluate(text="오늘 뭐 먹지?", complete_dev=_boom, state=state)
        assert decision.outcome == "normal_fallback_no_downgrade"
        assert decision.record["source"] == "fallback"
    assert state["tg:c1"]["normal_streak"] == 0


def test_dev_label_resets_streak():
    state = {}
    _evaluate(complete_dev=_complete("NORMAL"), state=state)
    _evaluate(complete_dev=_complete("NORMAL"), state=state)
    assert state["tg:c1"]["normal_streak"] == 2
    decision = _evaluate(complete_dev=_complete("SYSTEM_DEV"), state=state)
    assert decision.outcome == "noop_satisfied"  # runtime model-a is already dev
    assert state["tg:c1"]["normal_streak"] == 0
    # The next NORMAL starts a fresh streak.
    decision = _evaluate(complete_dev=_complete("NORMAL"), state=state)
    assert decision.outcome == "normal_streak_1_of_3"


def test_normal_outcomes_no_chat_route_and_unknown_runtime():
    cfg = _cfg(router={"chat_route": ""})
    decision = _evaluate(complete_dev=_complete("NORMAL"), cfg=cfg)
    assert decision.outcome == "normal_no_chat_route"
    assert decision.directive is None

    decision = _evaluate(complete_dev=_complete("NORMAL"), runtime={})
    assert decision.outcome == "normal_unknown_runtime"

    decision = _evaluate(
        complete_dev=_complete("NORMAL"), runtime={"model": "model-b", "provider": "p2"},
    )
    assert decision.outcome == "noop_already_chat"


def test_dev_switch_and_noop_and_unmapped_label():
    decision = _evaluate(
        complete_dev=_complete("SYSTEM_DEV"),
        runtime={"model": "model-b", "provider": "p2"},
    )
    assert decision.outcome == "switch"
    assert decision.directive["route"] == "dev"
    assert decision.directive["provider"] == "p1"
    assert decision.directive["model"] == "model-a"
    assert decision.directive["reasoning_effort"] == "xhigh"

    decision = _evaluate(complete_dev=_complete("SYSTEM_DEV"))  # runtime already dev
    assert decision.outcome == "noop_satisfied"
    assert decision.directive is None

    cfg = _cfg(router={"label_routes": {"SYSTEM_DEV": "dev"}})  # DOCUMENT_WORK unmapped
    decision = _evaluate(
        complete_dev=_complete("DOCUMENT_WORK"),
        runtime={"model": "model-b", "provider": "p2"},
        cfg=cfg,
    )
    assert decision.outcome == "none"
    assert decision.directive is None


def test_slash_command_and_empty_text_early_return():
    sentinel = MagicMock(side_effect=AssertionError("classifier must not run"))
    assert _evaluate(text="/model sonnet", complete_dev=sentinel) is None
    assert _evaluate(text="   ", complete_dev=sentinel) is None
    assert _evaluate(text="", complete_dev=sentinel) is None
    sentinel.assert_not_called()


def test_decision_record_schema():
    decision = _evaluate(complete_dev=_complete("SYSTEM_DEV"), mode="shadow")
    assert set(decision.record) == EXPECTED_RECORD_FIELDS
    assert decision.record["policy"] == "dev_routing"
    assert decision.record["mode"] == "shadow"
    assert decision.record["rule"] is None
    assert decision.record["model"] == "gemini-3-flash-preview"
    assert decision.record["runtime_model"] == "model-a"
    assert decision.record["msg_head"] == "status?"


# ---------------------------------------------------------------------------
# Static rules
# ---------------------------------------------------------------------------


def test_static_rule_first_match_wins_and_short_circuits(monkeypatch):
    rules = [
        {"name": "second", "route": "chat", "when": {"text_matches_any": ["never-matches"]}},
        {"name": "pr-rule", "route": "dev", "when": {"text_matches_any": [r"codex-lb\s+#?\d+"]}},
        {"name": "shadowed", "route": "chat", "when": {"text_matches_any": [r"codex-lb"]}},
    ]
    sentinel = MagicMock(side_effect=AssertionError("classifier must not run"))
    monkeypatch.setattr(mr_mod, "_call_gemini", sentinel)
    decision = _evaluate(
        text="codex-lb #123 리뷰해줘",
        cfg=_cfg(static_rules=rules),
        runtime={"model": "model-b", "provider": "p2"},
        complete_dev=None,
    )
    sentinel.assert_not_called()
    assert decision.rule == "pr-rule"
    assert decision.outcome == "switch"
    assert decision.directive["route"] == "dev"
    record = decision.record
    assert set(record) == EXPECTED_RECORD_FIELDS
    assert record["policy"] == "static_rule"
    assert record["source"] == "static"
    assert record["rule"] == "pr-rule"
    assert record["label"] == "dev"


def test_static_rule_noop_when_runtime_already_member():
    rules = [{"name": "pr-rule", "route": "dev", "when": {"text_matches_any": ["codex-lb"]}}]
    decision = _evaluate(
        text="codex-lb 확인해줘",
        cfg=_cfg(static_rules=rules),
        runtime={"model": "model-a", "provider": "p1", "reasoning_effort": "xhigh"},
        complete_dev=_complete("NORMAL"),
    )
    assert decision.rule == "pr-rule"
    assert decision.outcome == "noop_satisfied"
    assert decision.directive is None


def test_static_rule_is_owner_env_semantics(monkeypatch):
    rules = [{"name": "guard", "route": "chat", "when": {"is_owner": {"eq": False}}}]
    cfg = _cfg(static_rules=rules)
    runtime = {"model": "model-a", "provider": "p1"}

    # Allowlist set, sender not on it → not owner → rule matches.
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "999,888")
    decision = _evaluate(text="안녕", cfg=cfg, runtime=runtime, complete_dev=_complete("NORMAL"))
    assert decision.rule == "guard" and decision.outcome == "switch"

    # Sender on the allowlist → owner → falls through to the classifier.
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "u1,999")
    decision = _evaluate(text="안녕", cfg=cfg, runtime=runtime, complete_dev=_complete("NORMAL"))
    assert decision.rule is None

    # Missing/empty allowlist → everyone is owner (fail-open).
    monkeypatch.delenv("TELEGRAM_ALLOWED_USERS", raising=False)
    decision = _evaluate(text="안녕", cfg=cfg, runtime=runtime, complete_dev=_complete("NORMAL"))
    assert decision.rule is None

    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "   ,  ")
    decision = _evaluate(text="안녕", cfg=cfg, runtime=runtime, complete_dev=_complete("NORMAL"))
    assert decision.rule is None


def test_static_rule_source_field_conditions():
    rules = [{
        "name": "scoped",
        "route": "dev",
        "when": {
            "platform": {"eq": "telegram"},
            "chat_id": {"in": ["c1", "c2"]},
            "user_id": {"not_in": ["banned"]},
        },
    }]
    cfg = _cfg(static_rules=rules)
    runtime = {"model": "model-b", "provider": "p2"}
    decision = _evaluate(text="아무 텍스트", cfg=cfg, runtime=runtime, complete_dev=_complete("NORMAL"))
    assert decision.rule == "scoped" and decision.outcome == "switch"

    # AND semantics: one failing condition → no match.
    decision = _evaluate(
        text="아무 텍스트", cfg=cfg, runtime=runtime,
        source=_source(chat_id="c3"), complete_dev=_complete("NORMAL"),
    )
    assert decision.rule is None


def test_static_rule_unknown_condition_never_matches():
    assert mr_mod.match_static_rule(
        [{"route": "dev", "when": {"channel": "codex-lb-pr"}}],
        text="anything", source_context={"platform": "telegram"},
    ) is None


def test_static_rule_text_matches_ignorecase():
    # Plugin parity: skill-gate compiles every scan pattern with IGNORECASE;
    # the live codex-lb rule fails without it.
    rules = [{"name": "pr", "route": "dev", "when": {"text_matches_any": ["codex-lb"]}}]
    assert mr_mod.match_static_rule(
        rules, text="CODEX-LB #123 봐줘", source_context={},
    ) is not None
    assert mr_mod.match_static_rule(
        rules, text="Codex-Lb 상태 어때", source_context={},
    ) is not None


def test_static_rule_matches_raw_unstripped_text():
    # Plugin parity: matching runs on the RAW event text, so anchors can see
    # leading whitespace that .strip() would have removed.
    rules = [{"name": "ws", "route": "dev", "when": {"text_matches_any": [r"^\s+urgent"]}}]
    assert mr_mod.match_static_rule(rules, text="   urgent fix", source_context={}) is not None
    assert mr_mod.match_static_rule(rules, text="urgent fix", source_context={}) is None

    # evaluate_event feeds the raw text through to matching.
    decision = _evaluate(
        text="   urgent fix",
        cfg=_cfg(static_rules=[{"name": "ws", "route": "dev", "when": {"text_matches_any": [r"^\s+urgent"]}}]),
        runtime={"model": "model-b", "provider": "p2"},
        complete_dev=_complete("NORMAL"),
    )
    assert decision.rule == "ws" and decision.outcome == "switch"


def test_static_rule_is_owner_non_bool_operand_never_matches(monkeypatch):
    # YAML string "false" is truthy — bool-coercing it would invert the
    # author's intent, so a non-bool operand must never match (B2).
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "999")  # sender u1 is NOT owner
    rules = [{"name": "guard", "route": "chat", "when": {"is_owner": {"eq": "false"}}}]
    decision = _evaluate(
        text="안녕", cfg=_cfg(static_rules=rules),
        runtime={"model": "model-a", "provider": "p1"},
        complete_dev=_complete("NORMAL"),
    )
    assert decision.rule is None  # fell through to the classifier


def test_evaluate_event_static_rule_applies_to_slash_commands():
    # Plugin parity: static runtime_overrides apply even for "/status" from a
    # non-owner — only the CLASSIFIER is skipped for slash commands.
    rules = [{"name": "slash-pin", "route": "dev", "when": {"text_matches_any": [r"^/status"]}}]
    decision = _evaluate(
        text="/status",
        cfg=_cfg(static_rules=rules),
        runtime={"model": "model-b", "provider": "p2"},
        complete_dev=MagicMock(side_effect=AssertionError("classifier must not run")),
    )
    assert decision is not None
    assert decision.rule == "slash-pin"
    assert decision.outcome == "switch"
    assert decision.record["policy"] == "static_rule"


def test_switch_directive_reason_never_blank(monkeypatch):
    # B4: a healthy default resolution has an empty resolve_route reason —
    # the directive gets the route name so log/notify text is never blank.
    cfg = _cfg()
    directive = mr_mod._resolve_route_directive("dev", cfg, _catalog(cfg))
    assert directive["reason"] == "dev"

    decision = _evaluate(
        complete_dev=_complete("SYSTEM_DEV"),
        runtime={"model": "model-b", "provider": "p2"},
    )
    assert decision.outcome == "switch"
    assert decision.directive["reason"] == "dev"

    # Failover reasons from resolve_route are kept as-is.
    cfg2 = _cfg()
    cfg2["model_routes"]["routes"]["dev"]["fallbacks"] = [
        {"provider": "p2", "model": "model-c"},
    ]
    monkeypatch.setattr(
        "hermes_cli.model_routes.provider_health",
        lambda provider, model="", **kw: (
            (provider != "p1"), "HTTP 500" if provider == "p1" else "HTTP 200",
        ),
    )
    directive = mr_mod._resolve_route_directive("dev", cfg2, _catalog(cfg2))
    assert directive["model"] == "model-c"
    assert directive["reason"].startswith("failover")


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------


def test_log_decision_env_isolation_and_schema(monkeypatch, tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(log_path))
    decision = _evaluate(complete_dev=_complete("SYSTEM_DEV"), mode="shadow")
    mr_mod.log_decision(decision.record, decision_log="/nonexistent/ignored.jsonl")
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert set(record) == EXPECTED_RECORD_FIELDS | {"ts"}
    assert isinstance(record["ts"], float)
    assert record["mode"] == "shadow"


def test_log_decision_default_path_under_hermes_home(monkeypatch):
    from hermes_constants import get_hermes_home

    monkeypatch.delenv("HERMES_MODEL_ROUTER_DECISION_LOG", raising=False)
    mr_mod.log_decision({"policy": "dev_routing"})
    default = get_hermes_home() / "logs" / "model_router_decisions.jsonl"
    assert default.exists()
    assert json.loads(default.read_text().splitlines()[0])["policy"] == "dev_routing"


def test_log_decision_swallows_write_errors(monkeypatch, tmp_path):
    target = tmp_path / "not-a-dir"
    target.write_text("file blocks parent mkdir")
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(target / "x.jsonl"))
    mr_mod.log_decision({"policy": "dev_routing"})  # must not raise


# ---------------------------------------------------------------------------
# Gateway wiring (_model_router_stage / _handle_message)
# ---------------------------------------------------------------------------


def _make_runner(monkeypatch, cfg, *, runtime=("model-a", {"provider": "p1", "base_url": "https://p1.example/v1"})):
    from gateway.run import GatewayRunner
    import gateway.run as run_mod

    monkeypatch.setattr(run_mod, "_load_gateway_config", lambda: cfg)

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="tok")}
    )
    runner.adapters = {}
    runner.session_store = _FakeStore()
    # async_session_store is a property facade over session_store — assert
    # persistence through the underlying sync method.
    runner.session_store.set_model_override = MagicMock()
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._model_router_state = {}
    runner._evict_cached_agent = MagicMock()
    runner._resolve_session_agent_runtime = MagicMock(return_value=runtime)
    return runner


def test_shadow_mode_mutates_nothing_but_logs(monkeypatch, tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(log_path))
    cfg = _cfg()
    runner = _make_runner(
        monkeypatch, cfg, runtime=("model-b", {"provider": "p2", "base_url": "https://p2.example/v1"}),
    )
    monkeypatch.setattr(
        mr_mod, "_call_gemini",
        lambda *a, **k: json.dumps({"evidence": "S5", "label": "SYSTEM_DEV", "confidence": 0.9}),
    )
    switch_sentinel = MagicMock(side_effect=AssertionError("shadow must not switch"))
    monkeypatch.setattr("hermes_cli.model_switch.switch_model", switch_sentinel)

    asyncio.run(
        runner._model_router_stage(_event("gateway 버그 고쳐줘"), _source(), "tg:c1", mode="shadow")
    )

    assert runner._session_model_overrides == {}
    runner.session_store.set_model_override.assert_not_called()
    runner._evict_cached_agent.assert_not_called()
    switch_sentinel.assert_not_called()
    # Shadow still mutates its own streak dict (full-fidelity soak).
    assert runner._model_router_state["tg:c1"]["normal_streak"] == 0

    record = json.loads(log_path.read_text().splitlines()[0])
    assert record["mode"] == "shadow"
    assert record["outcome"] == "switch"
    assert record["label"] == "SYSTEM_DEV"
    assert record["directive_route"] == "dev"
    assert record["runtime_model"] == "model-b"
    assert set(record) == EXPECTED_RECORD_FIELDS | {"ts"}


def test_enforce_mode_applies_override_and_persists(monkeypatch, tmp_path):
    from hermes_cli.model_switch import ModelSwitchResult

    log_path = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(log_path))
    cfg = _cfg(router={"mode": "enforce"})
    runner = _make_runner(
        monkeypatch, cfg, runtime=("model-b", {"provider": "p2", "base_url": "https://p2.example/v1"}),
    )
    monkeypatch.setattr(
        mr_mod, "_call_gemini",
        lambda *a, **k: json.dumps({"evidence": "S5", "label": "SYSTEM_DEV", "confidence": 0.9}),
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.switch_model",
        MagicMock(return_value=ModelSwitchResult(
            success=True,
            new_model="model-a",
            target_provider="p1",
            api_key="sk-test",
            base_url="https://p1.example/v1",
            api_mode="chat_completions",
        )),
    )

    asyncio.run(
        runner._model_router_stage(_event("gateway 버그 고쳐줘"), _source(), "tg:c1", mode="enforce")
    )

    expected_override = {
        "model": "model-a",
        "provider": "p1",
        "api_key": "sk-test",
        "base_url": "https://p1.example/v1",
        "api_mode": "chat_completions",
    }
    assert runner._session_model_overrides["tg:c1"] == expected_override
    runner.session_store.set_model_override.assert_called_once_with(
        "tg:c1", expected_override,
    )
    runner._evict_cached_agent.assert_called_once_with("tg:c1")
    # Route carries reasoning_effort=xhigh and this base has session reasoning
    # override plumbing → applied and recorded.
    assert runner._session_reasoning_overrides["tg:c1"] == {"enabled": True, "effort": "xhigh"}

    record = json.loads(log_path.read_text().splitlines()[0])
    assert record["mode"] == "enforce"
    assert record["outcome"] == "switch"
    assert record["applied"] is True
    assert record["reasoning_applied"] is True
    assert set(record) == EXPECTED_RECORD_FIELDS | {"ts", "applied", "reasoning_applied"}


def test_enforce_failed_switch_leaves_state_untouched(monkeypatch, tmp_path):
    from hermes_cli.model_switch import ModelSwitchResult

    log_path = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(log_path))
    cfg = _cfg(router={"mode": "enforce"})
    runner = _make_runner(
        monkeypatch, cfg, runtime=("model-b", {"provider": "p2", "base_url": "https://p2.example/v1"}),
    )
    monkeypatch.setattr(
        mr_mod, "_call_gemini",
        lambda *a, **k: json.dumps({"evidence": "S5", "label": "SYSTEM_DEV", "confidence": 0.9}),
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.switch_model",
        MagicMock(return_value=ModelSwitchResult(success=False, error_message="no credentials")),
    )

    asyncio.run(
        runner._model_router_stage(_event("gateway 버그 고쳐줘"), _source(), "tg:c1", mode="enforce")
    )

    assert runner._session_model_overrides == {}
    runner.session_store.set_model_override.assert_not_called()
    runner._evict_cached_agent.assert_not_called()
    # Decision is still logged (one record per decision).
    record = json.loads(log_path.read_text().splitlines()[0])
    assert record["outcome"] == "switch"
    assert record["applied"] is False
    assert record["reasoning_applied"] is False


def test_stage_slash_command_writes_no_log(monkeypatch, tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(log_path))
    runner = _make_runner(monkeypatch, _cfg())
    sentinel = MagicMock(side_effect=AssertionError("classifier must not run"))
    monkeypatch.setattr(mr_mod, "_call_gemini", sentinel)

    asyncio.run(runner._model_router_stage(_event("/model sonnet"), _source(), "tg:c1", mode="shadow"))
    asyncio.run(runner._model_router_stage(_event("   "), _source(), "tg:c1", mode="shadow"))

    sentinel.assert_not_called()
    assert not log_path.exists()


def test_stage_empty_text_is_zero_work(monkeypatch, tmp_path):
    """Empty/whitespace events: no catalog parse, no snapshot, no thread, no log."""
    log_path = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(log_path))
    runner = _make_runner(monkeypatch, _cfg())
    parse_sentinel = MagicMock(side_effect=AssertionError("catalog must not parse"))
    monkeypatch.setattr("hermes_cli.model_routes.load_routes", parse_sentinel)
    runner._model_router_runtime_snapshot = MagicMock(
        side_effect=AssertionError("snapshot must not be taken"),
    )
    thread_sentinel = AsyncMock(side_effect=AssertionError("no thread dispatch"))
    monkeypatch.setattr("gateway.run.asyncio.to_thread", thread_sentinel)

    for text in ("", "   ", "\n\t"):
        asyncio.run(runner._model_router_stage(_event(text), _source(), "tg:c1", mode="enforce"))

    parse_sentinel.assert_not_called()
    runner._model_router_runtime_snapshot.assert_not_called()
    thread_sentinel.assert_not_called()
    assert not log_path.exists()


def test_stage_slash_without_static_match_no_snapshot_no_thread(monkeypatch, tmp_path):
    """Slash events still parse the catalog (static rules must be evaluated),
    but with no static match: no snapshot, no classifier, no thread, no log."""
    log_path = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(log_path))
    runner = _make_runner(monkeypatch, _cfg(static_rules=[
        {"name": "never", "route": "dev", "when": {"text_matches_any": ["will-not-match"]}},
    ]))
    runner._model_router_runtime_snapshot = MagicMock(
        side_effect=AssertionError("snapshot must not be taken for unmatched slash"),
    )
    monkeypatch.setattr(
        mr_mod, "classifier_decision",
        MagicMock(side_effect=AssertionError("classifier path must not run for slash")),
    )
    monkeypatch.setattr(
        mr_mod, "static_rule_decision",
        MagicMock(side_effect=AssertionError("no static decision without a match")),
    )
    thread_sentinel = AsyncMock(side_effect=AssertionError("no thread dispatch"))
    monkeypatch.setattr("gateway.run.asyncio.to_thread", thread_sentinel)

    asyncio.run(runner._model_router_stage(_event("/model sonnet"), _source(), "tg:c1", mode="enforce"))

    runner._model_router_runtime_snapshot.assert_not_called()
    thread_sentinel.assert_not_called()
    assert not log_path.exists()


def test_stage_slash_matching_static_rule_applies_override(monkeypatch, tmp_path):
    """A1/B parity: static rules run for slash commands — a matching rule in
    enforce mode applies the override even for '/status'."""
    from hermes_cli.model_switch import ModelSwitchResult

    log_path = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(log_path))
    cfg = _cfg(
        router={"mode": "enforce"},
        static_rules=[
            {"name": "slash-pin", "route": "chat", "when": {"text_matches_any": [r"^/status"]}},
        ],
    )
    runner = _make_runner(
        monkeypatch, cfg, runtime=("model-a", {"provider": "p1", "base_url": "https://p1.example/v1"}),
    )
    monkeypatch.setattr(
        mr_mod, "classifier_decision",
        MagicMock(side_effect=AssertionError("classifier must not run for slash")),
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.switch_model",
        MagicMock(return_value=ModelSwitchResult(
            success=True,
            new_model="model-b",
            target_provider="p2",
            api_key="sk-test",
            base_url="https://p2.example/v1",
            api_mode="chat_completions",
        )),
    )

    asyncio.run(runner._model_router_stage(_event("/status"), _source(), "tg:c1", mode="enforce"))

    assert runner._session_model_overrides["tg:c1"]["model"] == "model-b"
    runner._evict_cached_agent.assert_called_once_with("tg:c1")
    record = json.loads(log_path.read_text().splitlines()[0])
    assert record["policy"] == "static_rule"
    assert record["rule"] == "slash-pin"
    assert record["outcome"] == "switch"
    assert record["applied"] is True


def test_stage_takes_runtime_snapshot_on_event_loop(monkeypatch, tmp_path):
    """The snapshot must run on the loop (loop-atomic with the rehydrate
    check-then-assign), never inside asyncio.to_thread."""
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(tmp_path / "d.jsonl"))
    runner = _make_runner(monkeypatch, _cfg())
    monkeypatch.setattr(
        mr_mod, "_call_gemini",
        lambda *a, **k: json.dumps({"evidence": "S5", "label": "SYSTEM_DEV", "confidence": 0.9}),
    )
    seen = {}

    def _snapshot(source, session_key):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return {"model": "model-b", "provider": "p2"}

    runner._model_router_runtime_snapshot = _snapshot
    asyncio.run(runner._model_router_stage(_event("gateway 버그 고쳐줘"), _source(), "tg:c1", mode="shadow"))
    assert seen == {"on_loop": True}


def test_stage_routes_on_pre_hook_original_text(monkeypatch, tmp_path):
    """Plugin hooks may prepend advisories / rewrite event.text before the
    stage runs; the classifier must see the pre-hook original (bench inputs
    were stripped of injected advisories — they are label noise)."""
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(tmp_path / "d.jsonl"))
    runner = _make_runner(monkeypatch, _cfg())
    captured = {}

    def _fake_gemini(user_prompt, **kwargs):
        captured["prompt"] = user_prompt
        return json.dumps({"evidence": "S5", "label": "SYSTEM_DEV", "confidence": 0.9})

    monkeypatch.setattr(mr_mod, "_call_gemini", _fake_gemini)
    mutated = _event("[Learning reminder: consider the deploy skill]\n\ngateway 버그 고쳐줘")
    asyncio.run(runner._model_router_stage(
        mutated, _source(), "tg:c1", mode="shadow",
        original_text="gateway 버그 고쳐줘",
    ))
    assert "gateway 버그 고쳐줘" in captured["prompt"]
    assert "Learning reminder" not in captured["prompt"]
    record = json.loads((tmp_path / "d.jsonl").read_text().splitlines()[-1])
    assert record["msg_head"] == "gateway 버그 고쳐줘"


def test_enforce_apply_model_path_parity(monkeypatch, tmp_path):
    """A3: enforce apply mirrors /model — pending self-identification note
    (route + reason) and session-DB update_session_model persist."""
    from hermes_cli.model_switch import ModelSwitchResult

    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(tmp_path / "d.jsonl"))
    cfg = _cfg(router={"mode": "enforce"})
    runner = _make_runner(
        monkeypatch, cfg, runtime=("model-b", {"provider": "p2", "base_url": "https://p2.example/v1"}),
    )
    runner._session_db = SimpleNamespace(update_session_model=AsyncMock())
    runner.session_store.get_or_create_session = MagicMock(
        return_value=SimpleNamespace(session_id="sid-1"),
    )
    monkeypatch.setattr(
        mr_mod, "_call_gemini",
        lambda *a, **k: json.dumps({"evidence": "S5", "label": "SYSTEM_DEV", "confidence": 0.9}),
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.switch_model",
        MagicMock(return_value=ModelSwitchResult(
            success=True,
            new_model="model-a",
            target_provider="p1",
            api_key="sk-test",
            base_url="https://p1.example/v1",
            api_mode="chat_completions",
        )),
    )

    asyncio.run(
        runner._model_router_stage(_event("gateway 버그 고쳐줘"), _source(), "tg:c1", mode="enforce")
    )

    note = runner._pending_model_notes["tg:c1"]
    assert "switched" in note and "model-a" in note
    assert "route 'dev'" in note  # mentions the route name
    runner._session_db.update_session_model.assert_awaited_once_with("sid-1", "model-a")
    # A2 flag was recorded for the auto-reset boundary consume.
    assert "tg:c1" in runner._model_router_fresh_applies


def _applied_runner(monkeypatch, tmp_path):
    """Runner with a successful enforce apply already performed for tg:c1."""
    from hermes_cli.model_switch import ModelSwitchResult

    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(tmp_path / "d.jsonl"))
    cfg = _cfg(router={"mode": "enforce"})
    runner = _make_runner(
        monkeypatch, cfg, runtime=("model-b", {"provider": "p2", "base_url": "https://p2.example/v1"}),
    )
    monkeypatch.setattr(
        mr_mod, "_call_gemini",
        lambda *a, **k: json.dumps({"evidence": "S5", "label": "SYSTEM_DEV", "confidence": 0.9}),
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.switch_model",
        MagicMock(return_value=ModelSwitchResult(
            success=True,
            new_model="model-a",
            target_provider="p1",
            api_key="sk-test",
            base_url="https://p1.example/v1",
            api_mode="chat_completions",
        )),
    )
    asyncio.run(
        runner._model_router_stage(_event("gateway 버그 고쳐줘"), _source(), "tg:c1", mode="enforce")
    )
    assert runner._session_model_overrides["tg:c1"]["model"] == "model-a"
    return runner


def test_auto_reset_boundary_preserves_router_fresh_apply(monkeypatch, tmp_path):
    """A2 (#48031 class): an enforce apply from THIS turn survives the
    was_auto_reset cleanup — kept in memory and re-persisted to the new entry."""
    runner = _applied_runner(monkeypatch, tmp_path)
    assert "tg:c1" in runner._model_router_fresh_applies
    override = dict(runner._session_model_overrides["tg:c1"])
    runner.session_store.set_model_override.reset_mock()
    runner._model_router_state["tg:c1"] = {"normal_streak": 2}

    entry = SimpleNamespace(was_auto_reset=True)
    asyncio.run(runner._consume_auto_reset_boundary(entry, "tg:c1"))

    # Override survived in memory AND was re-persisted onto the new entry.
    assert runner._session_model_overrides["tg:c1"] == override
    runner.session_store.set_model_override.assert_called_once_with("tg:c1", override)
    # Router-applied reasoning override and pending note survive with it.
    assert runner._session_reasoning_overrides["tg:c1"] == {"enabled": True, "effort": "xhigh"}
    assert "tg:c1" in runner._pending_model_notes
    # Flag consumed (protects exactly one boundary), streak cleared (A5).
    assert "tg:c1" not in runner._model_router_fresh_applies
    assert "tg:c1" not in runner._model_router_state
    assert entry.was_auto_reset is False


def test_auto_reset_boundary_without_fresh_apply_clears_everything(monkeypatch):
    """Genuine conversation boundary (no router apply this turn): the cleanup
    drops overrides, reasoning, notes, and the router streak (A5)."""
    runner = _make_runner(monkeypatch, _cfg())
    runner._session_model_overrides["tg:c1"] = {"model": "old"}
    runner._session_reasoning_overrides["tg:c1"] = {"enabled": True, "effort": "low"}
    runner._pending_model_notes = {"tg:c1": "stale note"}
    runner._model_router_state["tg:c1"] = {"normal_streak": 2}

    entry = SimpleNamespace(was_auto_reset=True)
    asyncio.run(runner._consume_auto_reset_boundary(entry, "tg:c1"))

    assert runner._session_model_overrides == {}
    assert "tg:c1" not in runner._session_reasoning_overrides
    assert "tg:c1" not in runner._pending_model_notes
    assert "tg:c1" not in runner._model_router_state
    runner.session_store.set_model_override.assert_not_called()
    runner._evict_cached_agent.assert_called_once_with("tg:c1")
    assert entry.was_auto_reset is False


def test_new_command_clears_router_streak_and_flag(monkeypatch):
    """A5: the /new handler clears the router hysteresis streak (and any
    unconsumed fresh-apply flag) alongside the model/reasoning overrides."""
    runner = _make_runner(monkeypatch, _cfg())
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *a, **k: [])
    runner._invalidate_session_run_generation = MagicMock()
    runner._release_running_agent_state = MagicMock()
    runner._clear_session_boundary_security_state = MagicMock()
    runner.hooks = SimpleNamespace(emit=AsyncMock())
    runner._reset_notice_session_info = MagicMock(return_value="")
    runner._telegram_topic_new_header = MagicMock(return_value="")
    runner._is_telegram_topic_lane = MagicMock(return_value=False)
    runner._session_db = None
    runner.session_store._entries = {}
    runner.session_store.reset_session = MagicMock(
        return_value=SimpleNamespace(session_id="sid-2"),
    )
    runner._session_model_overrides["tg:c1"] = {"model": "old"}
    runner._model_router_state["tg:c1"] = {"normal_streak": 2}
    runner._model_router_fresh_applies = {"tg:c1"}

    asyncio.run(runner._handle_reset_command(_event("/new")))

    assert runner._session_model_overrides == {}
    assert "tg:c1" not in runner._model_router_state
    assert "tg:c1" not in runner._model_router_fresh_applies


# ---------------------------------------------------------------------------
# Mode resolution + env bridge + _handle_message integration
# ---------------------------------------------------------------------------


def test_model_router_mode_env_bridge(monkeypatch):
    import gateway.run as run_mod

    monkeypatch.setattr(run_mod, "_load_gateway_config", lambda: _cfg(router={"mode": "enforce"}))
    monkeypatch.delenv("HERMES_MODEL_ROUTER_MODE", raising=False)
    assert run_mod._model_router_mode() == "enforce"

    # Env wins when set — including forcing off and invalid values.
    monkeypatch.setenv("HERMES_MODEL_ROUTER_MODE", "shadow")
    assert run_mod._model_router_mode() == "shadow"
    monkeypatch.setenv("HERMES_MODEL_ROUTER_MODE", "off")
    assert run_mod._model_router_mode() == "off"
    monkeypatch.setenv("HERMES_MODEL_ROUTER_MODE", "bogus")
    assert run_mod._model_router_mode() == "off"

    # Config-first when env is unset; YAML-False and absent both mean off.
    monkeypatch.delenv("HERMES_MODEL_ROUTER_MODE", raising=False)
    monkeypatch.setattr(run_mod, "_load_gateway_config", lambda: {"model_routes": {"router": {"mode": False}}})
    assert run_mod._model_router_mode() == "off"
    monkeypatch.setattr(run_mod, "_load_gateway_config", lambda: {})
    assert run_mod._model_router_mode() == "off"


def _make_flow_runner(monkeypatch):
    """Minimal runner able to run _handle_message end-to-end (see
    tests/gateway/test_pre_gateway_dispatch.py idioms)."""
    from gateway.run import GatewayRunner

    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda name, **kw: [])

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.WHATSAPP: PlatformConfig(enabled=True)},
    )
    runner.adapters = {Platform.WHATSAPP: SimpleNamespace(send=AsyncMock())}
    runner.pairing_store = MagicMock()
    runner.session_store = MagicMock()
    runner._running_agents = {}
    runner._update_prompt_pending = {}

    async def _agent_leg(event, source, quick_key, run_generation):
        return "ok"

    runner._handle_message_with_agent = _agent_leg
    return runner


def _flow_event(text="hello there"):
    return MessageEvent(
        text=text,
        message_id="m1",
        source=SessionSource(
            platform=Platform.WHATSAPP,
            user_id="15551234567@s.whatsapp.net",
            chat_id="15551234567@s.whatsapp.net",
            user_name="tester",
            chat_type="dm",
        ),
    )


@pytest.mark.asyncio
async def test_handle_message_mode_off_zero_evaluation(monkeypatch):
    import gateway.run as run_mod
    from gateway.run import GatewayRunner

    monkeypatch.delenv("HERMES_MODEL_ROUTER_MODE", raising=False)
    monkeypatch.setattr(run_mod, "_load_gateway_config", lambda: {})
    stage = AsyncMock()
    monkeypatch.setattr(GatewayRunner, "_model_router_stage", stage)

    runner = _make_flow_runner(monkeypatch)
    await runner._handle_message(_flow_event())
    stage.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_env_shadow_invokes_stage(monkeypatch):
    import gateway.run as run_mod
    from gateway.run import GatewayRunner

    monkeypatch.setenv("HERMES_MODEL_ROUTER_MODE", "shadow")
    monkeypatch.setattr(run_mod, "_load_gateway_config", lambda: {})
    stage = AsyncMock()
    monkeypatch.setattr(GatewayRunner, "_model_router_stage", stage)

    runner = _make_flow_runner(monkeypatch)
    await runner._handle_message(_flow_event())
    stage.assert_awaited_once()
    assert stage.await_args.kwargs["mode"] == "shadow"


@pytest.mark.asyncio
async def test_handle_message_stage_failure_fails_open(monkeypatch):
    import gateway.run as run_mod
    from gateway.run import GatewayRunner

    monkeypatch.setenv("HERMES_MODEL_ROUTER_MODE", "shadow")
    monkeypatch.setattr(run_mod, "_load_gateway_config", lambda: {})
    monkeypatch.setattr(
        GatewayRunner, "_model_router_stage", AsyncMock(side_effect=RuntimeError("boom")),
    )

    runner = _make_flow_runner(monkeypatch)
    result = await runner._handle_message(_flow_event())
    assert result == "ok"  # dispatch still reached the agent leg


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["shadow", "enforce"])
async def test_handle_message_internal_event_skips_stage(monkeypatch, mode):
    """internal=True events never reach the router stage — in either mode."""
    import gateway.run as run_mod
    from gateway.run import GatewayRunner

    monkeypatch.setenv("HERMES_MODEL_ROUTER_MODE", mode)
    monkeypatch.setattr(run_mod, "_load_gateway_config", lambda: {})
    stage = AsyncMock()
    monkeypatch.setattr(GatewayRunner, "_model_router_stage", stage)

    runner = _make_flow_runner(monkeypatch)
    event = _flow_event("gateway 버그 고쳐줘")
    event.internal = True
    result = await runner._handle_message(event)
    stage.assert_not_awaited()
    assert result == "ok"  # dispatch itself is unaffected


@pytest.mark.asyncio
async def test_handle_message_running_agent_priority_skips_stage(monkeypatch):
    """A1 relocation: messages intercepted by the running-agent PRIORITY block
    (interrupt/steer/stop) are handled before — and without — the router stage."""
    import time

    import gateway.run as run_mod
    from gateway.run import GatewayRunner

    monkeypatch.setenv("HERMES_MODEL_ROUTER_MODE", "shadow")
    monkeypatch.setattr(run_mod, "_load_gateway_config", lambda: {})
    stage = AsyncMock()
    monkeypatch.setattr(GatewayRunner, "_model_router_stage", stage)

    runner = _make_flow_runner(monkeypatch)
    runner.session_store._generate_session_key = MagicMock(return_value="wa:c1")
    agent = MagicMock(spec=["interrupt", "get_activity_summary"])
    agent.get_activity_summary.return_value = {"seconds_since_activity": 0.0}
    runner._running_agents = {"wa:c1": agent}
    runner._running_agents_ts = {"wa:c1": time.time()}
    runner._session_has_compression_in_flight = AsyncMock(return_value=False)

    result = await runner._handle_message(_flow_event("잠깐, 그거 말고 이걸로 해줘"))
    assert result is None
    agent.interrupt.assert_called_once_with("잠깐, 그거 말고 이걸로 해줘")
    stage.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_slash_command_reaches_stage(monkeypatch):
    """A1 relocation: the stage runs before slash-command dispatch on the
    fresh path, so static rules can still see slash commands."""
    import gateway.run as run_mod
    from gateway.run import GatewayRunner

    monkeypatch.setenv("HERMES_MODEL_ROUTER_MODE", "shadow")
    monkeypatch.setattr(run_mod, "_load_gateway_config", lambda: {})
    stage = AsyncMock()
    monkeypatch.setattr(GatewayRunner, "_model_router_stage", stage)

    runner = _make_flow_runner(monkeypatch)
    await runner._handle_message(_flow_event("/zzz-not-a-real-command"))
    stage.assert_awaited_once()
    assert stage.await_args.kwargs["mode"] == "shadow"
