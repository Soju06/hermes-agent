"""An empty compression summary must log WHY it was empty, never WHAT was in the exchange.

The failure itself is unchanged: the same ``empty content`` RuntimeError, classified the same way.
What is added is one content-free log line that tells a structured refusal from a reasoning-only turn.
"""

import logging
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from anthropic.types import Message

from agent.auxiliary_client import _AnthropicCompletionsAdapter
from agent.context_compressor import ContextCompressor, _classify_summary_failure
from agent.error_classifier import classify_api_error

SECRET_PROMPT = "SECRET-PROMPT-TEXT"
SECRET_REASONING = "SECRET-REASONING-TEXT"
SECRET_EXPLANATION = "SECRET-REFUSAL-EXPLANATION"
SECRETS = (SECRET_PROMPT, SECRET_REASONING, SECRET_EXPLANATION)

_THINKING = {"type": "thinking", "thinking": "", "signature": "sig"}


@pytest.fixture()
def compressor():
    with patch("agent.context_compressor.get_model_context_length", return_value=100000):
        c = ContextCompressor(model="test/summary-model", provider="test-provider", quiet_mode=True)
        _ = c.context_length
        return c


def _native_message(content, stop_reason, output_tokens, stop_details=None):
    payload = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "test/summary-model",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 25000, "output_tokens": output_tokens},
    }
    if stop_details is not None:
        payload["stop_details"] = stop_details
    return Message.model_validate(payload)


def _through_aux_adapter(native):
    """What the compressor actually receives: the native message after the real aux shim."""
    adapter = _AnthropicCompletionsAdapter(SimpleNamespace(base_url=""), "test/summary-model")
    with patch("agent.anthropic_adapter.create_anthropic_message", return_value=native):
        return adapter.create(messages=[{"role": "user", "content": SECRET_PROMPT}])


def _summary_call(compressor, response, caplog):
    with caplog.at_level(logging.DEBUG, logger="agent.context_compressor"), \
            patch("agent.context_compressor.call_llm", return_value=response):
        with pytest.raises(RuntimeError, match="empty content") as excinfo:
            compressor._call_summary_llm([{"role": "user", "content": SECRET_PROMPT}], time.monotonic())
    lines = [r.getMessage() for r in caplog.records if r.name == "agent.context_compressor"]
    return excinfo.value, lines


@pytest.mark.parametrize(
    "native",
    [
        pytest.param(_native_message([_THINKING], "end_turn", 3789), id="reasoning-only"),
        pytest.param(
            _native_message(
                [_THINKING, {"type": "text", "text": f"<thinking>{SECRET_REASONING}</thinking>"}], "end_turn", 2104,
            ),
            id="reasoning-inside-text-tags",
        ),
        pytest.param(
            _native_message(
                [], "refusal", 4021,
                stop_details={"type": "refusal", "category": "cyber", "explanation": SECRET_EXPLANATION},
            ),
            id="structured-refusal",
        ),
    ],
)
def test_empty_summary_logs_why_it_ended_without_its_text(compressor, caplog, native):
    response = _through_aux_adapter(native)

    error, lines = _summary_call(compressor, response, caplog)

    # Same failure, same class: the diagnostics ride beside the exception, not inside its routing.
    assert _classify_summary_failure(error, classify_api_error(error)).empty_content
    # One line carries the mapped AND the raw stop reason, the output tokens and every block kind.
    wanted = [response.choices[0].finish_reason, native.stop_reason, str(native.usage.output_tokens)]
    wanted += [block.type for block in native.content]
    assert any(all(value in line for value in wanted) for line in lines), lines
    # Kinds, counts and lengths only.
    for secret in SECRETS:
        assert secret not in "\n".join(lines)
        assert secret not in str(error)


class _UnreadableResponse:
    """An empty message whose diagnostic attributes blow up when read."""

    choices = [SimpleNamespace(message=SimpleNamespace(content=""))]

    @property
    def usage(self):
        raise ValueError("diagnostics must not surface this")

    native_shape = usage


@pytest.mark.parametrize(
    "response, readable",
    [
        pytest.param({"choices": [{"message": {"content": "   "}}]}, True, id="dict-without-usage"),
        pytest.param(
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))]), True,
            id="object-without-attributes",
        ),
        pytest.param(_UnreadableResponse(), False, id="attributes-raise"),
    ],
)
def test_empty_summary_still_fails_the_same_when_the_response_has_nothing_to_report(
    compressor, caplog, response, readable,
):
    error, lines = _summary_call(compressor, response, caplog)

    assert _classify_summary_failure(error, classify_api_error(error)).empty_content
    if readable:
        # A response from a wire with no stop reason or usage still gets a line naming the route.
        assert any(compressor.model in line and compressor.provider in line for line in lines), lines
