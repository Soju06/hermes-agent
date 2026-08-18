"""Regression tests for the delegated-child marker latch (proposal draft).

These are the pytest form of /home/ubuntu/work/comm-improve/latch-bug/repro.py
and repro_thread.py, trimmed to run in-process with no gateway.

Place at: tests/tools/test_delegated_child_marker_latch.py
"""
from __future__ import annotations

import os
import threading

import pytest


# ---------------------------------------------------------------------------
# Fix A — the shared bash session snapshot must not persist the marker or the
# dispatcher's Kanban identity.
# ---------------------------------------------------------------------------

def test_snapshot_dump_unsets_delegated_marker_and_kanban_vars():
    """The snapshot is a write-through env store: whatever ``export -p`` emits
    is re-sourced by every later command on the SAME environment, including the
    parent's next turn. A delegate_task child's command therefore persists its
    own marker unless the dump unsets it first.
    """
    from tools.environments.base_session_env import _export_dump_excluding_session_vars

    snippet = _export_dump_excluding_session_vars('"$__hermes_snap_tmp"')

    # The marker must be unset by name.
    assert "HERMES_DELEGATED_CHILD_CONTEXT" in snippet
    # HERMES_KANBAN_* must be unset by prefix so a dispatcher worker's identity
    # cannot be re-sourced by a child whose Popen env was scrubbed.
    assert "${!HERMES_KANBAN_*}" in snippet
    # Unsets happen inside the subshell, BEFORE export -p (line-based grep
    # filtering is unsafe for multi-line values — issue #71296).
    assert snippet.index("HERMES_DELEGATED_CHILD_CONTEXT") < snippet.index("export -p")


@pytest.mark.timeout(180)
def test_marker_does_not_latch_through_the_shared_snapshot(tmp_path, monkeypatch):
    """End-to-end: a real LocalEnvironment, a real delegated_child_context().

    Asserts BOTH directions:
      * the child sees the marker and still sees the board *location* (upstream
        keeps ``HERMES_KANBAN_BOARD`` / ``_DB`` in descendants so board routing
        survives the scrub);
      * the parent's next command sees no marker, and the board location is
        still there (the snapshot unset must not lose what the Popen env
        re-supplies every command).

    The parent is deliberately NOT a dispatcher worker: since upstream's
    "keep Kanban worker scope out of descendant processes", every native shell
    child of a worker (its own included) is unowned — ``HERMES_KANBAN_TASK`` is
    stripped and the marker stamped — so a worker's shell shows the marker on
    every command by design and a latch is not observable there.
    """
    from agent.delegation_context import (
        DELEGATED_CHILD_ENV_MARKER,
        delegated_child_context,
    )
    from tools.environments.local import LocalEnvironment

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "parent-board")
    monkeypatch.delenv(DELEGATED_CHILD_ENV_MARKER, raising=False)

    probe = (
        f'echo "marker=[${{{DELEGATED_CHILD_ENV_MARKER}-unset}}]"; '
        'echo "board=[${HERMES_KANBAN_BOARD-unset}]"'
    )

    env = LocalEnvironment(cwd=str(tmp_path), timeout=60)
    env.init_session()
    try:
        def read():
            out = env.execute(probe).get("output", "")
            vals = {}
            for line in out.splitlines():
                line = line.strip()
                for key in ("marker", "board"):
                    if line.startswith(f"{key}=["):
                        vals[key] = line[len(key) + 2:].rstrip("]")
            return vals

        before = read()
        assert before["marker"] == "unset"
        assert before["board"] == "parent-board"

        with delegated_child_context("child-session"):
            during = read()
        # Upstream stamps the fenced board ROOT as the marker value (a legacy "1" fences everything).
        assert during["marker"] not in ("unset", ""), "child must see its own lineage marker"
        assert during["board"] == "parent-board", (
            "board location must survive the scrub (upstream contract: identity "
            "is stripped, routing is retained)"
        )

        after = read()
        assert after["marker"] == "unset", (
            "LATCH: the marker persisted into the parent's next command via the "
            "shared bash session snapshot"
        )
        assert after["board"] == "parent-board", (
            "unsetting HERMES_KANBAN_* inside the snapshot dump must not lose the "
            "location the Popen env re-supplies on every command"
        )
    finally:
        env.cleanup()


# ---------------------------------------------------------------------------
# Fix B — ContextVar propagation is scope-correct, not blanket.
# ---------------------------------------------------------------------------

def _flag_seen_on_worker(wrap) -> bool:
    from agent.delegation_context import is_delegated_child_context

    seen: dict[str, bool] = {}

    def _probe():
        seen["flag"] = is_delegated_child_context()

    t = threading.Thread(target=wrap(_probe), daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "probe thread did not finish"
    return bool(seen.get("flag"))


def test_in_turn_worker_keeps_the_delegated_child_marker():
    """The concurrent tool fan-out and execute_code RPC loops run the CHILD's
    own tool calls. Dropping the flag there would let a delegated child mutate
    Kanban board state (tools.kanban_tools._reject_delegated_child_mutation).
    """
    from agent.delegation_context import delegated_child_context
    from tools.thread_context import propagate_context_to_thread

    with delegated_child_context("child-in-turn"):
        assert _flag_seen_on_worker(propagate_context_to_thread) is True


def test_detached_daemon_does_not_inherit_the_delegated_child_marker():
    """ingest-curator / bg-review / async_delegation workers are wrapped inside
    a turn but outlive it. Inheriting the flag latches it for the process
    lifetime: the Kanban toolset stays suppressed and the tool-definition cache
    key (model_tools) stays poisoned.
    """
    from agent.delegation_context import delegated_child_context
    from tools.thread_context import propagate_context_to_thread

    def detached(fn):
        return propagate_context_to_thread(fn, inherit_delegated_child=False)

    with delegated_child_context("child-detached"):
        assert _flag_seen_on_worker(detached) is False


def test_no_marker_leaks_without_any_child_context():
    from tools.thread_context import propagate_context_to_thread

    assert _flag_seen_on_worker(propagate_context_to_thread) is False


@pytest.mark.parametrize(
    "module_path,symbol",
    [
        ("agent.ingest_curator", "spawn_curation_thread"),
        ("tools.async_delegation", "dispatch_async_delegation"),
    ],
)
def test_detached_call_sites_opt_out(module_path, symbol):
    """Contract test: the detached spawn sites must pass
    ``inherit_delegated_child=False``. Asserting the source keeps a future
    refactor from silently reintroducing the latch.
    """
    import importlib
    import inspect

    mod = importlib.import_module(module_path)
    src = inspect.getsource(mod)
    assert "inherit_delegated_child=False" in src, (
        f"{module_path} spawns a detached daemon via "
        "propagate_context_to_thread without opting out of marker inheritance"
    )
    assert hasattr(mod, symbol)


# ---------------------------------------------------------------------------
# Fix C — the mismatch diagnostic fires once and changes nothing.
# ---------------------------------------------------------------------------

def test_env_marker_without_contextvar_logs_once(monkeypatch, caplog):
    import logging

    import agent.delegation_context as dc

    monkeypatch.setattr(dc, "_MARKER_MISMATCH_WARNED", False, raising=False)
    monkeypatch.setenv(dc.DELEGATED_CHILD_ENV_MARKER, "1")

    with caplog.at_level(logging.DEBUG, logger="agent.delegation_context"):
        assert dc.is_delegated_child_process_context() is True
        assert dc.is_delegated_child_process_context() is True

    hits = [r for r in caplog.records if "no delegated-child ContextVar" in r.message]
    assert len(hits) == 1, "the diagnostic must be once-per-process, not per call"


def test_diagnostic_silent_when_contextvar_is_the_source(monkeypatch, caplog):
    import logging

    import agent.delegation_context as dc

    monkeypatch.setattr(dc, "_MARKER_MISMATCH_WARNED", False, raising=False)
    monkeypatch.delenv(dc.DELEGATED_CHILD_ENV_MARKER, raising=False)

    with caplog.at_level(logging.DEBUG, logger="agent.delegation_context"):
        with dc.delegated_child_context("child"):
            assert dc.is_delegated_child_process_context() is True

    assert not [r for r in caplog.records if "no delegated-child ContextVar" in r.message]


def test_reset_delegated_child_context_in_does_not_touch_the_caller():
    import contextvars

    from agent.delegation_context import (
        delegated_child_context,
        is_delegated_child_context,
        reset_delegated_child_context_in,
    )

    with delegated_child_context("child"):
        ctx = contextvars.copy_context()
        reset_delegated_child_context_in(ctx)
        assert is_delegated_child_context() is True, (
            "clearing a copied Context must not clear the caller's own"
        )
        assert ctx.run(is_delegated_child_context) is False


# ---------------------------------------------------------------------------
# Fix D — the Kanban scrub must cover the whole variable class, not a list.
# ---------------------------------------------------------------------------

def test_scrub_removes_every_kanban_var_the_dispatcher_injects():
    """Class-wide invariant, not a snapshot of today's key list.

    The dispatcher's worker-env builder is the growing side of this contract:
    HERMES_KANBAN_BRANCH / _GOAL_MODE / _GOAL_MAX_TURNS were all added after
    KANBAN_ENV_KEYS was written and escaped the scrub for that whole window.
    This reads what the dispatcher actually assigns and requires the scrubber
    to cover all of it — except the board *location* keys upstream deliberately
    keeps in descendants (``KANBAN_LOCATION_ENV_KEYS``), which must survive.
    """
    import re
    from pathlib import Path

    import hermes_cli.kanban_db as kdb
    from agent.delegation_context import KANBAN_LOCATION_ENV_KEYS, scrub_kanban_env

    # The worker-env builder lives in the kanban_db* module family (it moved
    # from kanban_db.py to kanban_db_dispatch.py upstream); scan all of them.
    src = "\n".join(
        path.read_text()
        for path in sorted(Path(kdb.__file__).parent.glob("kanban_db*.py"))
    )
    injected = sorted(set(re.findall(
        r"""env\[["'](HERMES_KANBAN_[A-Z_]+)["']\]\s*=""", src
    )))
    assert injected, "could not find the dispatcher's worker env assignments"

    scrubbed = scrub_kanban_env({k: "x" for k in injected} | {"PATH": "/usr/bin"})
    leaked = sorted(k for k in injected if k in scrubbed and k not in KANBAN_LOCATION_ENV_KEYS)
    assert not leaked, (
        f"scrub_kanban_env left dispatcher vars in a delegated child's env: {leaked}. "
        "Strip by the HERMES_KANBAN_ prefix so new dispatcher variables are "
        "covered when they are introduced."
    )
    kept = sorted(k for k in injected if k in KANBAN_LOCATION_ENV_KEYS)
    assert kept, "expected the dispatcher to inject at least one location key"
    assert all(k in scrubbed for k in kept), "board location must survive the scrub"
    assert scrubbed["PATH"] == "/usr/bin", "unrelated env must survive"


def test_scrub_is_prefix_based_not_allowlist_based():
    """A hypothetical future dispatcher variable must already be covered."""
    from agent.delegation_context import (
        DELEGATED_CHILD_ENV_MARKER,
        _fenced_kanban_root,
        scrub_kanban_env,
    )

    cleaned = scrub_kanban_env({
        "HERMES_KANBAN_SOME_FUTURE_KNOB": "1",
        "HERMES_SESSION_KEY": "keep-me",
    })
    assert "HERMES_KANBAN_SOME_FUTURE_KNOB" not in cleaned
    assert cleaned["HERMES_SESSION_KEY"] == "keep-me"
    # Upstream's path-valued write fence: the marker carries the lineage's board root.
    assert cleaned[DELEGATED_CHILD_ENV_MARKER] == _fenced_kanban_root()
