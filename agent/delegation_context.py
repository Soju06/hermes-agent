"""Context-local state for delegate_task child execution.

A Hermes process may itself be a Kanban dispatcher worker with HERMES_KANBAN_* in
os.environ. In-process delegate_task children and cron jobs fired via
``cronjob(action="run")`` are NOT dispatcher-owned, so identity gates must fail
closed for them without mutating the process-global environment.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import Context, ContextVar, Token
from typing import Iterator, Mapping, MutableMapping, overload

_DELEGATED_CHILD_CONTEXT: ContextVar[bool] = ContextVar("hermes_delegated_child_context", default=False)
# Any in-process execution that is NOT the dispatcher-owned worker (cron jobs). Kept separate
# so delegate_task-specific behaviour (subprocess env scrubbing, its error strings) is unchanged.
_NON_DISPATCHER_OWNED_CONTEXT: ContextVar[bool] = ContextVar("hermes_non_dispatcher_owned_context", default=False)

DELEGATED_CHILD_ENV_MARKER = "HERMES_DELEGATED_CHILD_CONTEXT"

KANBAN_ENV_PREFIX = "HERMES_KANBAN_"

# Worker *identity* variables. Enumerated for documentation, tests and the
# codex app-server scope grant only. Do NOT use this tuple to decide what to
# strip: the dispatcher (``hermes_cli/kanban_db_dispatch.py``) grows its worker
# env over time (HERMES_KANBAN_BRANCH, _GOAL_MODE, _GOAL_MAX_TURNS were all
# added after the original list was written and silently escaped the scrub for
# that whole window). ``scrub_kanban_env`` strips by prefix so a new dispatcher
# variable is covered on the day it is introduced instead of the day someone
# notices.
KANBAN_ENV_KEYS: tuple[str, ...] = (
    "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_CLAIM_LOCK",
    "HERMES_KANBAN_GOAL_MODE", "HERMES_KANBAN_GOAL_MAX_TURNS",
)

# Board *location/routing* variables a descendant keeps (upstream contract since
# "keep Kanban worker scope out of descendant processes": scrubbing removes who
# the worker IS, not which board/workspace it works on). Everything else under
# ``KANBAN_ENV_PREFIX`` is treated as identity and stripped. ``HERMES_KANBAN_HOME``
# and ``HERMES_KANBAN_ATTACHMENTS_ROOT`` are operator location overrides read by
# ``hermes_cli.kanban_db.kanban_home`` / the attachments root, not dispatcher identity.
KANBAN_LOCATION_ENV_KEYS: frozenset[str] = frozenset({
    "HERMES_KANBAN_BOARD", "HERMES_KANBAN_DB", "HERMES_KANBAN_WORKSPACES_ROOT",
    "HERMES_KANBAN_WORKSPACE", "HERMES_KANBAN_BRANCH",
    "HERMES_KANBAN_HOME", "HERMES_KANBAN_ATTACHMENTS_ROOT",
})


@contextmanager
def delegated_child_context(session_id: str | None = None) -> Iterator[None]:
    """Mark child execution and isolate its task-local session identity. Even a context
    entered without an id must restore the parent's session ContextVar (child
    construction calls ``set_current_session_id``)."""
    token = _DELEGATED_CHILD_CONTEXT.set(True)
    try:
        from gateway.session_context import scoped_current_session_id  # lazy: it calls is_delegated_child_context()

        with scoped_current_session_id(session_id):
            yield
    finally:
        _DELEGATED_CHILD_CONTEXT.reset(token)


def is_delegated_child_context() -> bool:
    """Return True while code is running for a delegate_task child."""
    return bool(_DELEGATED_CHILD_CONTEXT.get())


def enter_non_dispatcher_owned_context() -> Token[bool]:
    """Token form of :func:`non_dispatcher_owned_context` for long try/finally scopes."""
    return _NON_DISPATCHER_OWNED_CONTEXT.set(True)


def exit_non_dispatcher_owned_context(token: Token[bool]) -> None:
    """Restore the flag saved by :func:`enter_non_dispatcher_owned_context`."""
    _NON_DISPATCHER_OWNED_CONTEXT.reset(token)


@contextmanager
def non_dispatcher_owned_context() -> Iterator[None]:
    """Mark in-process execution that does NOT own the dispatcher's Kanban task; without it
    a cron agent run inside a worker is misread as that worker (kanban toolset force-added,
    ``kanban_complete`` defaulting to its task). ContextVar-scoped rather than clearing
    os.environ, which the worker's claim heartbeat and concurrent readers share."""
    token = enter_non_dispatcher_owned_context()
    try:
        yield
    finally:
        exit_non_dispatcher_owned_context(token)


def is_dispatcher_owned_worker_context() -> bool:
    """The single predicate every ``HERMES_KANBAN_*`` identity gate should use."""
    return not (is_delegated_child_process_context() or _NON_DISPATCHER_OWNED_CONTEXT.get())


def explicit_board_intent_is_pinned() -> bool:
    """Whether an explicit kanban ``board=`` argument must still resolve through
    the dispatcher-injected env pins (``HERMES_KANBAN_DB`` & friends) rather than
    its own board directory.

    True for in-process delegate children, descendants carrying
    :data:`DELEGATED_CHILD_ENV_MARKER`, and dispatched workers
    (``HERMES_KANBAN_TASK`` set). The pins are the "workers physically cannot
    see other boards" isolation, and :func:`kanban_path_is_fenced` checks the
    pinned path / fenced root — an explicit board that resolved elsewhere would
    also escape that fence. Outside these fences an explicit board is the
    caller's own intent and wins.
    """
    if _DELEGATED_CHILD_CONTEXT.get():
        return True
    if os.environ.get(DELEGATED_CHILD_ENV_MARKER):
        return True
    return bool((os.environ.get("HERMES_KANBAN_TASK") or "").strip())


def owned_kanban_task() -> str:
    """The board task this execution OWNS: ``HERMES_KANBAN_TASK`` for the dispatcher-owned
    worker, ``""`` otherwise. Tool access is not worker identity — a profile can expose the
    kanban toolset interactively, and children/cron runs inherit the env var — so every
    reader that turns the task id into worker behaviour (guidance, stop nudge, terminal
    outcomes) goes through this one helper."""
    if not is_dispatcher_owned_worker_context():
        return ""
    return (os.environ.get("HERMES_KANBAN_TASK") or "").strip()


def reset_delegated_child_context_in(ctx: Context) -> None:
    """Clear the delegated-child marker inside a *copied* Context.

    ``contextvars.Context`` cannot be written from the outside, but a value set
    while running inside it persists in that object — so this is the supported
    way to hand a worker thread a context that is otherwise identical to its
    parent's but is NOT flagged as delegated-child. Used by
    ``tools.thread_context.propagate_context_to_thread``: the marker is a scope
    around one synchronous child run, not an inheritable identity, and detached
    daemon threads (ingest-curator, bg-review, async delegation) outlive that
    scope. In-turn workers such as the execute_code RPC loops keep the marker.
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


def _fenced_kanban_root() -> str:
    """The board root this process's Kanban lineage lives under (``kanban_home()``); ``"1"`` when it
    cannot be resolved, which readers treat as "fence every board" (the pre-path marker)."""
    try:
        from hermes_cli.kanban_db import kanban_home
        return str(kanban_home())
    except Exception:
        return "1"


def scrub_kanban_env(env: Mapping[str, str] | MutableMapping[str, str]) -> dict[str, str]:
    """Remove worker identity, retaining board/location and an inherited write fence.

    TASK absence alone would promote a descendant to an orchestrator. The marker
    survives later execs, including scripts that remove TASK themselves. This is
    cooperative runtime scoping, not confinement of code with direct SQLite access.

    The marker's value is the fenced board ROOT, so the fence applies to the lineage's
    board and not to every Kanban DB the descendant touches: a child running a repro
    against a temp ``HERMES_HOME`` got a silently read-only board there. An inherited
    path-valued marker is kept (a grandchild that moved HERMES_HOME must not re-fence
    onto its scratch root and unfence the real one).

    Strips by ``KANBAN_ENV_PREFIX`` (minus ``KANBAN_LOCATION_ENV_KEYS``) rather
    than by the enumerated ``KANBAN_ENV_KEYS``. The dispatcher's worker-env
    builder is the growing side of this contract; an allowlist here means every
    identity variable it adds leaks into delegated children until someone edits
    this file. Board routing and location overrides are kept by name.
    """
    cleaned = {
        k: v for k, v in env.items()
        if not k.startswith(KANBAN_ENV_PREFIX) or k in KANBAN_LOCATION_ENV_KEYS
    }
    # Only the gateway mints ``kanban`` sessions: a descendant ``hermes chat`` sending it is refused.
    if cleaned.get("HERMES_SESSION_SOURCE") == "kanban":
        del cleaned["HERMES_SESSION_SOURCE"]
    inherited = str(env.get(DELEGATED_CHILD_ENV_MARKER) or "")
    cleaned[DELEGATED_CHILD_ENV_MARKER] = inherited if inherited and inherited != "1" else _fenced_kanban_root()
    return cleaned


def kanban_path_is_fenced(path: os.PathLike[str] | str) -> bool:
    """Whether Kanban mutations at *path* (a board DB or board-metadata root) are denied for this
    process: always for an in-process delegate child (the parent's own board); for a spawned
    descendant only when *path* is the dispatcher-pinned ``HERMES_KANBAN_DB`` or lies under the
    fenced root the marker carries. A legacy ``"1"`` marker fences everything."""
    if _DELEGATED_CHILD_CONTEXT.get():
        return True
    marker = os.environ.get(DELEGATED_CHILD_ENV_MARKER, "")
    if not marker:
        return False
    if marker == "1":
        return True
    from pathlib import Path
    target = Path(path).expanduser().resolve()
    pinned = os.environ.get("HERMES_KANBAN_DB", "").strip()
    if pinned and target == Path(pinned).expanduser().resolve():
        return True
    try:
        target.relative_to(Path(marker).expanduser().resolve())
    except ValueError:
        return False
    return True


@overload
def delegated_child_subprocess_env(env: Mapping[str, str]) -> dict[str, str]: ...


@overload
def delegated_child_subprocess_env(env: None = None) -> dict[str, str] | None: ...


def delegated_child_subprocess_env(
    env: Mapping[str, str] | MutableMapping[str, str] | None = None,
) -> dict[str, str] | None:
    """Carry worker/delegate descendant denial across a real process spawn.

    Location and credentials are untouched; callers retain their existing secret policy.
    Dispatcher workers and supervised tool transports grant their own explicit scope.
    """
    if not (is_delegated_child_process_context() or os.environ.get("HERMES_KANBAN_TASK")
            or (env and (env.get("HERMES_KANBAN_TASK") or env.get(DELEGATED_CHILD_ENV_MARKER)))):
        return None if env is None else dict(env)
    return scrub_kanban_env(os.environ if env is None else env)
