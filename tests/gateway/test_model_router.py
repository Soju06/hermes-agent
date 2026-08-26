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
import logging
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
    "mode", "rule", "refusal_risk", "refusal_confidence", "prior_refusal",
    "prior_refusal_confidence", "refusal_applied", "masked",
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
            "PERMISSIVE_DEV": {
                "description": "low-refusal dev route",
                "provider": "p1",
                "model": "kimi-k3",
            },
            "PERMISSIVE_CHAT": {
                "description": "low-refusal chat route",
                "provider": "p2",
                "model": "grok-4.5",
            },
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
        self.inactive_ids = set()

    def get_messages_as_conversation(self, session_id, include_ancestors=False):
        return [
            dict(message)
            for index, message in enumerate(self.messages, 1)
            if index not in self.inactive_ids
        ]

    def get_messages(self, session_id):
        return [
            {**message, "id": index, "active": 1}
            for index, message in enumerate(self.messages, 1)
            if index not in self.inactive_ids
        ]

    def latest_message_row_id(
        self, session_id, *, role="user", offset=0, require_text=True,
    ):
        matches = [
            message["id"]
            for message in self.get_messages(session_id)
            if message.get("role") == role
            and (
                not require_text
                or str(message.get("content") or "").strip()
            )
        ]
        return matches[-1 - offset] if len(matches) > offset else None

    def deactivate_messages(self, session_id, message_ids):
        before = len(self.inactive_ids)
        self.inactive_ids.update(
            message_id
            for message_id in message_ids
            if 1 <= message_id <= len(self.messages)
        )
        return len(self.inactive_ids) - before


class _FakeStore:
    def __init__(self, key="tg:c1", session_id="sid-1", messages=()):
        self._key = key
        self._sid = session_id
        self._db = _FakeDB(messages)
        self.transcript_ops = []

    def _generate_session_key(self, source):
        return self._key

    def peek_session_id(self, session_key):
        return self._sid

    def load_transcript(self, session_id):
        self.transcript_ops.append(("load", session_id))
        return list(self._db.messages)

    def rewrite_transcript(self, session_id, messages):
        self.transcript_ops.append(("rewrite", session_id))
        self._db.messages = list(messages)
        return True

    def deactivate_messages(self, session_id, message_ids):
        self.transcript_ops.append(("deactivate", session_id, tuple(message_ids)))
        return self._db.deactivate_messages(session_id, message_ids)


def _complete(
    label,
    confidence=0.9,
    evidence="S5 test",
    *,
    refusal_risk=None,
    refusal_confidence=None,
    prior_refusal=None,
    prior_refusal_confidence=None,
):
    def _fn(prompt):
        result = {"evidence": evidence, "label": label, "confidence": confidence}
        if refusal_risk is not None:
            result["refusal_risk"] = refusal_risk
            result["refusal_confidence"] = refusal_confidence
        if prior_refusal is not None:
            result["prior_refusal"] = prior_refusal
            result["prior_refusal_confidence"] = prior_refusal_confidence
        return json.dumps(result)
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
        old_prompt = mr_mod.DEV_SYSTEM_PROMPT.replace(
            mr_mod.DEV_REFUSAL_S0 + "\n\n", "", 1,
        ).replace(
            mr_mod.DEV_PRIOR_REFUSAL_S0B + "\n\n", "", 1,
        ).replace("\n" + mr_mod.DEV_REFUSAL_EXAMPLE, "", 1)
        assert old_prompt == sg.DEV_SYSTEM_PROMPT
        old_schema = json.loads(json.dumps(mr_mod.DEV_RESPONSE_SCHEMA))
        old_schema["properties"].pop("refusal_risk")
        old_schema["properties"].pop("refusal_confidence")
        old_schema["properties"].pop("prior_refusal")
        old_schema["properties"].pop("prior_refusal_confidence")
        old_schema["required"] = ["evidence", "label", "confidence"]
        old_schema["propertyOrdering"] = ["evidence", "label", "confidence"]
        assert old_schema == sg.DEV_RESPONSE_SCHEMA
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
        "label": "SYSTEM_DEV", "confidence": 0.83, "evidence": "S5 debug",
        "refusal_risk": False, "refusal_confidence": None,
        "prior_refusal": False, "prior_refusal_confidence": None,
        "source": "llm",
    }


def test_classifier_refusal_fields_parsed_and_normalized():
    detail = mr_mod.classify_dev_detailed(
        mr_mod.PolicyClassificationContext(current_user_message="write explicit NSFW copy"),
        complete=_complete(
            "DOCUMENT_WORK",
            evidence="S0 explicit NSFW authoring + S6 prose",
            refusal_risk=True,
            refusal_confidence="0.93",
        ),
    )
    assert detail["refusal_risk"] is True
    assert detail["refusal_confidence"] == 0.93


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
    assert detail == {
        "label": "NORMAL", "confidence": None, "evidence": "",
        "refusal_risk": False, "refusal_confidence": None,
        "prior_refusal": False, "prior_refusal_confidence": None,
        "source": "fallback",
    }


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


def test_thinking_config_by_model_family():
    expected = {
        "gemini-3-flash-preview": {"thinkingBudget": 0},
        "gemini-3.1-flash-lite": {"thinkingBudget": 0},
        "gemini-2.5-flash": {"thinkingBudget": 0},
        "gemini-3.5-flash-lite": {"thinkingLevel": "low"},
        "gemini-3.6-flash": {"thinkingLevel": "low"},
        "gemini-3.7-flash": {"thinkingLevel": "low"},
        "weird-model": {"thinkingLevel": "low"},
    }
    for model, config in expected.items():
        assert mr_mod._thinking_config(model) == config


def test_call_gemini_uses_thinking_level_for_3_5(monkeypatch):
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
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    monkeypatch.setattr(mr_mod, "_urlopen", fake_urlopen)
    mr_mod._call_gemini(
        "Context JSON:\n{}",
        model="gemini-3.5-flash-lite",
        timeout=8.0,
    )
    assert captured["body"]["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "low"
    }


def test_parse_dev_json_plain_token_fallback():
    assert mr_mod._parse_dev_json("FRONTEND_DEV") is None
    detail = mr_mod.classify_dev_detailed(
        mr_mod.PolicyClassificationContext(current_user_message="x"),
        complete=lambda prompt: "FRONTEND_DEV",
    )
    assert detail == {
        "label": "FRONTEND_DEV", "confidence": None, "evidence": "",
        "refusal_risk": False, "refusal_confidence": None,
        "prior_refusal": False, "prior_refusal_confidence": None,
        "source": "llm",
    }


def test_dev_schema_refusal_fields_are_required_and_ordered():
    schema = mr_mod.DEV_RESPONSE_SCHEMA
    assert schema["required"] == [
        "evidence", "label", "confidence", "refusal_risk", "refusal_confidence",
        "prior_refusal", "prior_refusal_confidence",
    ]
    assert schema["propertyOrdering"] == schema["required"]
    assert schema["properties"]["refusal_risk"] == {"type": "boolean"}
    assert schema["properties"]["refusal_confidence"] == {
        "type": "number", "minimum": 0, "maximum": 1,
    }
    assert schema["properties"]["prior_refusal"] == {"type": "boolean"}
    assert schema["properties"]["prior_refusal_confidence"] == {
        "type": "number", "minimum": 0, "maximum": 1,
    }


def test_prior_refusal_fields_parse_and_default_safely():
    parsed = mr_mod._parse_dev_json(json.dumps({
        "evidence": "S0b prior refusal",
        "label": "SYSTEM_DEV",
        "confidence": 0.9,
        "prior_refusal": True,
        "prior_refusal_confidence": 0.96,
    }))
    assert parsed["prior_refusal"] is True
    assert parsed["prior_refusal_confidence"] == 0.96

    defaults = mr_mod._parse_dev_json(json.dumps({
        "evidence": "S5",
        "label": "SYSTEM_DEV",
        "confidence": 0.9,
    }))
    assert defaults["prior_refusal"] is False
    assert defaults["prior_refusal_confidence"] is None

    invalid = mr_mod._parse_dev_json(json.dumps({
        "evidence": "S0b",
        "label": "SYSTEM_DEV",
        "confidence": 0.9,
        "prior_refusal": True,
        "prior_refusal_confidence": 1.1,
    }))
    assert invalid["prior_refusal"] is True
    assert invalid["prior_refusal_confidence"] is None


# ---------------------------------------------------------------------------
# Refusal-risk routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "refusal_route"),
    [
        ("SYSTEM_DEV", "PERMISSIVE_DEV"),
        ("FRONTEND_DEV", "PERMISSIVE_DEV"),
        ("DOCUMENT_WORK", "PERMISSIVE_CHAT"),
        ("NORMAL", "PERMISSIVE_CHAT"),
    ],
)
@pytest.mark.parametrize("refusal_risk", [False, True])
@pytest.mark.parametrize("refusal_confidence", [0.84, 0.9])
@pytest.mark.parametrize("enabled", [False, True])
def test_refusal_routing_matrix(
    label, refusal_route, refusal_risk, refusal_confidence, enabled,
):
    cfg = _cfg(router={
        "refusal": {
            "enabled": enabled,
            "min_confidence": 0.85,
            "dev_route": "PERMISSIVE_DEV",
            "chat_route": "PERMISSIVE_CHAT",
            "document_route": "",
        },
    })
    decision = _evaluate(
        complete_dev=_complete(
            label,
            refusal_risk=refusal_risk,
            refusal_confidence=refusal_confidence,
        ),
        runtime={"model": "model-z", "provider": "p1"},
        cfg=cfg,
    )
    should_route = enabled and refusal_risk and refusal_confidence >= 0.85
    if should_route:
        assert decision.outcome == "refusal_switch"
        assert decision.directive["route"] == refusal_route
        assert decision.record["refusal_applied"] is True
    else:
        expected_route = None if label == "NORMAL" else "dev"
        assert (decision.directive or {}).get("route") == expected_route
        assert decision.record["refusal_applied"] is False
    assert decision.record["refusal_risk"] is refusal_risk
    assert decision.record["refusal_confidence"] == refusal_confidence
    assert decision.record.get("refusal_below_threshold") is (
        True if enabled and refusal_risk and refusal_confidence < 0.85 else None
    )


def test_refusal_document_route_override():
    cfg = _cfg(router={
        "refusal": {
            "enabled": True,
            "document_route": "PERMISSIVE_DEV",
        },
    })
    decision = _evaluate(
        complete_dev=_complete(
            "DOCUMENT_WORK", refusal_risk=True, refusal_confidence=0.91,
        ),
        runtime={"model": "model-z", "provider": "p1"},
        cfg=cfg,
    )
    assert decision.outcome == "refusal_switch"
    assert decision.directive["route"] == "PERMISSIVE_DEV"


def test_prior_soft_refusal_masks_newest_assistant_and_routes_permissive():
    store = _FakeStore(messages=[
        {"role": "user", "content": "build this"},
        {"role": "assistant", "content": "I cannot help with that request."},
    ])
    decision = _evaluate(
        complete_dev=_complete(
            "SYSTEM_DEV",
            prior_refusal=True,
            prior_refusal_confidence=0.94,
        ),
        runtime={"model": "model-z", "provider": "p1"},
        cfg=_cfg(router={"refusal": {"enabled": True, "min_confidence": 0.85}}),
        store=store,
        mode="enforce",
    )
    assert decision.outcome == "refusal_switch"
    assert decision.directive["route"] == "PERMISSIVE_DEV"
    assert decision.record["masked"] == 1
    assert store._db.inactive_ids == {2}
    assert store.transcript_ops == [("deactivate", "sid-1", (2,))]


def test_prior_soft_refusal_below_threshold_does_not_mask_or_switch():
    store = _FakeStore(messages=[
        {"role": "user", "content": "build this"},
        {"role": "assistant", "content": "I cannot help with that request."},
    ])
    decision = _evaluate(
        complete_dev=_complete(
            "SYSTEM_DEV",
            prior_refusal=True,
            prior_refusal_confidence=0.84,
        ),
        runtime={"model": "model-z", "provider": "p1"},
        cfg=_cfg(router={"refusal": {"enabled": True, "min_confidence": 0.85}}),
        store=store,
        mode="enforce",
    )
    assert decision.outcome == "switch"
    assert decision.directive["route"] == "dev"
    assert decision.record["masked"] == 0
    assert decision.record["prior_refusal_below_threshold"] is True
    assert store._db.inactive_ids == set()


def test_mask_on_refusal_false_keeps_route_behavior_without_masking():
    store = _FakeStore(messages=[
        {"role": "user", "content": "build this"},
        {"role": "assistant", "content": "I cannot help with that request."},
    ])
    decision = _evaluate(
        complete_dev=_complete(
            "SYSTEM_DEV",
            prior_refusal=True,
            prior_refusal_confidence=0.94,
        ),
        runtime={"model": "model-z", "provider": "p1"},
        cfg=_cfg(router={"refusal": {
            "enabled": True,
            "mask_on_refusal": False,
        }}),
        store=store,
        mode="enforce",
    )
    assert decision.outcome == "refusal_switch"
    assert decision.directive["route"] == "PERMISSIVE_DEV"
    assert decision.record["masked"] == 0
    assert store._db.inactive_ids == set()


def test_forced_refusal_route_is_one_shot():
    state = {
        "tg:c1": {
            "normal_streak": 0,
            "force_refusal_route": True,
            "force_refusal_reason": "prior_turn_refused",
        },
    }
    cfg = _cfg(router={"refusal": {"enabled": True}})
    first = _evaluate(
        complete_dev=_complete("SYSTEM_DEV"),
        runtime={"model": "model-z", "provider": "p1"},
        cfg=cfg,
        state=state,
    )
    assert first.outcome == "refusal_switch"
    assert first.directive["route"] == "PERMISSIVE_DEV"
    assert first.record["forced_refusal_route"] is True
    assert "force_refusal_route" not in state["tg:c1"]
    assert "force_refusal_reason" not in state["tg:c1"]

    second = _evaluate(
        complete_dev=_complete("SYSTEM_DEV"),
        runtime={"model": "model-z", "provider": "p1"},
        cfg=cfg,
        state=state,
    )
    assert second.outcome == "switch"
    assert second.directive["route"] == "dev"


def test_forced_refusal_route_clears_even_when_refusal_is_disabled():
    state = {
        "tg:c1": {
            "normal_streak": 0,
            "force_refusal_route": True,
            "force_refusal_reason": "prior_turn_refused",
        },
    }
    decision = _evaluate(
        complete_dev=_complete("NORMAL"),
        runtime={"model": "model-z", "provider": "p1"},
        cfg=_cfg(router={"refusal": {"enabled": False}}),
        state=state,
    )
    assert decision.outcome != "refusal_switch"
    assert "force_refusal_route" not in state["tg:c1"]
    assert "force_refusal_reason" not in state["tg:c1"]


def test_refusal_fallback_source_never_routes(monkeypatch):
    monkeypatch.setattr(mr_mod, "classify_dev_detailed", lambda *a, **k: {
        "label": "SYSTEM_DEV",
        "confidence": 0.99,
        "evidence": "S0 hard cue + S5 code",
        "refusal_risk": True,
        "refusal_confidence": 0.99,
        "source": "fallback",
    })
    decision = _evaluate(
        cfg=_cfg(router={"refusal": {"enabled": True}}),
        runtime={"model": "model-z", "provider": "p1"},
    )
    assert decision.outcome == "switch"
    assert decision.directive["route"] == "dev"
    assert decision.record["refusal_applied"] is False


def test_refusal_evaluation_exception_keeps_normal_routing(monkeypatch):
    original = mr_mod._resolve_route_directive

    def _resolve(route_name, cfg, catalog):
        if route_name == "PERMISSIVE_DEV":
            raise RuntimeError("refusal route lookup failed")
        return original(route_name, cfg, catalog)

    monkeypatch.setattr(mr_mod, "_resolve_route_directive", _resolve)
    decision = _evaluate(
        complete_dev=_complete(
            "SYSTEM_DEV", refusal_risk=True, refusal_confidence=0.99,
        ),
        cfg=_cfg(router={"refusal": {"enabled": True}}),
        runtime={"model": "model-z", "provider": "p1"},
    )
    assert decision.outcome == "switch"
    assert decision.directive["route"] == "dev"
    assert decision.record["refusal_applied"] is False


def test_refusal_membership_exception_keeps_normal_routing(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.model_routes.runtime_satisfies_route",
        MagicMock(side_effect=RuntimeError("membership lookup failed")),
    )
    decision = _evaluate(
        complete_dev=_complete(
            "SYSTEM_DEV", refusal_risk=True, refusal_confidence=0.99,
        ),
        cfg=_cfg(router={"refusal": {"enabled": True}}),
        runtime={"model": "model-z", "provider": "p1"},
    )
    assert decision.outcome == "switch"
    assert decision.directive["route"] == "dev"
    assert decision.record["refusal_applied"] is False


def test_refusal_route_membership_is_absorbing_and_repromotes():
    cfg = _cfg(router={"refusal": {"enabled": True}})
    cfg["model_routes"]["routes"]["PERMISSIVE_DEV"]["accepted"] = [
        "kimi-k3", "permissive-member",
    ]
    state = {}
    outcomes = []
    for _ in range(3):
        decision = _evaluate(
            complete_dev=_complete(
                "SYSTEM_DEV", refusal_risk=True, refusal_confidence=0.99,
            ),
            cfg=cfg,
            state=state,
            runtime={"model": "permissive-member", "provider": "p1"},
        )
        outcomes.append(decision.outcome)
        assert decision.record["refusal_applied"] is True
    assert outcomes == [
        "noop_satisfied_repromote_1_of_3",
        "noop_satisfied_repromote_2_of_3",
        "repromote_to_primary",
    ]
    assert decision.directive["route"] == "PERMISSIVE_DEV"
    assert decision.directive["model"] == "kimi-k3"


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
# Re-promotion hysteresis (member → route primary)
# ---------------------------------------------------------------------------


def _member_cfg(*, router=None, static_rules=None):
    """_cfg with non-primary accepted members: model-alt on dev, grok-x on chat."""
    cfg = _cfg(router=router, static_rules=static_rules)
    cfg["model_routes"]["routes"]["dev"]["accepted"] = ["model-a", "model-alt"]
    cfg["model_routes"]["routes"]["chat"]["accepted"] = ["model-b", "grok-x"]
    return cfg


_MEMBER_RUNTIME = {"model": "model-alt", "provider": "p1"}


def test_repromote_streak_advances_and_emits_at_threshold():
    state = {}
    cfg = _member_cfg()
    outcomes = []
    for _ in range(3):
        decision = _evaluate(
            complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME,
        )
        outcomes.append(decision.outcome)
    assert outcomes == [
        "noop_satisfied_repromote_1_of_3",
        "noop_satisfied_repromote_2_of_3",
        "repromote_to_primary",
    ]
    assert decision.directive["route"] == "dev"
    assert decision.directive["model"] == "model-a"
    assert decision.directive["reason"] == (
        "repromote to route primary after 3 accepted-member turns (model-alt -> model-a)"
    )
    assert set(decision.record) == EXPECTED_RECORD_FIELDS
    # Emission resets even in shadow (shared state, never applied) — the
    # next member noop starts a fresh streak instead of re-emitting.
    assert state["tg:c1"]["repromote_streak"] == 0
    decision = _evaluate(
        complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME,
    )
    assert decision.outcome == "noop_satisfied_repromote_1_of_3"


def test_repromote_fallback_label_never_advances():
    # Classifier-path trust gate: a regex-fallback dev label during an outage
    # must not walk the session toward a swap (mirror of the normal_streak rule).
    state = {}

    def _boom(prompt):
        raise TimeoutError("classifier down")

    cfg = _member_cfg()
    for _ in range(5):
        decision = _evaluate(
            text="gateway 고장났어 디버깅해줘", complete_dev=_boom,
            state=state, cfg=cfg, runtime=_MEMBER_RUNTIME,
        )
        assert decision.outcome == "noop_satisfied"
        assert decision.record["source"] == "fallback"
    assert state["tg:c1"].get("repromote_streak", 0) == 0


def test_repromote_static_noop_always_advances(monkeypatch):
    # Static labels are deterministic — no trust gate on that path.
    rules = [{"name": "pin", "route": "dev", "when": {"text_matches_any": ["codex-lb"]}}]
    cfg = _member_cfg(static_rules=rules)
    sentinel = MagicMock(side_effect=AssertionError("classifier must not run"))
    monkeypatch.setattr(mr_mod, "_call_gemini", sentinel)
    state = {}
    outcomes = [
        _evaluate(text="codex-lb 확인해줘", cfg=cfg, runtime=_MEMBER_RUNTIME, state=state).outcome
        for _ in range(3)
    ]
    sentinel.assert_not_called()
    assert outcomes == [
        "noop_satisfied_repromote_1_of_3",
        "noop_satisfied_repromote_2_of_3",
        "repromote_to_primary",
    ]


def test_repromote_streak_shared_across_static_and_classifier_paths():
    rules = [{"name": "pin", "route": "dev", "when": {"text_matches_any": ["codex-lb"]}}]
    cfg = _member_cfg(static_rules=rules)
    state = {}
    for _ in range(2):
        _evaluate(text="codex-lb 확인해줘", cfg=cfg, runtime=_MEMBER_RUNTIME, state=state)
    # Same session, same route: the classifier path continues the streak.
    decision = _evaluate(
        complete_dev=_complete("SYSTEM_DEV"), cfg=cfg, runtime=_MEMBER_RUNTIME, state=state,
    )
    assert decision.outcome == "repromote_to_primary"


def test_repromote_resets_on_primary_runtime():
    cfg = _member_cfg()
    state = {}
    for _ in range(2):
        _evaluate(complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME)
    assert state["tg:c1"]["repromote_streak"] == 2
    decision = _evaluate(
        complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg,
        runtime={"model": "model-a", "provider": "p1"},
    )
    assert decision.outcome == "noop_satisfied"  # on-primary noop stays plain
    assert state["tg:c1"]["repromote_streak"] == 0
    decision = _evaluate(complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME)
    assert decision.outcome == "noop_satisfied_repromote_1_of_3"


def test_repromote_route_change_resets_then_advances():
    cfg = _member_cfg()
    cfg["model_routes"]["routes"]["doc"] = {
        "description": "doc route", "provider": "p1", "model": "model-d",
        "accepted": ["model-alt", "model-d"],
    }
    cfg["model_routes"]["router"]["label_routes"] = {
        "SYSTEM_DEV": "dev", "FRONTEND_DEV": "dev", "DOCUMENT_WORK": "doc",
    }
    state = {}
    for _ in range(2):
        _evaluate(complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME)
    assert state["tg:c1"] == {"normal_streak": 0, "repromote_streak": 2, "repromote_route": "dev"}
    decision = _evaluate(
        complete_dev=_complete("DOCUMENT_WORK"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME,
    )
    assert decision.outcome == "noop_satisfied_repromote_1_of_3"
    assert state["tg:c1"]["repromote_route"] == "doc"
    assert state["tg:c1"]["repromote_streak"] == 1


def test_repromote_resets_on_any_emission():
    cfg = _member_cfg()
    # switch resets: streak 2 on dev, then a dev label from a non-member runtime.
    state = {}
    for _ in range(2):
        _evaluate(complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME)
    decision = _evaluate(
        complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg,
        runtime={"model": "model-z", "provider": "p1"},
    )
    assert decision.outcome == "switch"
    assert state["tg:c1"]["repromote_streak"] == 0
    assert state["tg:c1"]["repromote_route"] == ""

    # downgrade_to_chat resets too.
    state = {}
    for _ in range(2):
        _evaluate(complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME)
    for expected in ("normal_streak_1_of_3", "normal_streak_2_of_3", "downgrade_to_chat"):
        decision = _evaluate(complete_dev=_complete("NORMAL"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME)
        assert decision.outcome == expected
    assert state["tg:c1"]["repromote_streak"] == 0


def test_repromote_held_when_primary_unhealthy(monkeypatch):
    cfg = _member_cfg()
    state = {}
    unhealthy = {
        "route": "dev", "provider": "p2", "model": "model-b",
        "reasoning_effort": "", "source": "fallback:1",
        "reason": "failover — p1 unhealthy (HTTP 500)",
    }
    monkeypatch.setattr(mr_mod, "_resolve_route_directive", lambda *a, **k: dict(unhealthy))
    outcomes = [
        _evaluate(complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME).outcome
        for _ in range(4)
    ]
    assert outcomes == [
        "noop_satisfied_repromote_1_of_3",
        "noop_satisfied_repromote_2_of_3",
        "repromote_held",
        "repromote_held",  # streak held clamped at threshold, retried per turn
    ]
    assert state["tg:c1"]["repromote_streak"] == 3

    # Primary heals → re-promotion on the very next turn.
    healthy = {
        "route": "dev", "provider": "p1", "model": "model-a",
        "reasoning_effort": "xhigh", "source": "default", "reason": "dev",
    }
    monkeypatch.setattr(mr_mod, "_resolve_route_directive", lambda *a, **k: dict(healthy))
    decision = _evaluate(complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME)
    assert decision.outcome == "repromote_to_primary"
    assert decision.directive["model"] == "model-a"


def test_repromote_held_when_resolution_matches_runtime(monkeypatch):
    # A default-source resolution that lands on the runtime's own model must
    # not emit a self-switch — held, same as an unhealthy primary.
    cfg = _member_cfg()
    state = {"tg:c1": {"normal_streak": 0, "repromote_streak": 2, "repromote_route": "dev"}}
    same = {
        "route": "dev", "provider": "p1", "model": "model-alt",
        "reasoning_effort": "", "source": "default", "reason": "dev",
    }
    monkeypatch.setattr(mr_mod, "_resolve_route_directive", lambda *a, **k: dict(same))
    decision = _evaluate(complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME)
    assert decision.outcome == "repromote_held"
    assert decision.directive is None


def test_repromote_chat_member_via_noop_already_chat():
    # CHAT parity: a session parked on a non-primary chat member re-promotes
    # to the chat primary through the noop_already_chat branch.
    cfg = _member_cfg()
    state = {}
    runtime = {"model": "grok-x", "provider": "p2"}
    outcomes = []
    for _ in range(3):
        decision = _evaluate(complete_dev=_complete("NORMAL"), state=state, cfg=cfg, runtime=runtime)
        outcomes.append(decision.outcome)
    assert outcomes == [
        "noop_satisfied_repromote_1_of_3",
        "noop_satisfied_repromote_2_of_3",
        "repromote_to_primary",
    ]
    assert decision.directive["route"] == "chat"
    assert decision.directive["model"] == "model-b"
    assert decision.directive["reason"] == (
        "repromote to route primary after 3 accepted-member turns (grok-x -> model-b)"
    )
    # normal_streak advanced independently — the counters never interact.
    assert state["tg:c1"]["normal_streak"] == 3
    assert state["tg:c1"]["repromote_streak"] == 0


def test_repromote_chat_plain_outcomes_untouched():
    cfg = _member_cfg()
    # On the chat primary: plain noop_already_chat, no streak.
    state = {}
    decision = _evaluate(
        complete_dev=_complete("NORMAL"), cfg=cfg, state=state,
        runtime={"model": "model-b", "provider": "p2"},
    )
    assert decision.outcome == "noop_already_chat"
    assert state["tg:c1"].get("repromote_streak", 0) == 0

    # Fallback-source NORMAL on a non-primary chat member: plain, no advance.
    def _boom(prompt):
        raise TimeoutError("down")

    decision = _evaluate(
        text="오늘 뭐 먹지?", complete_dev=_boom, cfg=cfg, state=state,
        runtime={"model": "grok-x", "provider": "p2"},
    )
    assert decision.outcome == "noop_already_chat"
    assert state["tg:c1"].get("repromote_streak", 0) == 0


def test_repromote_disabled_by_zero_threshold():
    # Route-level 0 disables regardless of the router default.
    cfg = _member_cfg()
    cfg["model_routes"]["routes"]["dev"]["repromote_after_turns"] = 0
    state = {}
    for _ in range(4):
        decision = _evaluate(complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME)
        assert decision.outcome == "noop_satisfied"
    assert state["tg:c1"].get("repromote_streak", 0) == 0

    # Router-level 0 disables every route without an override.
    cfg = _member_cfg(router={"repromote_after_turns": 0})
    state = {}
    for _ in range(4):
        decision = _evaluate(complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME)
        assert decision.outcome == "noop_satisfied"


def test_repromote_route_override_wins_over_router_value():
    def _outcomes(cfg):
        state = {}
        return [
            _evaluate(complete_dev=_complete("SYSTEM_DEV"), state=state, cfg=cfg, runtime=_MEMBER_RUNTIME).outcome
            for _ in range(2)
        ]

    cfg = _member_cfg(router={"repromote_after_turns": 5})
    cfg["model_routes"]["routes"]["dev"]["repromote_after_turns"] = 2
    assert _outcomes(cfg) == ["noop_satisfied_repromote_1_of_2", "repromote_to_primary"]

    # The override also re-enables under a disabled router default.
    cfg = _member_cfg(router={"repromote_after_turns": 0})
    cfg["model_routes"]["routes"]["dev"]["repromote_after_turns"] = 2
    assert _outcomes(cfg) == ["noop_satisfied_repromote_1_of_2", "repromote_to_primary"]


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


def _refusal_stage_runner(
    monkeypatch,
    tmp_path,
    *,
    notify=True,
    clean_fork=True,
    messages=(),
    soft_detect=True,
    max_recovery_hops=2,
    min_confidence=0.85,
):
    cfg = _cfg(router={
        "mode": "enforce",
        "refusal": {
            "enabled": True,
            "notify": notify,
            "clean_fork": clean_fork,
            "keep_user_turns": 2,
            "soft_detect": soft_detect,
            "max_recovery_hops": max_recovery_hops,
            "min_confidence": min_confidence,
        },
    })
    runner = _make_runner(
        monkeypatch,
        cfg,
        runtime=("model-z", {"provider": "p1", "base_url": "https://p1.example/v1"}),
    )
    runner.session_store._db.messages = list(messages)
    evidence = "S0 hard refusal cue + S5 code " + "x" * 100
    monkeypatch.setattr(
        mr_mod,
        "_call_gemini",
        lambda *a, **k: json.dumps({
            "evidence": evidence,
            "label": "SYSTEM_DEV",
            "confidence": 0.97,
            "refusal_risk": True,
            "refusal_confidence": 0.93,
        }),
    )
    runner._apply_model_router_directive = AsyncMock(return_value=(True, False))
    runner._deliver_platform_notice = AsyncMock()
    monkeypatch.setenv(
        "HERMES_MODEL_ROUTER_DECISION_LOG", str(tmp_path / "refusal-decisions.jsonl"),
    )
    return runner, evidence


def test_refusal_notify_sent_after_successful_apply(monkeypatch, tmp_path):
    runner, _ = _refusal_stage_runner(monkeypatch, tmp_path, notify=True)
    source = _source()
    asyncio.run(
        runner._model_router_stage(_event("hard request"), source, "tg:c1", mode="enforce")
    )
    runner._deliver_platform_notice.assert_awaited_once_with(
        source,
        "⚠️ 거절 감지 → PERMISSIVE_DEV(kimi-k3) 라우팅 (masked=0)",
    )


def test_preemptive_refusal_switch_never_rewrites_transcript(monkeypatch, tmp_path):
    messages = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "tool", "content": "policy narrative"},
        {"role": "user", "content": "latest"},
        {"role": "assistant", "content": "I cannot assist."},
    ]
    runner, _ = _refusal_stage_runner(
        monkeypatch, tmp_path, messages=messages,
    )

    asyncio.run(
        runner._model_router_stage(
            _event("hard request"), _source(), "tg:c1", mode="enforce",
        )
    )

    assert runner.session_store._db.messages == messages
    assert runner.session_store.transcript_ops == []
    assert "tg:c1" not in getattr(runner, "_pending_model_notes", {})


def test_refusal_switch_clean_fork_disabled_preserves_transcript_and_notice(
    monkeypatch, tmp_path,
):
    messages = [
        {"role": "user", "content": "request"},
        {"role": "assistant", "content": "refusal"},
    ]
    runner, _ = _refusal_stage_runner(
        monkeypatch,
        tmp_path,
        clean_fork=False,
        messages=messages,
    )
    source = _source()

    asyncio.run(
        runner._model_router_stage(
            _event("hard request"), source, "tg:c1", mode="enforce",
        )
    )

    assert runner.session_store._db.messages == messages
    assert "refusal clean-fork applied" not in getattr(
        runner, "_pending_model_notes", {}
    ).get("tg:c1", "")
    assert runner.session_store.transcript_ops == []
    runner._deliver_platform_notice.assert_awaited_once_with(
        source,
        "⚠️ 거절 감지 → PERMISSIVE_DEV(kimi-k3) 라우팅 (masked=0)",
    )


def test_hard_refusal_preserves_current_turn_and_stages_force(
    monkeypatch, tmp_path,
):
    messages = [
        {"role": "user", "content": "earlier request"},
        {"role": "assistant", "content": "earlier completed answer"},
        {"role": "user", "content": "current request"},
        {"role": "assistant", "content": "provider refusal output"},
    ]
    runner, _ = _refusal_stage_runner(
        monkeypatch, tmp_path, notify=True, messages=messages,
    )
    source = _source()

    masked = asyncio.run(
        runner._handle_gateway_hard_refusal(
            "tg:c1",
            "sid-1",
            source,
            {"error": "content_policy_blocked: blocked by provider"},
        )
    )

    assert masked == 0
    assert runner.session_store._db.inactive_ids == set()
    assert [
        message["content"]
        for message in runner.session_store._db.get_messages("sid-1")
        if message["role"] == "assistant"
    ] == ["earlier completed answer", "provider refusal output"]
    entry = runner._model_router_state["tg:c1"]
    assert entry["force_refusal_route"] is True
    assert entry["force_refusal_reason"] == "prior_turn_refused"
    assert entry["refusal_recovery_count"] == 1


def test_soft_refusal_masks_current_turn_and_stages_force(
    monkeypatch, tmp_path, caplog,
):
    response = "A completed refusal explanation from the assistant. " * 2
    messages = [
        {"role": "user", "content": "earlier request"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "current request"},
        {"role": "assistant", "content": response},
    ]
    runner, _ = _refusal_stage_runner(
        monkeypatch, tmp_path, messages=messages,
    )
    probe = MagicMock(return_value={
        "prior_refusal": True,
        "prior_refusal_confidence": 0.94,
        "source": "llm",
    })
    monkeypatch.setattr(mr_mod, "classify_prior_refusal", probe)

    with caplog.at_level(logging.INFO, logger="agent.refusal_history"):
        masked = asyncio.run(runner._handle_gateway_soft_refusal(
            "tg:c1",
            "sid-1",
            _source(),
            {"final_response": response},
            "current request",
        ))

    assert masked == 1
    assert runner.session_store._db.inactive_ids == {4}
    entry = runner._model_router_state["tg:c1"]
    assert entry["force_refusal_route"] is True
    assert entry["force_refusal_reason"] == "prior_turn_refused"
    assert entry["refusal_recovery_count"] == 1
    probe.assert_called_once()
    assert "source=router_soft_refusal" in caplog.text
    assert "configured=True apply=True" in caplog.text


def test_soft_refusal_clean_fork_disabled_still_stages_without_mask(
    monkeypatch, tmp_path,
):
    response = "A completed refusal explanation from the assistant. " * 2
    runner, _ = _refusal_stage_runner(
        monkeypatch,
        tmp_path,
        clean_fork=False,
        messages=[
            {"role": "user", "content": "current request"},
            {"role": "assistant", "content": response},
        ],
    )
    monkeypatch.setattr(mr_mod, "classify_prior_refusal", MagicMock(return_value={
        "prior_refusal": True,
        "prior_refusal_confidence": 0.94,
        "source": "llm",
    }))

    masked = asyncio.run(runner._handle_gateway_soft_refusal(
        "tg:c1", "sid-1", _source(), {"final_response": response}, "current request",
    ))

    assert masked == 0
    assert runner.session_store._db.inactive_ids == set()
    entry = runner._model_router_state["tg:c1"]
    assert entry["force_refusal_route"] is True
    assert entry["force_refusal_reason"] == "prior_turn_refused"


def test_soft_refusal_below_threshold_does_not_mask_or_stage(monkeypatch, tmp_path):
    response = "A long response that the probe rates below the configured threshold."
    runner, _ = _refusal_stage_runner(
        monkeypatch,
        tmp_path,
        messages=[
            {"role": "user", "content": "request"},
            {"role": "assistant", "content": response},
        ],
    )
    monkeypatch.setattr(mr_mod, "classify_prior_refusal", MagicMock(return_value={
        "prior_refusal": True,
        "prior_refusal_confidence": 0.84,
        "source": "llm",
    }))

    masked = asyncio.run(runner._handle_gateway_soft_refusal(
        "tg:c1", "sid-1", _source(), {"final_response": response}, "request",
    ))

    assert masked == 0
    assert runner.session_store._db.inactive_ids == set()
    entry = runner._model_router_state["tg:c1"]
    assert "force_refusal_route" not in entry
    assert entry["refusal_recovery_count"] == 0


def test_soft_refusal_skips_content_policy_hard_error(monkeypatch, tmp_path):
    runner, _ = _refusal_stage_runner(monkeypatch, tmp_path)
    probe = MagicMock(side_effect=AssertionError("hard refusal must own this turn"))
    monkeypatch.setattr(mr_mod, "classify_prior_refusal", probe)

    assert asyncio.run(runner._handle_gateway_soft_refusal(
        "tg:c1",
        "sid-1",
        _source(),
        {
            "final_response": "A provider-generated refusal response. " * 2,
            "error": "content_policy_blocked: provider rejected request",
        },
        "request",
    )) == 0
    probe.assert_not_called()


def test_soft_refusal_disabled_never_calls_probe(monkeypatch, tmp_path):
    runner, _ = _refusal_stage_runner(
        monkeypatch, tmp_path, soft_detect=False,
    )
    probe = MagicMock(side_effect=AssertionError("soft probe is disabled"))
    monkeypatch.setattr(mr_mod, "classify_prior_refusal", probe)

    assert asyncio.run(runner._handle_gateway_soft_refusal(
        "tg:c1",
        "sid-1",
        _source(),
        {"final_response": "A response long enough to otherwise be classified. " * 2},
        "request",
    )) == 0
    probe.assert_not_called()


def test_short_response_never_calls_soft_refusal_probe(monkeypatch, tmp_path):
    runner, _ = _refusal_stage_runner(monkeypatch, tmp_path)
    probe = MagicMock(side_effect=AssertionError("short response must be skipped"))
    monkeypatch.setattr(mr_mod, "classify_prior_refusal", probe)

    assert asyncio.run(runner._handle_gateway_soft_refusal(
        "tg:c1", "sid-1", _source(), {"final_response": "No, sorry."}, "request",
    )) == 0
    probe.assert_not_called()


def test_refusal_recovery_guard_stops_third_hop_and_notifies_once(
    monkeypatch, tmp_path,
):
    response = "A repeated refusal response that is sufficiently long for probing."
    runner, _ = _refusal_stage_runner(
        monkeypatch,
        tmp_path,
        max_recovery_hops=2,
        messages=[
            {"role": "user", "content": "request"},
            {"role": "assistant", "content": response},
        ],
    )
    monkeypatch.setattr(mr_mod, "classify_prior_refusal", MagicMock(return_value={
        "prior_refusal": True,
        "prior_refusal_confidence": 0.95,
        "source": "llm",
    }))

    for _ in range(2):
        asyncio.run(runner._handle_gateway_soft_refusal(
            "tg:c1", "sid-1", _source(), {"final_response": response}, "request",
        ))
        runner._model_router_state["tg:c1"].pop("force_refusal_route", None)
        runner._model_router_state["tg:c1"].pop("force_refusal_reason", None)

    mask_ops_before = list(runner.session_store.transcript_ops)
    asyncio.run(runner._handle_gateway_soft_refusal(
        "tg:c1", "sid-1", _source(), {"final_response": response}, "request",
    ))
    entry = runner._model_router_state["tg:c1"]
    assert entry["refusal_recovery_count"] == 2
    assert entry["refusal_recovery_exhausted"] is True
    assert "force_refusal_route" not in entry
    assert runner.session_store.transcript_ops == mask_ops_before
    exhaustion_notice = (
        "⚠️ 거절이 반복 — 라우팅으로 해결되는 케이스가 아님. 자동 전환을 멈춤"
    )
    assert runner._deliver_platform_notice.await_args_list[-1].args[1] == exhaustion_notice

    notice_count = runner._deliver_platform_notice.await_count
    asyncio.run(runner._handle_gateway_soft_refusal(
        "tg:c1", "sid-1", _source(), {"final_response": response}, "request",
    ))
    assert runner._deliver_platform_notice.await_count == notice_count


def test_refusal_recovery_guard_resets_after_clean_turn(monkeypatch, tmp_path):
    response = "A response long enough to exercise the refusal probe and recovery guard."
    runner, _ = _refusal_stage_runner(monkeypatch, tmp_path)
    probe = MagicMock(side_effect=[
        {"prior_refusal": True, "prior_refusal_confidence": 0.95},
        {"prior_refusal": False, "prior_refusal_confidence": 0.99},
        {"prior_refusal": True, "prior_refusal_confidence": 0.95},
    ])
    monkeypatch.setattr(mr_mod, "classify_prior_refusal", probe)

    asyncio.run(runner._handle_gateway_soft_refusal(
        "tg:c1", "sid-1", _source(), {"final_response": response}, "request",
    ))
    entry = runner._model_router_state["tg:c1"]
    assert entry["refusal_recovery_count"] == 1

    asyncio.run(runner._handle_gateway_soft_refusal(
        "tg:c1", "sid-1", _source(), {"final_response": response}, "request",
    ))
    assert entry["refusal_recovery_count"] == 0
    assert entry["refusal_recovery_exhausted"] is False

    asyncio.run(runner._handle_gateway_soft_refusal(
        "tg:c1", "sid-1", _source(), {"final_response": response}, "request",
    ))
    assert entry["refusal_recovery_count"] == 1


@pytest.mark.parametrize("terminal_flag", ["interrupted", "failed"])
def test_interrupted_or_failed_turn_never_calls_soft_probe(
    monkeypatch, tmp_path, terminal_flag,
):
    runner, _ = _refusal_stage_runner(monkeypatch, tmp_path)
    probe = MagicMock(side_effect=AssertionError("terminal turn must skip probe"))
    monkeypatch.setattr(mr_mod, "classify_prior_refusal", probe)

    assert asyncio.run(runner._handle_gateway_soft_refusal(
        "tg:c1",
        "sid-1",
        _source(),
        {
            "final_response": "A response long enough to otherwise be classified. " * 2,
            terminal_flag: True,
        },
        "request",
    )) == 0
    probe.assert_not_called()


def test_refusal_notify_suppressed_by_config(monkeypatch, tmp_path):
    runner, _ = _refusal_stage_runner(monkeypatch, tmp_path, notify=False)
    asyncio.run(
        runner._model_router_stage(
            _event("hard request"), _source(), "tg:c1", mode="enforce",
        )
    )
    runner._deliver_platform_notice.assert_not_awaited()


def test_refusal_notify_exception_does_not_break_dispatch(monkeypatch, tmp_path):
    runner, _ = _refusal_stage_runner(monkeypatch, tmp_path, notify=True)
    runner._deliver_platform_notice.side_effect = RuntimeError("adapter send failed")
    asyncio.run(
        runner._model_router_stage(
            _event("hard request"), _source(), "tg:c1", mode="enforce",
        )
    )
    record = json.loads((tmp_path / "refusal-decisions.jsonl").read_text().splitlines()[0])
    assert record["outcome"] == "refusal_switch"
    assert record["applied"] is True


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


def test_enforce_mode_applies_repromote_directive(monkeypatch, tmp_path):
    """The enforce gate applies repromote_to_primary like switch/downgrade —
    only on the threshold turn, with pre-threshold noops left unapplied."""
    from hermes_cli.model_switch import ModelSwitchResult

    log_path = tmp_path / "decisions.jsonl"
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(log_path))
    cfg = _cfg(router={"mode": "enforce"})
    cfg["model_routes"]["routes"]["dev"]["accepted"] = ["model-a", "model-alt"]
    runner = _make_runner(
        monkeypatch, cfg, runtime=("model-alt", {"provider": "p1", "base_url": "https://p1.example/v1"}),
    )
    monkeypatch.setattr(
        mr_mod, "_call_gemini",
        lambda *a, **k: json.dumps({"evidence": "S5", "label": "SYSTEM_DEV", "confidence": 0.9}),
    )
    switch = MagicMock(return_value=ModelSwitchResult(
        success=True,
        new_model="model-a",
        target_provider="p1",
        api_key="sk-test",
        base_url="https://p1.example/v1",
        api_mode="chat_completions",
    ))
    monkeypatch.setattr("hermes_cli.model_switch.switch_model", switch)

    for _ in range(3):
        asyncio.run(
            runner._model_router_stage(_event("gateway 버그 고쳐줘"), _source(), "tg:c1", mode="enforce")
        )

    switch.assert_called_once()  # only the threshold turn applies
    assert runner._session_model_overrides["tg:c1"]["model"] == "model-a"
    runner._evict_cached_agent.assert_called_once_with("tg:c1")
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert [r["outcome"] for r in records] == [
        "noop_satisfied_repromote_1_of_3",
        "noop_satisfied_repromote_2_of_3",
        "repromote_to_primary",
    ]
    assert [r["applied"] for r in records] == [False, False, True]
    assert set(records[-1]) == EXPECTED_RECORD_FIELDS | {"ts", "applied", "reasoning_applied"}


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
    # Dict (not set): the _clear_conversation_scope funnel only pops dicts.
    runner._model_router_fresh_applies = {"tg:c1": True}

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
