"""Durable scope cleanup reaches the user manager the launch reached (terminal.durable_background).

A system-level gateway unit does not inherit XDG_RUNTIME_DIR or the bus address, so the launch
passes systemd_user_bus_env(); the stop of a scope that never got ready and the reset-failed of a
finished scope must use the same env, or they fail silently on exactly that host.
"""

import subprocess
from unittest.mock import MagicMock, patch

from tools.process_registry import ProcessRegistry, ProcessSession


def test_failed_scope_is_stopped_through_the_user_bus_helper():
    proc = MagicMock(pid=7001)
    proc.poll.return_value = None
    with patch("tools.process_registry._stop_systemd_unit", return_value=True) as stop_unit, \
         patch.object(ProcessRegistry, "_terminate_host_pid") as terminate, \
         patch.object(ProcessRegistry, "_safe_host_start_time", return_value=None):
        ProcessRegistry._discard_failed_scope(proc, "hermes-bg-proc_gone")
    stop_unit.assert_called_once_with("hermes-bg-proc_gone.scope")
    terminate.assert_called_once_with(7001, None)


def test_reset_failed_runs_with_the_user_bus_env(monkeypatch):
    monkeypatch.setattr("hermes_cli.process_identity._update_supervised_pgids", lambda verb, pgids: None)
    captured = []
    bus_env = {"XDG_RUNTIME_DIR": "/run/user/1000", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
    monkeypatch.setattr("tools.process_registry.systemd_user_bus_env", lambda base_env=None: dict(bus_env))
    monkeypatch.setattr("tools.process_registry_durable.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "tools.process_registry_durable.locate_command", lambda name: MagicMock(command=[f"/usr/bin/{name}"]),
    )
    monkeypatch.setattr(
        "tools.process_registry_durable.subprocess.run", lambda argv, **kwargs: captured.append((argv, kwargs)),
    )
    session = ProcessSession(id="proc_reset", command="sleep 1", task_id="t1", durable=True)
    ProcessRegistry()._reset_failed_durable_scope(session)
    assert captured == [(
        ["/usr/bin/systemctl", "--user", "reset-failed", "hermes-bg-proc_reset.scope"],
        {"check": False, "env": bus_env, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
         "stderr": subprocess.DEVNULL, "timeout": 5},
    )]
