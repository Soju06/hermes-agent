"""Tests for the periodic todo reminder + Claude Code todo-tool parity.

Covers the three parity changes:

1. ``_maybe_inject_todo_reminder`` — mid-loop re-surfacing of unfinished
   todos through the cache-safe tool-tail channel (Claude Code
   ``todo_reminder`` attachment parity).
2. ``todo_tool`` result carrying the continuation nudge (CC's TodoWrite
   result text) on writes, and hydration compatibility.
3. ``active_form`` item field acceptance (snake_case and camelCase).
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.todo_tool import TODO_INJECTION_HEADER, TodoStore, todo_tool


class _StubAgent:
    def __init__(self, *, items=None, since_write=0, since_reminder=10):
        self._todo_store = TodoStore()
        if items:
            self._todo_store.write(items, merge=False)
        self._todo_loop_iterations_since_write = since_write
        self._todo_loop_iterations_since_reminder = since_reminder


def _tool_messages():
    return [
        {"role": "user", "content": "do the task"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}]},
        {"role": "tool", "tool_call_id": "t1", "content": "result one"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t2"}]},
        {"role": "tool", "tool_call_id": "t2", "content": "result two"},
    ]


def _pending_items():
    return [
        {"id": "1", "content": "write parser", "status": "completed"},
        {"id": "2", "content": "wire reminder", "status": "in_progress"},
        {"id": "3", "content": "run tests", "status": "pending"},
    ]


# ── _maybe_inject_todo_reminder ──────────────────────────────────────────────


def test_reminder_injected_at_threshold():
    from agent.turn_todo_reminder import (
        TODO_REMINDER_HEADER,
        _maybe_inject_todo_reminder,
    )

    agent = _StubAgent(items=_pending_items(), since_write=10, since_reminder=10)
    messages = _tool_messages()
    assert _maybe_inject_todo_reminder(agent, messages) is True
    tail = messages[-1]["content"]
    assert tail.startswith("result two")
    assert TODO_REMINDER_HEADER in tail
    # Unfinished items are listed; completed ones are not.
    assert "wire reminder" in tail
    assert "run tests" in tail
    assert "write parser" not in tail
    # Live nudge must not carry the post-compaction snapshot header.
    assert TODO_INJECTION_HEADER not in tail
    # Reminder spacing counter reset; write counter untouched.
    assert agent._todo_loop_iterations_since_reminder == 0
    assert agent._todo_loop_iterations_since_write == 10
    # No synthetic user message was inserted.
    assert [m["role"] for m in messages] == [
        "user", "assistant", "tool", "assistant", "tool",
    ]


def test_reminder_respects_since_write_threshold():
    from agent.turn_todo_reminder import _maybe_inject_todo_reminder

    agent = _StubAgent(items=_pending_items(), since_write=9, since_reminder=10)
    messages = _tool_messages()
    assert _maybe_inject_todo_reminder(agent, messages) is False
    assert messages[-1]["content"] == "result two"


def test_reminder_respects_spacing_threshold():
    from agent.turn_todo_reminder import _maybe_inject_todo_reminder

    agent = _StubAgent(items=_pending_items(), since_write=25, since_reminder=3)
    messages = _tool_messages()
    assert _maybe_inject_todo_reminder(agent, messages) is False


def test_reminder_skips_empty_store():
    from agent.turn_todo_reminder import _maybe_inject_todo_reminder

    agent = _StubAgent(items=None, since_write=50, since_reminder=50)
    messages = _tool_messages()
    assert _maybe_inject_todo_reminder(agent, messages) is False


def test_reminder_skips_fully_completed_list():
    from agent.turn_todo_reminder import _maybe_inject_todo_reminder

    agent = _StubAgent(
        items=[
            {"id": "1", "content": "a", "status": "completed"},
            {"id": "2", "content": "b", "status": "cancelled"},
        ],
        since_write=50,
        since_reminder=50,
    )
    messages = _tool_messages()
    assert _maybe_inject_todo_reminder(agent, messages) is False


def test_reminder_waits_for_tool_message():
    """No tool message yet — nothing to append to; counters stay armed so a
    later iteration with a tool result can deliver."""
    from agent.turn_todo_reminder import _maybe_inject_todo_reminder

    agent = _StubAgent(items=_pending_items(), since_write=10, since_reminder=10)
    messages = [{"role": "user", "content": "task"}]
    assert _maybe_inject_todo_reminder(agent, messages) is False
    assert agent._todo_loop_iterations_since_reminder == 10


def test_reminder_multimodal_tool_content():
    from agent.turn_todo_reminder import (
        TODO_REMINDER_HEADER,
        _maybe_inject_todo_reminder,
    )

    agent = _StubAgent(items=_pending_items(), since_write=10, since_reminder=10)
    messages = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}]},
        {"role": "tool", "tool_call_id": "t1",
         "content": [{"type": "text", "text": "block"}]},
    ]
    assert _maybe_inject_todo_reminder(agent, messages) is True
    blocks = messages[-1]["content"]
    assert blocks[0] == {"type": "text", "text": "block"}
    assert TODO_REMINDER_HEADER in blocks[-1]["text"]


def test_reminder_no_store_attr():
    from agent.turn_todo_reminder import _maybe_inject_todo_reminder

    class Bare:
        pass

    assert _maybe_inject_todo_reminder(Bare(), _tool_messages()) is False


def test_reminder_ignores_non_int_counters():
    """A MagicMock agent (or a partially initialised one) has non-int counters: no raise, no inject."""
    from unittest.mock import MagicMock

    from agent.turn_todo_reminder import _maybe_inject_todo_reminder

    agent = MagicMock()
    agent._todo_store = TodoStore()
    agent._todo_store.write(_pending_items(), merge=False)
    messages = _tool_messages()
    assert _maybe_inject_todo_reminder(agent, messages) is False
    assert messages[-1]["content"] == "result two"


# ── todo_tool result message (CC TodoWrite parity) ───────────────────────────


def test_write_result_carries_continuation_message():
    store = TodoStore()
    result = json.loads(todo_tool(todos=_pending_items(), store=store))
    assert result["message"].startswith("Todos have been modified successfully.")
    assert "Please proceed with the current tasks" in result["message"]
    assert result["summary"]["total"] == 3


def test_read_result_has_no_continuation_message():
    store = TodoStore()
    store.write(_pending_items(), merge=False)
    result = json.loads(todo_tool(store=store))
    assert "message" not in result
    assert result["summary"]["total"] == 3


def test_write_result_still_hydratable():
    """The hydration path json.loads()-es the newest todo result and replays
    data["todos"] — the added message field must not break that contract."""
    store = TodoStore()
    payload = json.loads(todo_tool(todos=_pending_items(), store=store))
    fresh = TodoStore()
    fresh.write(payload["todos"], merge=False)
    assert [i["id"] for i in fresh.read()] == [i["id"] for i in store.read()]


# ── active_form field ────────────────────────────────────────────────────────


def test_active_form_accepted_snake_case():
    store = TodoStore()
    items = store.write(
        [{"id": "1", "content": "Run tests", "active_form": "Running tests",
          "status": "in_progress"}],
        merge=False,
    )
    assert items[0]["active_form"] == "Running tests"


def test_active_form_accepted_camel_case():
    store = TodoStore()
    items = store.write(
        [{"id": "1", "content": "Run tests", "activeForm": "Running tests",
          "status": "in_progress"}],
        merge=False,
    )
    assert items[0]["active_form"] == "Running tests"


def test_active_form_optional_and_merge_updates():
    store = TodoStore()
    store.write([{"id": "1", "content": "Build", "status": "pending"}], merge=False)
    assert "active_form" not in store.read()[0]
    store.write(
        [{"id": "1", "active_form": "Building", "status": "in_progress"}],
        merge=True,
    )
    item = store.read()[0]
    assert item["active_form"] == "Building"
    assert item["content"] == "Build"
    assert item["status"] == "in_progress"


def test_active_form_blank_dropped():
    store = TodoStore()
    items = store.write(
        [{"id": "1", "content": "Build", "active_form": "   ", "status": "pending"}],
        merge=False,
    )
    assert "active_form" not in items[0]
