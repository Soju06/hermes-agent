"""Tests for the slow-tool perf advisor (tools/perf_advisor.py ``perf_advisory`` /
``with_perf_advisory``) and its executor wiring (agent/tool_executor.py ``_commit_tool_result``)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools.perf_advisor import perf_advisory, with_perf_advisory


RGLOB_CMD = (
    "python3 - <<'PY'\nfrom pathlib import Path\n"
    "for p in Path('/home/ubuntu/apifuse').rglob('*'):\n    pass\nPY"
)


def _no_env(monkeypatch):
    for var in (
        "HERMES_PERF_ADVISOR",
        "HERMES_PERF_ADVISOR_MIN_S",
        "HERMES_PERF_ADVISOR_FOREGROUND_S",
    ):
        monkeypatch.delenv(var, raising=False)


# ── tree-scan advisories ────────────────────────────────────────────


def test_rglob_slow_fires(monkeypatch):
    _no_env(monkeypatch)
    hint = perf_advisory("terminal", {"command": RGLOB_CMD}, 88.0)
    assert hint is not None
    assert "search_files" in hint
    assert hint.startswith("\n\n[perf-advisor]")


def test_os_walk_slow_fires(monkeypatch):
    _no_env(monkeypatch)
    cmd = "python3 -c 'import os\nfor r,d,f in os.walk(\"/home/ubuntu\"): pass'"
    assert perf_advisory("terminal", {"command": cmd}, 30.0)


def test_rglob_fast_silent(monkeypatch):
    _no_env(monkeypatch)
    assert perf_advisory("terminal", {"command": RGLOB_CMD}, 3.0) is None


def test_find_broad_root_fires(monkeypatch):
    _no_env(monkeypatch)
    cmd = "find /home/ubuntu -name '*.ts' | head"
    assert perf_advisory("terminal", {"command": cmd}, 25.0)


def test_find_pruned_silent(monkeypatch):
    _no_env(monkeypatch)
    cmd = "find /home/ubuntu -name node_modules -prune -o -name '*.ts' -print"
    assert perf_advisory("terminal", {"command": cmd}, 25.0) is None


def test_find_maxdepth_silent(monkeypatch):
    _no_env(monkeypatch)
    cmd = "find /home/ubuntu -maxdepth 2 -name '*.ts'"
    assert perf_advisory("terminal", {"command": cmd}, 25.0) is None


def test_find_tmp_silent(monkeypatch):
    _no_env(monkeypatch)
    # /tmp scans are shallow scratch sweeps, not repo walks.
    cmd = "find /tmp -name 'pr-*.log'"
    assert perf_advisory("terminal", {"command": cmd}, 25.0) is None


def test_grep_r_fires(monkeypatch):
    _no_env(monkeypatch)
    cmd = "grep -rn 'request_logs' /home/ubuntu/apifuse"
    assert perf_advisory("terminal", {"command": cmd}, 15.0)


def test_grep_r_exclude_dir_silent(monkeypatch):
    _no_env(monkeypatch)
    cmd = "grep -rn --exclude-dir=node_modules 'request_logs' /home/ubuntu/apifuse"
    assert perf_advisory("terminal", {"command": cmd}, 15.0) is None


# ── sleep-poll advisories ───────────────────────────────────────────


def test_sleep_poll_fires(monkeypatch):
    _no_env(monkeypatch)
    cmd = "while true; do curl -s localhost:8080/health && break; sleep 10; done"
    hint = perf_advisory("terminal", {"command": cmd}, 60.0)
    assert hint and "background=true, notify=true" in hint


def test_short_sleep_silent(monkeypatch):
    _no_env(monkeypatch)
    # sleep < 5s inside a fast command is not a polling loop.
    assert perf_advisory("terminal", {"command": "sleep 1; echo ok"}, 11.0) is None


# ── long-foreground advisory ────────────────────────────────────────


def test_long_foreground_fires(monkeypatch):
    _no_env(monkeypatch)
    hint = perf_advisory("terminal", {"command": "pnpm turbo run test"}, 400.0)
    assert hint and "background=true" in hint and "notify=true" in hint
    assert "process_manage(action='wait')" in hint


def test_long_background_silent(monkeypatch):
    _no_env(monkeypatch)
    args = {"command": "pnpm turbo run test", "background": True}
    assert perf_advisory("terminal", args, 400.0) is None


def test_medium_foreground_silent(monkeypatch):
    _no_env(monkeypatch)
    # Below the 120s foreground threshold, a plain slow command is fine.
    assert perf_advisory("terminal", {"command": "pytest -q"}, 60.0) is None


# ── precedence, scope, knobs ────────────────────────────────────────


def test_tree_scan_wins_over_foreground(monkeypatch):
    _no_env(monkeypatch)
    hint = perf_advisory("terminal", {"command": RGLOB_CMD}, 300.0)
    assert "search_files" in hint


def test_non_terminal_silent(monkeypatch):
    _no_env(monkeypatch)
    assert perf_advisory("execute_code", {"command": RGLOB_CMD}, 88.0) is None
    assert perf_advisory("search_files", {"pattern": "x"}, 88.0) is None


def test_kill_switch(monkeypatch):
    _no_env(monkeypatch)
    monkeypatch.setenv("HERMES_PERF_ADVISOR", "0")
    assert perf_advisory("terminal", {"command": RGLOB_CMD}, 88.0) is None


def test_min_s_override(monkeypatch):
    _no_env(monkeypatch)
    monkeypatch.setenv("HERMES_PERF_ADVISOR_MIN_S", "100")
    assert perf_advisory("terminal", {"command": RGLOB_CMD}, 88.0) is None
    assert perf_advisory("terminal", {"command": RGLOB_CMD}, 120.0)


def test_malformed_args_silent(monkeypatch):
    _no_env(monkeypatch)
    assert perf_advisory("terminal", None, 88.0) is None
    assert perf_advisory("terminal", {"command": None}, 88.0) is None
    assert perf_advisory("terminal", {}, 88.0) is None


def test_advisories_teach_only_advertised_interface(monkeypatch):
    """The hints must name what the model's schemas advertise (``notify``,
    ``process_manage``), never the unadvertised legacy aliases."""
    from tools.process_registry import PROCESS_SCHEMA
    from tools.terminal_tool import TERMINAL_SCHEMA

    _no_env(monkeypatch)
    terminal_props = TERMINAL_SCHEMA["parameters"]["properties"]
    assert "notify" in terminal_props and "background" in terminal_props
    assert "notify_on_complete" not in terminal_props
    assert PROCESS_SCHEMA["name"] == "process_manage"
    assert "wait" in PROCESS_SCHEMA["parameters"]["properties"]["action"]["enum"]

    hints = [
        perf_advisory("terminal", {"command": RGLOB_CMD}, 88.0),
        perf_advisory("terminal", {"command": "sleep 10; curl -s localhost"}, 60.0),
        perf_advisory("terminal", {"command": "pnpm turbo run test"}, 400.0),
    ]
    assert all(hints)
    for hint in hints:
        assert "notify_on_complete" not in hint
        assert "process(" not in hint


# ── result append and executor wiring (agent/tool_executor.py) ──────


def _envelope(text: str) -> dict:
    return {
        "_multimodal": True,
        "text_summary": text,
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
            {"type": "text", "text": text},
        ],
    }


def test_with_perf_advisory_appends_to_slow_string_result(monkeypatch):
    _no_env(monkeypatch)
    out = with_perf_advisory("out", "terminal", {"command": "find / -name x"}, 30.0)
    assert out.startswith("out\n\n[perf-advisor]")
    assert "search_files" in out


def test_with_perf_advisory_leaves_fast_result_unchanged(monkeypatch):
    _no_env(monkeypatch)
    assert with_perf_advisory("out", "terminal", {"command": "ls"}, 0.2) == "out"
    assert with_perf_advisory("out", "terminal", {"command": "find / -name x"}, 0.2) == "out"


def test_with_perf_advisory_multimodal_lands_on_text_part(monkeypatch):
    _no_env(monkeypatch)
    envelope = _envelope("done")
    out = with_perf_advisory(envelope, "terminal", {"command": "sleep 30"}, 31.0)
    assert out is envelope
    image, text = out["content"]
    assert image == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}
    assert text["text"].startswith("done\n\n[perf-advisor]")
    assert out["text_summary"] == text["text"]


@pytest.mark.parametrize("executor_mode", ["sequential", "concurrent"])
@pytest.mark.parametrize("enabled", [True, False])
def test_committed_tool_message_carries_advisory(monkeypatch, tmp_path, executor_mode, enabled):
    """Both executors finalise through ``_commit_tool_result``; the advisory must reach the
    tool message the model sees (and the kill switch must keep it out)."""
    from tests.agent.test_tool_call_incremental_persistence import _make_agent, _mock_tool_call

    _no_env(monkeypatch)
    # A zero threshold makes the check independent of the mocked call's wall-clock duration.
    monkeypatch.setenv("HERMES_PERF_ADVISOR_MIN_S", "0")
    if not enabled:
        monkeypatch.setenv("HERMES_PERF_ADVISOR", "0")
    agent = _make_agent()
    agent._flush_messages_to_session_db = MagicMock()
    command = f"grep -rn needle {tmp_path}"
    tool_call = _mock_tool_call(name="terminal", arguments=json.dumps({"command": command}), call_id="call_scan")
    assistant_message = SimpleNamespace(content="", tool_calls=[tool_call])
    messages: list = []
    if executor_mode == "sequential":
        with patch("model_tools.handle_function_call", return_value="scan output"):
            agent._execute_tool_calls_sequential(assistant_message, messages, "task-1")
    else:
        with patch.object(agent, "_invoke_tool", return_value="scan output"):
            agent._execute_tool_calls_concurrent(assistant_message, messages, "task-1")

    (tool_msg,) = [m for m in messages if m.get("role") == "tool"]
    assert tool_msg["tool_call_id"] == "call_scan"
    content = tool_msg["content"]
    assert content.startswith("scan output")
    if enabled:
        assert "\n\n[perf-advisor]" in content and "search_files" in content
        assert content.count("[perf-advisor]") == 1
    else:
        assert "[perf-advisor]" not in content
