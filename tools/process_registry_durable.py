"""Durable local background processes (``terminal.durable_background``, opt-in).

An opted-in non-PTY local background command runs in its own ``hermes-bg-<id>`` transient
user scope and writes stdout+stderr to a private log (``<HERMES_HOME>/bg-logs/<id>.log``), so
it survives a gateway service restart: the shutdown sweep skips it
(``kill_all(exclude_durable=True)``), so does the sweep after a forced update kill
(``hermes_cli/update_cmd_force_stop.py``), and checkpoint recovery re-attaches its log. Only a
host whose jobs outlive it (a gateway, see ``ProcessOwnershipMixin._owns_job``) spawns durably; a
host that owns its jobs keeps them owned. Without a usable systemd user manager the spawn falls
back to the ordinary local path.
"""

import codecs
import logging
import os
import platform
import shlex
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from agent.memory_provider import spawn_context_thread
from hermes_constants import get_hermes_home
from hermes_platform.resolver import locate_command

# Log-record parity with the registry these methods are bound onto.
logger = logging.getLogger("tools.process_registry")

_DURABLE_WARNING_LOCK = threading.Lock()
_DURABLE_WARNING_EMITTED = False


class ProcessDurableMixin:
    """The subclass supplies ``_lock``, ``_running``, ``_check_watch_patterns``, ``_emit_output``,
    ``_move_to_finished``, ``_host_pid_is_ours``, ``_safe_host_start_time`` and
    ``_terminate_host_pid``. See ProcessRegistry."""

    @staticmethod
    def _durable_background_enabled() -> bool:
        """Return the resolved terminal.durable_background setting."""
        try:
            from hermes_cli.config import load_config_readonly
            cfg = load_config_readonly()
            terminal_cfg = cfg.get("terminal", {}) if isinstance(cfg, dict) else {}
            return bool(terminal_cfg.get("durable_background", False))
        except Exception:
            return False

    @staticmethod
    def _warn_durable_fallback_once(reason: str) -> None:
        """Warn once per process when durable spawning must degrade gracefully."""
        global _DURABLE_WARNING_EMITTED
        with _DURABLE_WARNING_LOCK:
            if _DURABLE_WARNING_EMITTED:
                return
            _DURABLE_WARNING_EMITTED = True
        logger.warning(
            "Durable background processes are enabled but unavailable (%s); "
            "falling back to ordinary local background spawning.",
            reason,
        )

    @classmethod
    def _durable_scope_launcher(cls) -> Optional[str]:
        """Resolve systemd-run when transient user scopes are usable."""
        if platform.system() != "Linux":
            cls._warn_durable_fallback_once("platform is not Linux")
            return None
        found = locate_command("systemd-run").command
        runner = found[0] if found else None
        if not runner:
            cls._warn_durable_fallback_once("systemd-run was not found")
            return None
        try:
            if not Path("/run/systemd/system").is_dir():
                raise RuntimeError("systemd runtime directory is absent")
            init_name = Path("/proc/1/comm").read_text(encoding="utf-8").strip()
            if init_name != "systemd":
                raise RuntimeError(f"PID 1 is {init_name or 'unknown'}, not systemd")
        except Exception as exc:
            cls._warn_durable_fallback_once(str(exc))
            return None
        return runner

    @staticmethod
    def _durable_scope_unit(session_id: str) -> str:
        return f"hermes-bg-{session_id}"

    @staticmethod
    def _prepare_durable_log(session_id: str) -> str:
        """Create a private durable-output file and return its path."""
        log_dir = get_hermes_home() / "bg-logs"
        log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(log_dir, 0o700)
        log_path = log_dir / f"{session_id}.log"
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        return str(log_path)

    @staticmethod
    def _await_scope_ready(
        proc: subprocess.Popen,
        marker: str,
        timeout: float = 2.0,
    ) -> tuple[bool, str]:
        """Wait for the payload shell marker or a systemd-run launch error."""
        stdout = proc.stdout
        if stdout is None:
            return False, "scope launcher did not expose its startup pipe"

        import select

        try:
            fd = stdout.fileno()
        except (AttributeError, OSError, ValueError) as exc:
            return False, f"scope readiness pipe is unavailable: {exc}"

        output = bytearray()
        marker_bytes = marker.encode("utf-8")
        deadline = time.monotonic() + timeout
        for _ in range(100):
            remaining = deadline - time.monotonic()
            try:
                ready, _, _ = select.select(
                    [fd], [], [], max(0.0, remaining)
                )
            except (OSError, TypeError, ValueError) as exc:
                return False, f"scope readiness check failed: {exc}"
            if not ready:
                break
            try:
                chunk = os.read(fd, 4096)
            except OSError as exc:
                return False, f"scope readiness read failed: {exc}"
            if not chunk:
                break
            output.extend(chunk)
            if marker_bytes in {
                line.rstrip(b"\r") for line in bytes(output).split(b"\n")
            }:
                return True, ""
            if remaining <= 0:
                # Drain only already-buffered lines after the deadline. This
                # still recognizes a marker written while the parent process
                # was descheduled, without extending the launch timeout.
                continue
        detail = bytes(output).decode("utf-8", errors="replace").strip()
        detail = "; ".join(part.strip() for part in detail.splitlines() if part.strip())
        if not detail:
            rc = proc.poll()
            detail = (
                f"systemd-run exited with status {rc}"
                if rc is not None
                else "timed out waiting for the scope payload"
            )
        return False, detail

    @classmethod
    def _discard_failed_scope(
        cls,
        proc: subprocess.Popen,
        scope_unit: str,
    ) -> None:
        """Best-effort cleanup for a scope launcher that never reached payload.

        The stop goes through upstream's helper, which passes the same user-bus
        env the launch needed: a system-level gateway unit does not inherit
        XDG_RUNTIME_DIR or the bus address, so a bare ``systemctl --user`` fails.
        """
        from tools.process_registry import _stop_systemd_unit

        _stop_systemd_unit(f"{scope_unit}.scope")
        try:
            cls._terminate_host_pid(
                proc.pid,
                cls._safe_host_start_time(proc.pid),
            )
        except Exception:
            pass
        try:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass

    def _spawn_durable_local(
        self, session, safe_command: str, spawn_env: dict, popen_common: dict,
    ) -> Optional[subprocess.Popen]:
        """Launch *safe_command* in a sibling ``hermes-bg-<id>`` transient user scope with
        file-backed output so it survives a gateway service restart. Returns the launcher
        Popen once the payload shell reported ready, or None when the scope is unavailable /
        failed (the caller falls back to ordinary local spawning)."""
        from tools.process_registry import _find_shell, _nice_argv, systemd_user_bus_env

        scope_launcher = self._durable_scope_launcher()
        if not scope_launcher:
            return None
        proc = None
        log_path = None
        candidate = None
        scope_unit = self._durable_scope_unit(session.id)
        try:
            log_path = self._prepare_durable_log(session.id)
            marker = f"__HERMES_SCOPE_READY_{uuid.uuid4().hex}__"
            durable_shell_command = (
                f"printf '%s\\n' {shlex.quote(marker)}; "
                f"exec >> {shlex.quote(log_path)} 2>&1; "
                f"set +m; {safe_command}"
            )
            durable_argv = [
                scope_launcher,
                "--user",
                "--scope",
                "--quiet",
                f"--unit={scope_unit}",
                "--",
                *_nice_argv([_find_shell(), "-lic", durable_shell_command]),
            ]
            candidate = subprocess.Popen(
                durable_argv, env=systemd_user_bus_env(spawn_env), stdin=subprocess.DEVNULL, **popen_common,
            )
            ready, failure = self._await_scope_ready(candidate, marker)
            if ready:
                proc = candidate
                session.durable = True
                session.log_path = log_path
                session.systemd_unit = f"{scope_unit}.scope"
                try:
                    if proc.stdout is not None:
                        proc.stdout.close()
                except Exception:
                    pass
            else:
                self._discard_failed_scope(candidate, scope_unit)
                self._warn_durable_fallback_once(failure)
        except Exception as exc:
            if candidate is not None:
                self._discard_failed_scope(candidate, scope_unit)
            self._warn_durable_fallback_once(f"scope launch failed: {exc}")

        if proc is None and log_path:
            try:
                Path(log_path).unlink(missing_ok=True)
            except Exception:
                pass
        return proc

    def _resume_recovered_durable_session(self, session) -> None:
        """Re-attach a checkpoint-recovered durable session to its log: refresh the
        in-memory tail and start the file-tailing monitor thread."""
        if not session.log_path:
            session.log_path = str(get_hermes_home() / "bg-logs" / f"{session.id}.log")
        self._read_durable_output(session)
        try:
            # The reader finishes the session (checkpoint, completed result) in the recovering
            # profile's home, so it keeps the recovering caller's profile scope.
            reader = spawn_context_thread(
                self._recovered_durable_reader_loop,
                args=(session,),
                name=f"proc-recovered-reader-{session.id}",
            )
            session._reader_thread = reader
            reader.start()
        except Exception as exc:
            logger.warning(
                "Could not resume durable log monitoring for %s: %s",
                session.id,
                exc,
            )

    def _read_durable_output(self, session) -> str:
        """Read the durable log and refresh the rolling in-memory tail."""
        if not session.durable or not session.log_path:
            with session._lock:
                return session.output_buffer
        try:
            full_output = Path(session.log_path).read_text(
                encoding="utf-8", errors="replace"
            )
        except (FileNotFoundError, OSError):
            with session._lock:
                return session.output_buffer
        with session._lock:
            session.output_buffer = full_output[-session.max_output_chars:]
            session._durable_sync_generation += 1
        return full_output

    def _record_durable_chunk(
        self,
        session,
        chunk: str,
        expected_generation: int,
    ) -> int:
        """Append one tailed chunk unless a file-source refresh won the race."""
        needs_resync = False
        with session._lock:
            if session._durable_sync_generation != expected_generation:
                needs_resync = True
            else:
                session._append_locked(chunk)
            generation = session._durable_sync_generation
        if needs_resync:
            self._read_durable_output(session)
            with session._lock:
                generation = session._durable_sync_generation
        self._check_watch_patterns(session, chunk)
        self._emit_output(session, chunk)
        return generation

    def _durable_reader_loop(self, session) -> None:
        """Tail a durable log while retaining the Popen completion behavior."""
        proc = session.process
        log_path = session.log_path
        if proc is None or not log_path:
            return

        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        byte_offset = 0
        with session._lock:
            sync_generation = session._durable_sync_generation

        def _drain_file() -> None:
            nonlocal byte_offset, sync_generation
            try:
                size = os.path.getsize(log_path)
                if size < byte_offset:
                    byte_offset = 0
                with open(log_path, "rb") as stream:
                    stream.seek(byte_offset)
                    raw = stream.read()
                if not raw:
                    return
                byte_offset += len(raw)
                chunk = decoder.decode(raw)
                if not chunk:
                    return
                sync_generation = self._record_durable_chunk(
                    session, chunk, sync_generation
                )
            except (FileNotFoundError, OSError):
                return

        try:
            while not session.exited and proc.poll() is None:
                _drain_file()
                time.sleep(0.2)
            _drain_file()
            tail = decoder.decode(b"", final=True)
            if tail:
                sync_generation = self._record_durable_chunk(
                    session, tail, sync_generation
                )
            try:
                proc.wait(timeout=5)
            except Exception as exc:
                logger.debug("Durable process wait timed out or failed: %s", exc)
            with session._lock:
                session.mark_exited(proc.returncode)
        finally:
            self._move_to_finished(session)

    def _recovered_durable_reader_loop(self, session) -> None:
        """Tail and monitor a durable PID re-adopted from a checkpoint."""
        log_path = session.log_path
        if not log_path:
            return

        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            byte_offset = os.path.getsize(log_path)
        except OSError:
            byte_offset = 0
        with session._lock:
            sync_generation = session._durable_sync_generation

        def _drain_file() -> None:
            nonlocal byte_offset, sync_generation
            try:
                size = os.path.getsize(log_path)
                if size < byte_offset:
                    byte_offset = 0
                with open(log_path, "rb") as stream:
                    stream.seek(byte_offset)
                    raw = stream.read()
                if not raw:
                    return
                byte_offset += len(raw)
                chunk = decoder.decode(raw)
                if chunk:
                    sync_generation = self._record_durable_chunk(
                        session, chunk, sync_generation
                    )
            except (FileNotFoundError, OSError):
                return

        try:
            while (
                not session.exited
                and self._host_pid_is_ours(session.pid, session.host_start_time)
            ):
                _drain_file()
                time.sleep(0.2)
            _drain_file()
            tail = decoder.decode(b"", final=True)
            if tail:
                sync_generation = self._record_durable_chunk(
                    session, tail, sync_generation
                )
            # No waitable handle survives recovery, so the real exit code is unknown.
            with session._lock:
                session.mark_exited(None)
        finally:
            self._move_to_finished(session)

    def _reset_failed_durable_scope(self, session) -> None:
        """Clear a finished durable scope's failed state so its unit name can be reused.

        Runs with the launch's user-bus env (see :meth:`_discard_failed_scope`).
        """
        if not session.durable or platform.system() != "Linux":
            return
        from tools.process_registry import systemd_user_bus_env

        found = locate_command("systemctl").command
        if not found:
            return
        try:
            subprocess.run(
                [
                    found[0],
                    "--user",
                    "reset-failed",
                    f"{self._durable_scope_unit(session.id)}.scope",
                ],
                check=False,
                env=systemd_user_bus_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception:
            pass

    def count_durable_running(self) -> int:
        """Return the number of live sessions protected from gateway sweeps."""
        with self._lock:
            return sum(1 for s in self._running.values() if s.durable and not s.exited)
