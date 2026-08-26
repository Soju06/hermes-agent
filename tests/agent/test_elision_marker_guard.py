"""Regression guard: compression elision markers must not be model-imitable.

Incident (2026-08): the compressor rewrote old tool_call arguments in history
using the static literal ``...[truncated]``. The model then reproduced that
same literal at the TAIL of NEW write_file / patch / terminal payloads,
silently shipping cut-off content to disk. Measured on state.db: 1,507
imitated fresh tool calls across 151 sessions, and 84% of them were triggered
by the model's own earlier imitation rather than by a fresh compressor
rewrite — a self-propagation loop.

The structural fix is that every elision marker carries the exact count of
omitted characters. A model composing a fresh payload cannot know that count,
so the marker is not pattern-completable, and any marker that does appear is
identifiable as a compression artifact.

These tests fail if anyone reintroduces a static truncation literal in a code
path whose output reaches the main model's context.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent.context_compressor import (
    _elision_marker,
    _truncate_tool_call_args_json,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Code paths whose truncated output is fed back into the main model's context.
# hermes_cli/auth.py is intentionally excluded: it renders CLI error output for
# a human terminal, not for model context.
MODEL_FACING_SOURCES = [
    "agent/context_compressor.py",
    "agent/skill_preprocessing.py",
    "trajectory_compressor.py",
    "tools/delegate_tool.py",
]

STATIC_LITERAL = "..." + "[truncated]"


class TestElisionMarkerShape:
    def test_marker_reports_omitted_character_count(self):
        marker = _elision_marker(1240)
        assert "1,240" in marker
        assert "omitted" in marker

    def test_marker_varies_with_omitted_length(self):
        """Non-constant output is what makes the marker unimitable."""
        assert _elision_marker(10) != _elision_marker(11)

    def test_marker_is_not_the_old_static_literal(self):
        assert not _elision_marker(500).endswith(STATIC_LITERAL)


class TestToolCallArgShrinking:
    def test_shrunk_args_stay_valid_json(self):
        """Providers 400 on malformed function arguments — must still parse."""
        original = json.dumps({"path": "/tmp/x.md", "content": "abc " * 400})
        shrunk = _truncate_tool_call_args_json(original)
        parsed = json.loads(shrunk)  # must not raise
        assert parsed["path"] == "/tmp/x.md"
        assert len(shrunk) < len(original)

    def test_shrunk_string_carries_omitted_count(self):
        content = "x" * 900
        shrunk = _truncate_tool_call_args_json(json.dumps({"content": content}))
        note = json.loads(shrunk)["content"]
        assert note.endswith("chars omitted]")
        # 900 chars in, 200 kept => 700 omitted, rendered with a thousands sep.
        assert "700" in note

    def test_shrunk_string_does_not_emit_static_literal(self):
        shrunk = _truncate_tool_call_args_json(json.dumps({"content": "y" * 900}))
        assert STATIC_LITERAL not in shrunk

    def test_non_string_values_are_preserved(self):
        payload = json.dumps(
            {"retries": 3, "enabled": True, "timeout": None, "items": [1, 2, 3],
             "note": "z" * 900}
        )
        parsed = json.loads(_truncate_tool_call_args_json(payload))
        assert parsed["retries"] == 3
        assert parsed["enabled"] is True
        assert parsed["timeout"] is None
        assert parsed["items"] == [1, 2, 3]

    def test_invalid_json_is_returned_unchanged(self):
        """Some backends use non-JSON tool arguments; don't corrupt them."""
        raw = "not json at all " * 50
        assert _truncate_tool_call_args_json(raw) == raw


class TestNoStaticLiteralInModelFacingPaths:
    @pytest.mark.parametrize("relpath", MODEL_FACING_SOURCES)
    def test_source_has_no_static_truncation_literal(self, relpath):
        """Guard against reintroducing the self-propagating literal.

        Prose mentions inside comments/docstrings are allowed (the incident is
        documented in-tree); what must not come back is a literal appended to
        content at runtime, i.e. inside a quoted string expression.
        """
        source = (REPO_ROOT / relpath).read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in source.splitlines()
            if STATIC_LITERAL in line
            and re.search(r'[+=]\s*[fru]*["\']', line)
            and not line.strip().startswith("#")
        ]
        assert not offenders, (
            f"{relpath} reintroduced a static truncation literal: {offenders}"
        )
