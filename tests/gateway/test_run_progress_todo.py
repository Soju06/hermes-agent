"""Gateway progress bubbles render the todo tool's completed task list (fork: todo-progress-display)."""

import pytest

import tools.todo_tool  # noqa: F401  (registers the todo_list emoji the started line resolves)
from tests.gateway.test_run_progress_topics import _ACTIVE_ADAPTER, _run_with_agent
from tests.waiting import wait_until

_TODO_RESULT = (
    '{"todos":[{"id":"1","content":"Inspect code","status":"in_progress"},'
    '{"id":"2","content":"Run tests","status":"pending"}],'
    '"summary":{"total":2,"pending":1,"in_progress":1,"completed":0,"cancelled":0}}'
)


class TodoProgressAgent:
    """Plans with the todo tool (canonical name ``todo_list`` since the 2026-08 rename), then runs a
    terminal command; paces on what the adapter has delivered instead of the clock."""

    def __init__(self, **kwargs):
        self.tool_progress_callback = kwargs.get("tool_progress_callback")
        self.tools = []

    def run_conversation(self, message, conversation_history=None, task_id=None, **kwargs):
        cb = self.tool_progress_callback
        assert cb is not None
        adapter = _ACTIVE_ADAPTER["adapter"]
        cb("tool.started", "todo_list", "planning 2 task(s)", {
            "todos": [
                {"id": "1", "content": "Inspect code", "status": "in_progress"},
                {"id": "2", "content": "Run tests", "status": "pending"},
            ],
        })
        cb("tool.started", "terminal", "pytest focused suite", {})
        wait_until(lambda: adapter.sent, what="the first progress bubble was sent")
        # Each line lands inside the edit throttle window with nothing queued behind it: the
        # throttled sender must still deliver it once the interval passes, not strand it until a
        # later tool event (or the end-of-turn cancel) flushes the bubble.
        wait_until(lambda: any("pytest focused suite" in edit["content"] for edit in adapter.edits),
                   what="the terminal line was edited into the progress bubble")
        cb("tool.completed", "todo_list", None, None, duration=0.1, is_error=False, result=_TODO_RESULT)
        wait_until(lambda: any("📋 tasks:" in edit["content"] for edit in adapter.edits),
                   what="the todo result was edited into the progress bubble")
        return {"final_response": "done", "messages": [], "api_calls": 1}


@pytest.mark.asyncio
async def test_run_agent_progress_renders_todo_completed_result(monkeypatch, tmp_path):
    adapter, result = await _run_with_agent(
        monkeypatch, tmp_path, TodoProgressAgent, session_id="sess-todo",
        config_data={"display": {"tool_progress": "all"}},
    )
    assert result["final_response"] == "done"
    assert "planning 2 task(s)" in adapter.sent[0]["content"]
    final_progress = adapter.edits[-1]["content"]
    assert final_progress.startswith("📋 tasks: 2 total")
    assert "planning 2 task(s)" not in final_progress
    assert "▸ doing" in final_progress and "○ todo" in final_progress
    assert final_progress.index("📋 tasks:") < final_progress.index("pytest focused suite")
