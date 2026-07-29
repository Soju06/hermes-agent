"""CPU niceness for terminal-tool children: an argv prefix through nice(1).

The gateway and every process its terminal tool spawns share ONE systemd service cgroup, so cgroup
CPU weights cannot protect the event loop from its own children. Used by the foreground spawn in
``tools.environments.local`` and the background/PTY spawns in ``tools.process_registry``.
"""

import os
import platform

from hermes_platform.resolver import locate_command

_IS_WINDOWS = platform.system() == "Windows"


def _terminal_child_nice() -> int:
    """Niceness for spawned terminal children (0 disables).

    The gateway and every process its terminal tool spawns share ONE
    systemd service cgroup, so cgroup CPU weights cannot protect the
    event loop from its own children — within a cgroup scheduling is
    flat CFS. 2026-07-28: an agent-spawned ~20-process codex review
    fleet starved the gateway's threads inside its own service and
    contributed to a 160s event-loop stall. Plain nice(1) still applies
    within a cgroup: +10 keeps the gateway interactive while batch
    children soak the idle cores (they lose nothing on an idle host).
    Override with HERMES_TERMINAL_CHILD_NICE; 0 turns the prefix off.
    """
    try:
        return max(0, min(19, int(os.environ.get("HERMES_TERMINAL_CHILD_NICE", "10"))))
    except (TypeError, ValueError):
        return 10


def _nice_argv(argv: list) -> list:
    """Prefix *argv* with nice(1) at the terminal-child niceness.

    An argv prefix instead of a pre-exec hook: nice(1) simply execs the
    real command (same PID, same process group, ``start_new_session``
    unaffected), whereas a Python-level fork/exec hook runs between fork
    and exec in a heavily threaded parent — CPython documents that as
    deadlock-prone. Works identically for ``subprocess.Popen`` and
    ``PtyProcess.spawn``.
    """
    if _IS_WINDOWS:
        return argv
    nice = _terminal_child_nice()
    if nice <= 0:
        return argv
    nice_bin = locate_command("nice").command
    if not nice_bin:
        return argv
    return [nice_bin[0], "-n", str(nice), *argv]
