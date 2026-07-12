"""Fork default for the inter-tool sleep: 0.0, overridable via HERMES_TOOL_DELAY."""

import os
from unittest.mock import patch

import pytest


def _make_agent(**kwargs):
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        from run_agent import AIAgent

        return AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            model="test/model",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            **kwargs,
        )


def test_default_tool_delay_is_zero(monkeypatch):
    monkeypatch.delenv("HERMES_TOOL_DELAY", raising=False)
    assert _make_agent().tool_delay == 0.0


def test_env_override(monkeypatch):
    monkeypatch.setenv("HERMES_TOOL_DELAY", "0.4")
    assert _make_agent().tool_delay == pytest.approx(0.4)


def test_explicit_arg_beats_env(monkeypatch):
    monkeypatch.setenv("HERMES_TOOL_DELAY", "0.4")
    assert _make_agent(tool_delay=2.0).tool_delay == pytest.approx(2.0)


def test_invalid_and_negative_env_fall_back(monkeypatch):
    monkeypatch.setenv("HERMES_TOOL_DELAY", "not-a-number")
    assert _make_agent().tool_delay == 0.0
    monkeypatch.setenv("HERMES_TOOL_DELAY", "-3")
    assert _make_agent().tool_delay == 0.0
