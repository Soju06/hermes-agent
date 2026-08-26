"""Context-local state for delegate_task child execution.

The parent Hermes process may itself be a Kanban dispatcher worker with
HERMES_KANBAN_* variables in process env. delegate_task children run inside the
same Python process, but they are not dispatcher-owned Kanban workers. This
module lets code paths that resolve tool schemas or spawn subprocesses fail
closed for delegated children without mutating global os.environ for the parent.

Cron jobs need the same treatment for the same reason: ``cronjob(action="run")``
executes ``run_job()`` in-process, so a cron agent fired from inside a Kanban
worker would otherwise inherit that worker's dispatcher identity.
``non_dispatcher_owned_context()`` covers both cases.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import Context, ContextVar, Token
from typing import Iterator, Mapping, MutableMapping

_DELEGATED_CHILD_CONTEXT: ContextVar[bool] = ContextVar(
    "hermes_delegated_child_context",
    default=False,
)

# Set for any in-process execution that is NOT the dispatcher-owned worker even
# though the worker's HERMES_KANBAN_* vars are legitimately in os.environ (cron
# jobs fired via the `cronjob` tool).  Kept separate from
# _DELEGATED_CHILD_CONTEXT so the delegate_task-specific behaviour attached to
# that flag (subprocess env scrubbing, its own error strings) is unchanged.
_NON_DISPATCHER_OWNED_CONTEXT: ContextVar[bool] = ContextVar(
    "hermes_non_dispatcher_owned_context",
    default=False,
)

DELEGATED_CHILD_ENV_MARKER = "HERMES_DELEGATED_CHILD_CONTEXT"

KANBAN_ENV_PREFIX = "HERMES_KANBAN_"

# Enumerated for documentation/tests only. Do NOT use this tuple to decide what
# to strip: the dispatcher (``hermes_cli/kanban_db.py``) grows its worker env
# over time (HERMES_KANBAN_BRANCH, _GOAL_MODE, _GOAL_MAX_TURNS were all added
# after this list was written and silently escaped the scrub for that whole
# window). ``scrub_kanban_env`` strips by prefix so a new dispatcher variable is
# covered on the day it is introduced instead of the day someone notices.
KANBAN_ENV_KEYS: tuple[str, ...] = (
    "HERMES_KANBAN_TASK",
    "HERMES_KANBAN_RUN_ID",
    "HERMES_KANBAN_WORKSPACE",
    "HERMES_KANBAN_WORKSPACES_ROOT",
    "HERMES_KANBAN_CLAIM_LOCK",
    "HERMES_KANBAN_BOARD",
    "HERMES_KANBAN_DB",
    "HERMES_KANBAN_BRANCH",
    "HERMES_KANBAN_GOAL_MODE",
    "HERMES_KANBAN_GOAL_MAX_TURNS",
)


@contextmanager
def delegated_child_context(session_id: str | None = None) -> Iterator[None]:
    """Mark child execution and isolate its task-local session identity.

    Child construction calls ``set_current_session_id`` internally, so even a
    context entered without an id must restore the parent's ContextVar.  Child
    execution passes its explicit id and receives it only for this scope.
    """
    token = _DELEGATED_CHILD_CONTEXT.set(True)
    try:
        # Import lazily: session_context calls is_delegated_child_context() when
        # deciding whether the compatibility os.environ mirror is safe.
        from gateway.session_context import scoped_current_session_id

        with scoped_current_session_id(session_id):
            yield
    finally:
        _DELEGATED_CHILD_CONTEXT.reset(token)


def is_delegated_child_context() -> bool:
    """Return True while code is running for a delegate_task child."""
    return bool(_DELEGATED_CHILD_CONTEXT.get())


@contextmanager
def non_dispatcher_owned_context() -> Iterator[None]:
    """Mark in-process execution that does NOT own the dispatcher's Kanban task.

    A Kanban worker is a normal CLI agent whose default toolset includes
    ``cronjob``; ``cronjob(action="run")`` runs ``run_job()`` inside the worker's
    own process, where ``HERMES_KANBAN_TASK`` is legitimately set.  Without this
    marker the cron agent is misread as that worker: the kanban toolset is
    force-added, the worker protocol is injected into its system prompt, and
    ``kanban_complete`` defaults ``task_id`` to ``$HERMES_KANBAN_TASK`` — letting
    an unrelated cron job close the worker's task and overwrite real results.

    Scoped via ContextVar rather than by clearing ``os.environ``: the env is
    process-global and shared with the worker's own claim heartbeat, the
    gateway's Kanban watchers, and concurrent cron jobs on the parallel pool, so
    mutating it would starve the worker's claim and race those readers.
    """
    token = _NON_DISPATCHER_OWNED_CONTEXT.set(True)
    try:
        yield
    finally:
        _NON_DISPATCHER_OWNED_CONTEXT.reset(token)


def is_dispatcher_owned_worker_context() -> bool:
    """Return True only when this execution owns the dispatcher's Kanban task.

    The single predicate every ``HERMES_KANBAN_*`` identity gate should use
    before trusting those vars.  False for delegate_task children and for cron
    jobs fired in-process from a worker.
    """
    if _DELEGATED_CHILD_CONTEXT.get():
        return False
    return not _NON_DISPATCHER_OWNED_CONTEXT.get()


def enter_non_dispatcher_owned_context() -> Token[bool]:
    """Token-based form of :func:`non_dispatcher_owned_context`.

    For callers whose scope is a long ``try`` with a matching ``finally`` rather
    than a ``with`` block (``cron.scheduler.run_job``).  Pair with
    :func:`exit_non_dispatcher_owned_context`.
    """
    return _NON_DISPATCHER_OWNED_CONTEXT.set(True)


def exit_non_dispatcher_owned_context(token: Token[bool]) -> None:
    """Restore the flag saved by :func:`enter_non_dispatcher_owned_context`."""
    _NON_DISPATCHER_OWNED_CONTEXT.reset(token)


def reset_delegated_child_context_in(ctx: Context) -> None:
    """Clear the delegated-child marker inside a *copied* Context.

    ``contextvars.Context`` cannot be written from the outside, but a value set
    while running inside it persists in that object — so this is the supported
    way to hand a worker thread a context that is otherwise identical to its
    parent's but is NOT flagged as delegated-child. Used by
    ``tools.thread_context.propagate_context_to_thread``: the marker is a scope
    around one synchronous child run, not an inheritable identity, and detached
    daemon threads (ingest-curator, bg-review, async delegation, execute_code
    RPC loops) outlive that scope.
    """
    ctx.run(_DELEGATED_CHILD_CONTEXT.set, False)


def is_delegated_child_process_context() -> bool:
    """Return True in this process or a subprocess spawned by a child.

    The env marker is the cross-process half of the signal: a subprocess a
    delegated child spawns has no ContextVar, so ``scrub_kanban_env`` stamps
    ``HERMES_DELEGATED_CHILD_CONTEXT`` into its environment instead.

    A mismatch — marker present in ``os.environ`` while the ContextVar is
    unset — means this process inherited the marker rather than being scoped by
    it. That is normal and correct for a genuine grandchild subprocess, but it
    is also exactly what a *latched* marker looks like: a long-lived gateway
    that once picked the marker up (e.g. from a poisoned bash session snapshot)
    will fail every Kanban mutation for the rest of its life with no visible
    cause. Emit one debug line per process so the state is diagnosable from
    logs without changing behavior.
    """
    import os

    ctx_flag = bool(_DELEGATED_CHILD_CONTEXT.get())
    env_flag = bool(os.environ.get(DELEGATED_CHILD_ENV_MARKER))
    if env_flag and not ctx_flag:
        _warn_marker_without_context_once()
    return ctx_flag or env_flag


_MARKER_MISMATCH_WARNED = False


def _warn_marker_without_context_once() -> None:
    """Log the env-marker-without-ContextVar state once per process."""
    global _MARKER_MISMATCH_WARNED
    if _MARKER_MISMATCH_WARNED:
        return
    _MARKER_MISMATCH_WARNED = True
    try:
        import logging
        import os

        logging.getLogger(__name__).debug(
            "%s is set in os.environ but no delegated-child ContextVar is "
            "active (pid=%s). Expected for a subprocess spawned by a "
            "delegate_task child; in a long-lived gateway process it means the "
            "marker was inherited or latched, and every Kanban mutation will "
            "be refused. Check the bash session snapshot "
            "(/tmp/hermes-snap-*.sh) and the parent process environment.",
            DELEGATED_CHILD_ENV_MARKER,
            os.getpid(),
        )
    except Exception:
        pass


def scrub_kanban_env(env: Mapping[str, str] | MutableMapping[str, str]) -> dict[str, str]:
    """Return *env* with dispatcher-only Kanban variables removed.

    Strips by ``KANBAN_ENV_PREFIX`` rather than by the enumerated
    ``KANBAN_ENV_KEYS``. The dispatcher's worker-env builder is the growing
    side of this contract; an allowlist here means every variable it adds
    leaks into delegated children until someone edits this file. Nothing
    outside the dispatcher sets ``HERMES_KANBAN_*``, so a prefix strip has no
    false positives.
    """
    cleaned = {
        key: value
        for key, value in dict(env).items()
        if not key.startswith(KANBAN_ENV_PREFIX)
    }
    cleaned[DELEGATED_CHILD_ENV_MARKER] = "1"
    return cleaned


def delegated_child_subprocess_env(
    env: Mapping[str, str] | MutableMapping[str, str] | None = None,
) -> dict[str, str] | None:
    """Return an env override only when delegated-child lineage must cross fork.

    Most subprocess call sites historically used ``env=None`` to inherit the
    process environment.  In a ``delegate_task`` child, inheriting as-is leaks
    parent dispatcher ``HERMES_KANBAN_*`` vars while losing the ContextVar in
    the new process.  This helper preserves normal ``env=None`` semantics for
    non-delegated calls, and only materializes a scrubbed env when the lineage
    marker must be propagated across a child-process boundary.
    """
    if not is_delegated_child_process_context():
        return None if env is None else dict(env)

    if env is None:
        import os

        env = os.environ
    return scrub_kanban_env(env)
