"""Providers flagged ``anthropic_signature_passthrough`` keep the
direct-Anthropic thinking replay contract even though their base_url is
classified third-party for auth purposes.

Motivation: a self-hosted Claude LB forwards content blocks to real Anthropic
verbatim, so signatures generated through it validate on replay. Stripping
thinking there (the default third-party behaviour) breaks interleaved-thinking
reasoning continuity mid tool-loop on the production Claude route.
"""

from types import SimpleNamespace

import pytest

import agent.anthropic_adapter as adapter
from agent.anthropic_adapter import convert_messages_to_anthropic
from agent.transports import get_transport

SIG = "sig-lb"
LB = "http://10.0.0.114:2455"
OTHER_PROXY = "https://foundry.example.com/anthropic"


@pytest.fixture(autouse=True)
def _passthrough_urls(monkeypatch):
    """Pin the trusted-passthrough set to the LB URL (no config.yaml I/O)."""
    monkeypatch.setattr(
        adapter, "_signature_passthrough_urls_cache", frozenset({LB.lower()})
    )


def _tool_loop_messages():
    """One in-flight tool loop: signed thinking interleaved with tool_use,
    then the tool result — the exact shape whose replay continuity matters."""
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="check a.py first", signature=SIG),
            SimpleNamespace(type="tool_use", id="toolu_1", name="read_file", input={"path": "a.py"}),
        ],
        stop_reason="tool_use",
        usage=None,
    )
    normalized = get_transport("anthropic_messages").normalize_response(response)
    provider_data = normalized.provider_data or {}
    stored = {
        "role": "assistant",
        "content": normalized.content or "",
        "reasoning_details": provider_data.get("reasoning_details"),
        "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
            for tc in (normalized.tool_calls or [])
        ],
    }
    if provider_data.get("anthropic_content_blocks"):
        stored["anthropic_content_blocks"] = provider_data["anthropic_content_blocks"]
    return [
        {"role": "user", "content": "inspect a.py"},
        stored,
        {"role": "tool", "tool_call_id": "toolu_1", "content": "a.py: ok"},
    ]


def _latest_assistant_thinking(base_url, messages=None):
    _sys, out = convert_messages_to_anthropic(
        messages or _tool_loop_messages(), base_url=base_url, model="claude-fable-5"
    )
    assistants = [m for m in out if m.get("role") == "assistant"]
    return [
        b for b in assistants[-1]["content"]
        if isinstance(b, dict) and b.get("type") == "thinking"
    ]


def test_passthrough_lb_keeps_signed_thinking_in_tool_loop():
    thinking = _latest_assistant_thinking(LB)
    assert thinking and thinking[0].get("signature") == SIG


def test_passthrough_matches_with_trailing_slash_and_case():
    thinking = _latest_assistant_thinking("HTTP://10.0.0.114:2455/")
    assert thinking and thinking[0].get("signature") == SIG


def test_unflagged_third_party_still_strips():
    assert not _latest_assistant_thinking(OTHER_PROXY)


def test_passthrough_still_strips_non_latest_assistant_turns():
    """Parity with direct Anthropic: only the LATEST assistant turn keeps
    signed thinking; earlier turns are stripped (the API ignores them and
    upstream mutation of old turns must never 400 the whole request)."""
    messages = _tool_loop_messages() + [
        {"role": "assistant", "content": "done: a.py is fine"},
        {"role": "user", "content": "now check b.py"},
    ]
    _sys, out = convert_messages_to_anthropic(
        messages, base_url=LB, model="claude-fable-5"
    )
    assistants = [m for m in out if m.get("role") == "assistant"]
    assert len(assistants) == 2
    first_turn_thinking = [
        b for b in assistants[0]["content"]
        if isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking")
    ]
    assert not first_turn_thinking


def test_config_loader_normalizes_urls(monkeypatch, tmp_path):
    """The config reader picks up flagged providers and normalizes base URLs."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
providers:
  claude-lb:
    base_url: HTTP://10.0.0.114:2455/
    anthropic_signature_passthrough: true
  minimax:
    base_url: https://api.minimax.io/anthropic
  broken: not-a-dict
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(adapter, "_signature_passthrough_urls_cache", None)
    monkeypatch.setattr(
        "hermes_constants.get_config_path", lambda: cfg
    )
    urls = adapter._get_signature_passthrough_urls()
    assert urls == frozenset({"http://10.0.0.114:2455"})
    assert adapter._is_signature_passthrough_endpoint("http://10.0.0.114:2455/")
    assert not adapter._is_signature_passthrough_endpoint(
        "https://api.minimax.io/anthropic"
    )
