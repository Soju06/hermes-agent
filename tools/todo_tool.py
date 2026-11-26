"""Todo tool: in-memory, revisioned task list for multi-step work. State lives on the
AIAgent (one per session), is re-injected after context compression, and every write bumps
a monotonic revision so UI clients can reject stale updates. One ``todo_list`` tool: pass
``todos`` to write, omit to read; every call returns the full list. No system-prompt mutation."""

import json
from typing import Any, Dict, List, Optional

VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
# The list is re-read after every compression (format_for_injection), so unbounded
# content/count would defeat the compression it rides through. Caps apply equally to
# model-authored items and caller-replayed API history.
MAX_TODO_CONTENT_CHARS = 4000
MAX_TODO_ITEMS = 256
# Max single todo tool-result payload accepted during history hydration, so a forged
# oversized result is dropped before parsing (AIAgent._hydrate_todo_store).
MAX_TODO_RESULT_CHARS = 512_000
_TRUNCATION_MARKER = "… [truncated]"
# Persisted as ordinary message content; ContextCompressor keys on this stable header to
# tell the synthetic post-compaction row from a real user message.
TODO_INJECTION_HEADER = "[Your active task list was preserved across context compression]"
_STATUS_MARKERS = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]", "cancelled": "[~]"}
_ACTIVE_STATUSES = {"pending", "in_progress"}


class TodoStore:
    """In-memory todo list, one per AIAgent. List position is priority; items are
    ``{id, content, status, parent?}`` — ``parent`` nests a subtask."""

    def __init__(self):
        self._items: list[dict[str, str]] = []
        self._revision = 0

    def _fresh_items(self, todos: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Validate, dedupe and order a whole new list (replace / restore)."""
        return self._normalize_order([self._validate(t) for t in self._dedupe_by_id(todos)])

    def write(self, todos: list[dict[str, Any]], merge: bool = False) -> list[dict[str, str]]:
        """Replace the list (default) or merge by id; returns the full list after writing.
        Raises ValueError (leaving the list untouched) if any item is invalid."""
        before = self.read()
        try:
            if merge:
                self._merge(todos)
            else:
                self._items = self._fresh_items(todos)
        except ValueError:
            self._items = before  # rejected write must not leave a partial list behind
            raise
        del self._items[MAX_TODO_ITEMS:]  # keep the priority head; replays can't grow unbounded
        self._sanitize_parents(self._items)
        if self._items != before:
            self._revision += 1
        return self.read()

    def _merge(self, todos: list[dict[str, Any]]) -> None:
        """Update existing items only in the fields provided; append new ones (validated)."""
        existing = {item["id"]: item for item in self._items}
        for t in self._dedupe_by_id(todos):
            item_id = str(t.get("id", "")).strip()
            if not item_id:
                continue  # can't merge without an id
            cur = existing.get(item_id)
            if cur is None:
                validated = self._validate(t)
                existing[validated["id"]] = validated
                self._items.append(validated)
                continue
            if t.get("content"):
                cur["content"] = self._cap_content(str(t["content"]).strip())
            if t.get("status") and str(t["status"]).strip().lower() in VALID_STATUSES:
                cur["status"] = str(t["status"]).strip().lower()
            active_form = self._read_active_form(t)
            if active_form:
                cur["active_form"] = active_form
            if "parent" in t:
                parent = str(t["parent"] or "").strip()
                if parent:
                    cur["parent"] = parent
                else:
                    cur.pop("parent", None)
        # Rebuild preserving original order for existing items (first occurrence wins).
        rebuilt = {item["id"]: existing.get(item["id"], item) for item in self._items}
        self._items = self._normalize_order(list(rebuilt.values()))

    def read(self) -> list[dict[str, str]]:
        return [item.copy() for item in self._items]

    def has_items(self) -> bool:
        return bool(self._items)

    def snapshot(self) -> dict[str, Any]:
        """Full state clients can reconcile atomically."""
        return {"todos": self.read(), "revision": self._revision}

    def restore(self, todos: list[dict[str, Any]], *, revision: Any = 0) -> list[dict[str, str]]:
        """Restore a trusted snapshot without manufacturing a new revision."""
        self._items = self._fresh_items(todos)[:MAX_TODO_ITEMS]
        try:
            self._revision = max(0, int(revision or 0))
        except (TypeError, ValueError):
            self._revision = 0
        return self.read()

    def format_for_injection(self) -> Optional[str]:
        """Render the list for post-compression injection, or None if nothing active. Only
        pending/in_progress items are injected — finished ones make the model re-do work after
        compression. A parent is kept (with its real status marker) when any descendant is
        active so subtasks keep context."""
        if not self._items:
            return None
        children: dict[str, list[dict[str, str]]] = {}
        for item in self._items:
            if item.get("parent"):
                children.setdefault(item["parent"], []).append(item)

        def render(item: dict[str, str], depth: int, out: list[str]) -> bool:
            kid_lines: list[str] = []
            has_active_kid = False
            for kid in children.get(item["id"], []):
                has_active_kid |= render(kid, depth + 1, kid_lines)
            keep = item["status"] in _ACTIVE_STATUSES or has_active_kid
            if keep:
                marker = _STATUS_MARKERS.get(item["status"], "[?]")
                out.append(f"{'  ' * depth}- {marker} {item['id']}. "
                           f"{item['content']} ({item['status']})")
                out.extend(kid_lines)
            return keep

        lines = [TODO_INJECTION_HEADER]
        for item in self._items:
            if not item.get("parent"):
                render(item, 0, lines)
        return "\n".join(lines) if len(lines) > 1 else None

    @staticmethod
    def _cap_content(content: str) -> str:
        """Truncate to MAX_TODO_CONTENT_CHARS keeping the head (the actionable part) + marker."""
        if len(content) > MAX_TODO_CONTENT_CHARS:
            return content[:MAX_TODO_CONTENT_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
        return content

    @staticmethod
    def _validate(item: dict[str, Any]) -> dict[str, str]:
        """Normalize one item to ``{id, content, status, parent?, active_form?}`` (placeholders
        when missing); ``active_form`` is kept only when the caller supplied one."""
        if not isinstance(item, dict):
            return {"id": "?", "content": "(invalid item)", "status": "pending"}
        item_id = str(item.get("id", "")).strip() or "?"
        content = str(item.get("content", "")).strip()
        if not content:
            raise ValueError(
                f"Todo item '{item_id}' has empty or missing content. "
                "Each item must have a non-empty description."
            )
        status = str(item.get("status", "pending")).strip().lower()
        result = {"id": item_id,
                  "content": TodoStore._cap_content(content),
                  "status": status if status in VALID_STATUSES else "pending"}
        parent = str(item.get("parent") or "").strip()
        if parent and parent != item_id:
            result["parent"] = parent
        active_form = TodoStore._read_active_form(item)
        if active_form:
            result["active_form"] = active_form
        return result

    @staticmethod
    def _read_active_form(item: dict[str, Any]) -> str:
        """Normalized present-continuous label for an item. Accepts both the snake_case name
        used across this codebase and the camelCase ``activeForm`` spelling, so a model that
        has internalized the camelCase convention from another harness is not silently
        dropped. Empty/blank values normalize to "" (field omitted)."""
        raw = item.get("active_form")
        if raw is None:
            raw = item.get("activeForm")
        if raw is None:
            return ""
        text = str(raw).strip()
        return TodoStore._cap_content(text) if text else ""

    @staticmethod
    def _sanitize_parents(items: list[dict[str, str]]) -> None:
        """Drop dangling parent refs and break cycles in place (such items become roots)."""
        by_id = {item["id"]: item for item in items}
        for item in items:
            if item.get("parent") and item["parent"] not in by_id:
                item.pop("parent", None)
        for item in items:
            seen, node = {item["id"]}, item
            while node.get("parent"):
                if node["parent"] in seen:
                    item.pop("parent", None)
                    break
                seen.add(node["parent"])
                node = by_id[node["parent"]]

    @staticmethod
    def _dedupe_by_id(todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Collapse duplicate ids, keeping the last occurrence in its position."""
        last_index: dict[str, int] = {}
        for i, item in enumerate(todos):  # non-dicts get a synthetic key; _validate handles them
            key = str(item.get("id", "")).strip() if isinstance(item, dict) else f"__invalid_{i}"
            last_index[key or "?"] = i
        return [todos[i] for i in sorted(last_index.values())]

    @staticmethod
    def _normalize_order(items: list[dict[str, str]]) -> list[dict[str, str]]:
        """Lift the in_progress step ahead of any earlier pending placeholder. Nested lists
        keep authored order — reordering would tear a subtask from its siblings."""
        statuses = [item["status"] for item in items]
        if any(item.get("parent") for item in items) or "in_progress" not in statuses:
            return items
        active_index = statuses.index("in_progress")
        if "pending" not in statuses[:active_index]:
            return items
        normalized = items.copy()
        normalized.insert(statuses.index("pending"), normalized.pop(active_index))
        return normalized


def todo_tool(todos: Optional[list[dict[str, Any]]] = None, merge: bool = False,
              store: Optional[TodoStore] = None) -> str:
    """Write ``todos`` (replace, or ``merge`` by id) or read when None -> list + summary JSON."""
    if store is None:
        return tool_error("TodoStore not initialized")
    if todos is None:
        items = store.read()
    else:
        if not isinstance(todos, list):
            return tool_error(f"todos must be a list, got {type(todos).__name__}")
        try:
            items = store.write(todos, merge)
        except ValueError as e:
            return tool_error(str(e))
    summary = {"total": len(items)}
    for status in ("pending", "in_progress", "completed", "cancelled"):
        summary[status] = sum(1 for i in items if i["status"] == status)
    payload = {"todos": items, "revision": store.snapshot()["revision"], "summary": summary}
    if todos is not None:
        # The same continuation nudge Claude Code returns from TodoWrite, verbatim. CC's tool
        # result is that sentence alone; here it rides inside the JSON payload because
        # AIAgent._hydrate_todo_store() reconstructs session todo state by json.loads()-ing
        # the newest todo tool result — a plain-text result would break session restoration.
        payload["message"] = (
            "Todos have been modified successfully. Ensure that you continue "
            "to use the todo list to track your progress. Please proceed with "
            "the current tasks if applicable"
        )
    return json.dumps(payload, ensure_ascii=False)


def check_todo_requirements() -> bool:
    """Todo tool has no external requirements -- always available."""
    return True


# Behavioral guidance is baked into the (static, cached) description; item shape and merge
# semantics live ONLY in the parameter schema.
TODO_SCHEMA = {
    "name": "todo_list",
    "description": (
        # Claude Code TodoWrite's when-to-use / when-not-to-use / state-management guidance
        # (v2.1.226), adapted to this tool's read/merge calling convention (#95681 keeps it
        # static so the cached prompt prefix survives). The first sentence leads with the
        # capability and fits the tool_search catalog line (CATALOG_SUMMARY_MAX_CHARS).
        "Create and manage a structured task list for your current coding "
        "session. This helps you track progress, organize "
        "complex tasks, and demonstrate thoroughness to the user.\n"
        "It also helps the user understand the progress of the task and "
        "overall progress of their requests.\n\n"
        "## When to Use This Tool\n"
        "Use this tool proactively in these scenarios:\n\n"
        "1. Complex multi-step tasks (3+ steps) - When a task requires 3 or more "
        "distinct steps or actions\n"
        "2. Non-trivial and complex tasks - Tasks that require careful "
        "planning or multiple operations\n"
        "3. User explicitly requests todo list - When the user directly asks "
        "you to use the todo list\n"
        "4. User provides multiple tasks - When users provide a list of "
        "things to be done (numbered or comma-separated). For 'all N items' "
        "tasks, enumerate every instance as its own checklist item so none "
        "are silently dropped\n"
        "5. After receiving new instructions - Immediately capture user "
        "requirements as todos\n"
        "6. When you start working on a task - Mark it as in_progress BEFORE "
        "beginning work. Ideally you should only have one todo as "
        "in_progress at a time\n"
        "7. After completing a task - Mark it as completed and add any new "
        "follow-up tasks discovered during implementation\n\n"
        "## When NOT to Use This Tool\n"
        "Skip using this tool when:\n"
        "1. There is only a single, straightforward task\n"
        "2. The task is trivial and tracking it provides no organizational "
        "benefit\n"
        "3. The task can be completed in less than 3 trivial steps\n"
        "4. The task is purely conversational or informational\n\n"
        "NOTE that you should not use this tool if there is only one trivial "
        "task to do. In this case you are better off just doing the task "
        "directly.\n\n"
        "## Task States and Management\n\n"
        "1. **Task States**: Use these states to track progress:\n"
        "   - pending: Task not yet started\n"
        "   - in_progress: Currently working on (limit to ONE task at a time)\n"
        "   - completed: Task finished successfully\n"
        "   - cancelled: Task abandoned (superseded or no longer relevant)\n\n"
        "   **IMPORTANT**: Task descriptions must have two forms:\n"
        "   - content: The imperative form describing what needs to be done "
        "(e.g., \"Run tests\", \"Build the project\")\n"
        "   - active_form: The present continuous form shown during execution "
        "(e.g., \"Running tests\", \"Building the project\")\n\n"
        "2. **Task Management**:\n"
        "   - Update task status in real-time as you work\n"
        "   - Mark tasks complete IMMEDIATELY after finishing (don't batch "
        "completions)\n"
        "   - Only ONE item in_progress at a time — exactly one task must be "
        "in_progress at any time (not less, not more)\n"
        "   - Complete current tasks before starting new ones\n"
        "   - Remove tasks that are no longer relevant from the list "
        "entirely, or mark them cancelled\n"
        "   - Break large phases into subtasks via parent\n\n"
        "3. **Task Completion Requirements**:\n"
        "   - ONLY mark a task as completed when you have FULLY accomplished "
        "it (verified done, never based on intent)\n"
        "   - If you encounter errors, blockers, or cannot finish, keep the "
        "task as in_progress\n"
        "   - When blocked, create a new task describing what needs to be "
        "resolved; if something fails, cancel it and add a revised item\n"
        "   - Never mark a task as completed if:\n"
        "     - Tests are failing\n"
        "     - Implementation is partial\n"
        "     - You encountered unresolved errors\n"
        "     - You couldn't find necessary files or dependencies\n\n"
        "4. **Task Breakdown**:\n"
        "   - Create specific, actionable items\n"
        "   - Break complex tasks into smaller, manageable steps\n"
        "   - Use clear, descriptive task names\n"
        "   - Always provide both forms:\n"
        "     - content: \"Fix authentication bug\"\n"
        "     - active_form: \"Fixing authentication bug\"\n\n"
        "## Calling Convention\n"
        "- Call with no parameters to read the current list\n"
        "- Provide 'todos' to create/update items (the merge parameter picks "
        "replace-the-plan vs update-by-id)\n"
        "- List order is priority. Always returns the full current list\n\n"
        "When in doubt, use this tool. Being proactive with task management "
        "demonstrates attentiveness and ensures you complete all "
        "requirements successfully."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The updated todo list. Omit to read the current list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string"
                        },
                        "content": {
                            "type": "string",
                            "description": "The task description in imperative form (e.g., \"Run tests\")"
                        },
                        "active_form": {
                            "type": "string",
                            "description": (
                                "The present continuous form shown during execution (e.g., \"Running tests\")"
                            )
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "cancelled"]
                        },
                        "parent": {
                            "type": "string",
                            "description": "Optional id of another item, making this a nested subtask. Omit for top-level."
                        }
                    },
                    "required": ["id", "content", "status"]
                }
            },
            "merge": {
                "type": "boolean",
                "description": (
                    "true: update existing items by id, add new ones. "
                    "false (default): replace the entire list with a fresh plan."
                ),
                "default": False
            }
        },
        "required": []
    }
}

# Pre-rename names that replay as the Todo tool. model_tools._LEGACY_TOOL_ALIASES derives its todo
# entries from this, so the alias map and the transcript/TUI predicates below cannot drift.
TODO_LEGACY_ALIASES = ("todo",)
TODO_TOOL_NAMES = frozenset((TODO_SCHEMA["name"], *TODO_LEGACY_ALIASES))


def is_todo_tool_name(name: Any) -> bool:
    """True for the Todo tool's current name or a legacy alias (an already-unwrapped dispatch name)."""
    return isinstance(name, str) and name in TODO_TOOL_NAMES


def is_todo_tool_call(tool_call: Any) -> bool:
    """True when a transcript tool_call entry (dict or object) invoked the Todo tool.

    Covers the current name, legacy aliases, and the ``tool_call`` bridge (``todo_list`` is deferred by
    default, and the transcript keeps the bridge name). The bridge is peeled from the recorded arguments
    only, never live tool-search config, and must wrap exactly one call. Keep this module free of model_tools / agent.tool_executor
    imports: TUI resume and run_agent call this without loading either.
    """
    from session_rules.turns import _tc_field

    fn = _tc_field(tool_call, "function")
    name, raw_args = _tc_field(fn, "name") or "", _tc_field(fn, "arguments")
    if is_todo_tool_name(name):
        return True
    # Cheap heuristic before the bridge modules load: skip args without a literal "todo". Only a
    # unicode-escaped name slips past, which json.dumps never writes for ASCII.
    if isinstance(raw_args, str) and "todo" not in raw_args:
        return False
    from tools.tool_search_catalog import TOOL_CALL_NAME

    if name != TOOL_CALL_NAME:
        return False
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError:
        return False
    if not isinstance(args, dict):
        return False
    from tools.tool_search_validation import normalize_tool_call_entries

    entries, error = normalize_tool_call_entries(args)
    return not error and len(entries) == 1 and is_todo_tool_name(entries[0]["name"])


from tools.registry import registry, tool_error

registry.register(
    name="todo_list", toolset="todo", schema=TODO_SCHEMA, check_fn=check_todo_requirements,
    handler=lambda args, **kw: todo_tool(
        todos=args.get("todos"), merge=args.get("merge", False), store=kw.get("store")),
    emoji="📋")
