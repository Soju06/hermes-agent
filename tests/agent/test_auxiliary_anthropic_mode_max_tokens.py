"""Explicit Anthropic Messages mode preserves auxiliary output caps."""

from types import SimpleNamespace
from unittest.mock import patch

from agent.auxiliary_client import _build_call_kwargs, call_llm


BASE = "http://10.8.0.3:2455"
MODEL = "claude-fable-5-1"
MESSAGES = [{"role": "user", "content": "hi"}]


def _kwargs(*, api_mode=None, max_tokens=1400, base_url=BASE):
    return _build_call_kwargs(
        "claude-lb",
        MODEL,
        MESSAGES,
        temperature=None,
        max_tokens=max_tokens,
        tools=None,
        timeout=300,
        extra_body=None,
        reasoning_config=None,
        base_url=base_url,
        task="compression",
        api_mode=api_mode,
    )


def test_declared_anthropic_mode_forwards_explicit_cap_on_plain_host():
    assert _kwargs(api_mode="anthropic_messages")["max_tokens"] == 1400


def test_plain_host_without_api_mode_still_omits_cap():
    assert "max_tokens" not in _kwargs()


def test_chat_completions_mode_does_not_broaden_forwarding():
    assert "max_tokens" not in _kwargs(api_mode="chat_completions")


def test_declared_anthropic_mode_with_no_cap_still_omits_cap():
    assert "max_tokens" not in _kwargs(
        api_mode="anthropic_messages", max_tokens=None
    )


def test_anthropic_url_detection_still_forwards_without_api_mode():
    assert _kwargs(base_url="https://example.test/anthropic")["max_tokens"] == 1400


def test_call_llm_threads_resolved_api_mode_into_wire_kwargs():
    recorded = {}

    def create(**kwargs):
        recorded.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )

    client = SimpleNamespace(
        base_url=BASE,
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    resolved = ("claude-lb", MODEL, BASE, "test-key", "anthropic_messages")
    with (
        patch("agent.auxiliary_client._resolve_task_provider_model", return_value=resolved),
        patch("agent.auxiliary_client._get_cached_client", return_value=(client, MODEL)),
        patch("agent.auxiliary_client._effective_provider_for_client", return_value="claude-lb"),
        patch("agent.auxiliary_client._get_task_extra_body", return_value={}),
        patch("agent.auxiliary_client._get_task_max_concurrency", return_value=None),
    ):
        call_llm(task="compression", messages=MESSAGES, max_tokens=1400)

    assert recorded["max_tokens"] == 1400
