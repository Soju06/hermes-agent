"""Tests for kanban goal_mode — per-card Ralph-style goal loop.

Covers three layers:

1. DB: goal_mode / goal_max_turns persist through create_task + from_row,
   and a legacy DB (without the columns) migrates cleanly.
2. Spawn: _default_spawn sets the HERMES_KANBAN_GOAL_MODE env vars only
   when the card opts in.
3. Loop: goals.run_kanban_goal_loop continuation / completion / budget
   behaviour, driven entirely through injected callbacks (no live model).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import goals


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


# ---------------------------------------------------------------------------
# DB layer
# ---------------------------------------------------------------------------





def test_legacy_db_migrates_goal_columns(tmp_path, monkeypatch):
    """A tasks table created without goal columns must gain them on init."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    db_path = kb.kanban_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Minimal legacy schema: tasks table missing goal_mode / goal_max_turns.
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT,
            assignee TEXT,
            status TEXT NOT NULL DEFAULT 'ready',
            priority INTEGER NOT NULL DEFAULT 0,
            created_by TEXT,
            created_at INTEGER NOT NULL,
            started_at INTEGER,
            completed_at INTEGER,
            workspace_kind TEXT NOT NULL DEFAULT 'scratch',
            workspace_path TEXT,
            claim_lock TEXT,
            claim_expires INTEGER
        )
        """
    )
    legacy.execute(
        "INSERT INTO tasks (id, title, status, priority, created_at, workspace_kind) "
        "VALUES ('legacy1', 'old', 'ready', 0, 1, 'scratch')"
    )
    legacy.commit()
    legacy.close()

    # init_db runs the additive migration.
    kb.init_db()
    with kb.connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
        assert "goal_mode" in cols
        assert "goal_max_turns" in cols
        task = kb.get_task(conn, "legacy1")
    # Existing row keeps the safe default.
    assert task.goal_mode is False
    assert task.goal_max_turns is None


# ---------------------------------------------------------------------------
# Spawn env
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Goal loop logic (callback-injected, no live model)
# ---------------------------------------------------------------------------

def _patch_judge(monkeypatch, verdicts):
    """Make judge_goal return a scripted sequence of verdicts."""
    seq = list(verdicts)

    def _fake_judge(goal, response, subgoals=None, background_processes=None, **_kw):
        v = seq.pop(0) if seq else "done"
        # 5-tuple contract: verdict, reason, parse failure, wait, transport failure.
        return v, f"scripted:{v}", False, None, False

    monkeypatch.setattr(goals, "judge_goal", _fake_judge)


def test_loop_stops_when_worker_already_completed(monkeypatch):
    # Worker called kanban_complete on its first turn — no judging needed.
    _patch_judge(monkeypatch, ["continue"])  # should never be consulted
    turns = []

    res = goals.run_kanban_goal_loop(
        task_id="t1",
        goal_text="do the thing",
        run_turn=lambda p: turns.append(p) or "x",
        task_status_fn=lambda: "done",
        block_fn=lambda r: pytest.fail("should not block"),
        first_response="done already",
    )
    assert res["outcome"] == "completed_by_worker"
    assert turns == []  # no extra turns


# ---------------------------------------------------------------------------
# Judge transport failure guard (judge_unavailable block)
# ---------------------------------------------------------------------------

def _patch_judge_scripted(monkeypatch, script):
    """Script judge_goal per call.

    Each item is either "transport" (simulated API-unreachable turn: the
    fail-open ``("continue", ..., transport_failed=True)`` shape judge_goal
    returns for 401/DNS/timeout) or a plain verdict string ("continue",
    "done") for a healthy judge reply.
    """
    seq = list(script)

    def _fake_judge(goal, response, subgoals=None, background_processes=None, **_kw):
        item = seq.pop(0) if seq else "done"
        if item == "transport":
            return "continue", "judge error: AuthenticationError", False, None, True
        return item, f"scripted:{item}", False, None, False

    monkeypatch.setattr(goals, "judge_goal", _fake_judge)


def _pin_failure_limit(monkeypatch, limit=3):
    """Isolate loop tests from the host's real config.yaml."""
    monkeypatch.setattr(goals, "_kanban_judge_failure_limit", lambda: limit)


def test_loop_blocks_when_judge_unreachable_consecutively(monkeypatch):
    """N consecutive transport failures must block early (not burn the budget)."""
    _patch_judge_scripted(monkeypatch, ["transport"] * 10)
    _pin_failure_limit(monkeypatch, 3)
    turns = []
    blocks = []

    res = goals.run_kanban_goal_loop(
        task_id="t1",
        goal_text="do the thing",
        run_turn=lambda p: turns.append(p) or "still working",
        task_status_fn=lambda: "running",
        block_fn=blocks.append,
        max_turns=15,
        first_response="turn one output",
    )
    assert res["outcome"] == "blocked_judge_unavailable"
    # Blocked on the 3rd consecutive judge failure — long before the
    # 15-turn budget the pre-patch loop would have burned.
    assert res["turns_used"] == 3
    assert len(turns) == 2  # two continuation turns ran before the guard hit
    assert len(blocks) == 1
    assert blocks[0].startswith("judge_unavailable:")
    assert "auxiliary.goal_judge" in blocks[0]


def test_loop_transport_counter_resets_on_healthy_verdict(monkeypatch):
    """A usable judge reply resets the streak — flakiness is not a config error."""
    # 2 failures, a healthy continue (reset), then 3 more failures → the
    # block must land on the 6th judge call, not the 3rd.
    _patch_judge_scripted(
        monkeypatch,
        ["transport", "transport", "continue", "transport", "transport", "transport"],
    )
    _pin_failure_limit(monkeypatch, 3)
    blocks = []

    res = goals.run_kanban_goal_loop(
        task_id="t1",
        goal_text="do the thing",
        run_turn=lambda p: "still working",
        task_status_fn=lambda: "running",
        block_fn=blocks.append,
        max_turns=15,
        first_response="turn one output",
    )
    assert res["outcome"] == "blocked_judge_unavailable"
    assert res["turns_used"] == 6
    assert len(blocks) == 1


def test_loop_healthy_judge_behaviour_unchanged(monkeypatch):
    """With a healthy judge the guard never fires — budget path is untouched."""
    _patch_judge_scripted(monkeypatch, ["continue"] * 10)
    _pin_failure_limit(monkeypatch, 3)
    blocks = []

    res = goals.run_kanban_goal_loop(
        task_id="t1",
        goal_text="do the thing",
        run_turn=lambda p: "still working",
        task_status_fn=lambda: "running",
        block_fn=blocks.append,
        max_turns=2,
        first_response="turn one output",
    )
    assert res["outcome"] == "blocked_budget"
    assert len(blocks) == 1
    assert "exhausted its turn budget" in blocks[0]
    # Healthy judge → no transport-failure disclaimer in the block reason.
    assert "transport failure" not in blocks[0]


def test_budget_block_message_tags_transport_failed_last_verdict(monkeypatch):
    """Budget exhaustion whose last judge call errored must say so.

    Pre-patch, "judge died every turn" and "worker never finished" produced
    the same block message; the last-verdict tag is the disambiguator when
    the failure count sits below the limit at exhaustion time.
    """
    # Healthy verdict then a transport failure right at the budget edge —
    # streak (1) stays under the limit (3), so only the budget path fires.
    _patch_judge_scripted(monkeypatch, ["continue", "transport"])
    _pin_failure_limit(monkeypatch, 3)
    blocks = []

    res = goals.run_kanban_goal_loop(
        task_id="t1",
        goal_text="do the thing",
        run_turn=lambda p: "still working",
        task_status_fn=lambda: "running",
        block_fn=blocks.append,
        max_turns=2,
        first_response="turn one output",
    )
    assert res["outcome"] == "blocked_budget"
    assert len(blocks) == 1
    assert "transport failure" in blocks[0]
    assert "judge error: AuthenticationError" in blocks[0]


class TestKanbanJudgeFailureLimitConfig:
    """kanban.goal.judge_failure_limit resolution via _kanban_judge_failure_limit."""

    def _with_config(self, monkeypatch, cfg):
        import hermes_cli.config as config_mod

        monkeypatch.setattr(config_mod, "load_config", lambda: cfg)

    def test_reads_configured_limit(self, monkeypatch):
        self._with_config(monkeypatch, {"kanban": {"goal": {"judge_failure_limit": 7}}})
        assert goals._kanban_judge_failure_limit() == 7

    def test_missing_key_falls_back_to_default(self, monkeypatch):
        self._with_config(monkeypatch, {"kanban": {}})
        assert (
            goals._kanban_judge_failure_limit()
            == goals.DEFAULT_KANBAN_JUDGE_FAILURE_LIMIT
        )

    def test_invalid_values_fall_back_to_default(self, monkeypatch):
        for bad in ("abc", 0, -2, None):
            self._with_config(
                monkeypatch, {"kanban": {"goal": {"judge_failure_limit": bad}}}
            )
            assert (
                goals._kanban_judge_failure_limit()
                == goals.DEFAULT_KANBAN_JUDGE_FAILURE_LIMIT
            ), f"value {bad!r} must fall back"

    def test_configured_limit_drives_loop(self, monkeypatch):
        """End-to-end: a limit of 1 blocks on the first transport failure."""
        self._with_config(monkeypatch, {"kanban": {"goal": {"judge_failure_limit": 1}}})
        _patch_judge_scripted(monkeypatch, ["transport"] * 5)
        blocks = []

        res = goals.run_kanban_goal_loop(
            task_id="t1",
            goal_text="do the thing",
            run_turn=lambda p: "still working",
            task_status_fn=lambda: "running",
            block_fn=blocks.append,
            max_turns=15,
            first_response="turn one output",
        )
        assert res["outcome"] == "blocked_judge_unavailable"
        assert res["turns_used"] == 1
        assert len(blocks) == 1






# ---------------------------------------------------------------------------
# CLI judge gate tests (hermes kanban complete bypass fix)
# ---------------------------------------------------------------------------

class TestCLIJudgeGate:
    """hermes kanban complete must apply the same goal_mode judge gate as the
    kanban_complete tool (Issue #38367 sibling gap).

    Uses mocks for kb.get_task and kb.complete_task to avoid depending on the
    full kanban_db schema; the gate logic is the unit under test.
    """

    def _run(self, monkeypatch, *, goal_mode=True, judge_available=True,
             verdict="done", reason="", complete_ok=True, summary="done"):
        import argparse
        import types
        from unittest.mock import MagicMock
        from hermes_cli.kanban import _cmd_complete

        fake_task = types.SimpleNamespace(
            goal_mode=goal_mode,
            title="Finish report",
            body="acceptance: criteria",
        )
        fake_conn = MagicMock()
        complete_calls: list = []

        def fake_connect_closing():
            from contextlib import contextmanager
            @contextmanager
            def _cm():
                yield fake_conn
            return _cm()

        def fake_complete_task(conn, tid, **kw):
            complete_calls.append(tid)
            return complete_ok

        monkeypatch.setattr("hermes_cli.kanban.kb.get_task", lambda conn, tid: fake_task)
        monkeypatch.setattr("hermes_cli.kanban.kb.complete_task", fake_complete_task)
        monkeypatch.setattr("hermes_cli.kanban.kb.connect_closing", fake_connect_closing)
        monkeypatch.setattr("hermes_cli.kanban._worker_run_id_for", lambda _: None)

        _aux_client = (object(), "judge-model") if judge_available else (None, None)
        monkeypatch.setattr(
            "agent.auxiliary_client.get_text_auxiliary_client",
            lambda name: _aux_client,
        )
        # Match the real judge_goal contract:
        # (verdict, reason, parse_failed, wait_directive, transport_failed)
        monkeypatch.setattr(
            "hermes_cli.goals.judge_goal",
            lambda **kw: (verdict, reason, False, None, False),
        )

        args = argparse.Namespace(task_ids=["t1"], summary=summary, result=None, metadata=None)
        return _cmd_complete(args), complete_calls

    def test_judge_rejects_premature_completion(self, monkeypatch):
        rc, complete_calls = self._run(
            monkeypatch, verdict="continue", reason="criteria not met"
        )
        assert rc != 0, "judge rejection must produce non-zero exit code"
        assert complete_calls == [], (
            "complete_task must NOT be invoked when the judge rejects"
        )


    def test_non_goal_mode_task_skips_gate(self, monkeypatch):
        """Plain (non-goal_mode) tasks are never sent to the judge."""
        rc, complete_calls = self._run(monkeypatch, goal_mode=False)
        assert rc == 0
        assert complete_calls == ["t1"]
