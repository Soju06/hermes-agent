import ast
import json
from pathlib import Path
from types import SimpleNamespace

from agent.agent_runtime_helpers import invoke_tool
from agent.tool_executor import execute_tool_calls_sequential


class _NoopHints:
    def check_tool_call(self, *_args, **_kwargs):
        return ""


class _AllowGuardrails:
    def before_call(self, *_args, **_kwargs):
        return SimpleNamespace(allows_execution=True)


class _DummyAgent:
    def __init__(self):
        self.session_id = "session-1"
        self._interrupt_requested = False
        self.quiet_mode = True
        self.verbose_logging = False
        self.log_prefix_chars = 80
        self._checkpoint_mgr = SimpleNamespace(enabled=False)
        self._tool_guardrails = _AllowGuardrails()
        self._current_tool = None
        self._memory_manager = None
        self._context_engine_tool_names = set()
        self.context_compressor = None
        self.valid_tool_names = {"web_search"}
        self.enabled_toolsets = None
        self.disabled_toolsets = None
        self.tool_progress_callback = None
        self.tool_start_callback = None
        self.tool_complete_callback = None
        self.tool_delay = 0
        self._subdirectory_hints = _NoopHints()
        self.activities = []

    def _touch_activity(self, activity):
        self.activities.append(activity)

    def _vprint(self, *_args, **_kwargs):
        pass

    def _should_emit_quiet_tool_messages(self):
        return False

    def _should_start_quiet_spinner(self):
        return False

    def _append_guardrail_observation(self, _name, _args, result, failed=False):
        return result

    def _record_file_mutation_result(self, *_args, **_kwargs):
        pass

    def _tool_result_content_for_active_model(self, _name, result):
        return result

    def _apply_pending_steer_to_tool_results(self, *_args, **_kwargs):
        pass


def _tool_call(name="web_search", args=None, call_id="call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(args or {"q": "test"}),
        ),
    )


def test_agent_runtime_helper_pre_tool_check_passes_session_and_call_id(monkeypatch):
    captured = {}

    def fake_block(tool_name, args, **kwargs):
        captured.update({"tool_name": tool_name, "args": args, **kwargs})
        return "blocked"

    monkeypatch.setattr("hermes_cli.plugins.get_pre_tool_call_block_message", fake_block)

    result = json.loads(
        invoke_tool(
            _DummyAgent(),
            "web_search",
            {"q": "test"},
            "task-1",
            tool_call_id="call-1",
        )
    )

    assert result == {"error": "blocked"}
    assert captured["task_id"] == "task-1"
    assert captured["session_id"] == "session-1"
    assert captured["tool_call_id"] == "call-1"


def test_sequential_pre_tool_check_passes_session_and_call_id(monkeypatch):
    captured = {}

    def fake_block(tool_name, args, **kwargs):
        captured.update({"tool_name": tool_name, "args": args, **kwargs})
        return "blocked"

    monkeypatch.setattr("hermes_cli.plugins.get_pre_tool_call_block_message", fake_block)
    monkeypatch.setattr(
        "agent.tool_executor.maybe_persist_tool_result",
        lambda *, content, **_kwargs: content,
    )

    messages = []
    execute_tool_calls_sequential(
        _DummyAgent(),
        SimpleNamespace(tool_calls=[_tool_call()]),
        messages,
        effective_task_id="task-1",
    )

    assert captured["task_id"] == "task-1"
    assert captured["session_id"] == "session-1"
    assert captured["tool_call_id"] == "call-1"
    assert messages[-1]["tool_call_id"] == "call-1"
    assert json.loads(messages[-1]["content"]) == {"error": "blocked"}


def test_agent_pre_tool_call_sites_forward_session_id_and_tool_call_id():
    """Regression for skill-gate runtime races.

    Agent-level prechecks fire before model_tools.handle_function_call() and then
    dispatch with skip_pre_tool_call_hook=True. They must pass the agent session
    id themselves; otherwise runtime guard plugins fall back to a process-global
    runtime snapshot that can be overwritten by another gateway session.
    """

    repo = Path(__file__).resolve().parents[2]
    paths = [
        repo / "agent" / "tool_executor.py",
        repo / "agent" / "agent_runtime_helpers.py",
    ]
    calls = []
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name == "get_pre_tool_call_block_message":
                    calls.append((path.name, node.lineno, {kw.arg for kw in node.keywords if kw.arg}))

    assert len(calls) == 3
    for path_name, line_no, kwargs in calls:
        assert "task_id" in kwargs, (path_name, line_no, kwargs)
        assert "session_id" in kwargs, (path_name, line_no, kwargs)
        assert "tool_call_id" in kwargs, (path_name, line_no, kwargs)
