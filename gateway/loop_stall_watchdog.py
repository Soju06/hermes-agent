"""Loop-stall watchdog: diagnostic logging for event-loop freezes (ERROR + loop-thread stack + PSI).

Complements ``gateway/shutdown_watchdog.py``'s liveness watchdog, which only acts (dump + hard exit)
after ~90-120s of sustained block; this one reports every stall of a few seconds and its recovery,
so short freezes that never trip a restart still leave the frame that held the loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

# Log-record parity with the origin module.
logger = logging.getLogger("gateway.run")


def _run_loop_stall_watchdog(
    stop_event: threading.Event,
    loop: asyncio.AbstractEventLoop,
    *,
    ping_interval: float = 2.0,
    stall_threshold: float = 5.0,
) -> None:
    """Detect and loudly report event-loop stalls.

    Pings the loop via ``call_soon_threadsafe`` and measures how long the
    callback takes to run. 2026-07-28: the loop blocked for 160s on the
    SessionDB writer lock and the only signal was discord.py's heartbeat
    warning — invisible when Discord isn't connected. This watchdog is
    transport-independent: on a stall it logs ERROR with the MainThread
    stack (the exact frame holding the loop) and a cgroup PSI snapshot so
    host CPU/memory starvation is distinguishable from an in-process
    block. Alarms escalate (5s, then doubling) and recovery logs the
    total stall duration.
    """
    # The loop normally runs on MainThread; record the actual ident from
    # inside a pong so the dump targets the right thread even when the
    # loop was started elsewhere (tests, embedders).
    loop_ident = [threading.main_thread().ident]

    def _loop_stack() -> str:
        try:
            frame = sys._current_frames().get(loop_ident[0])
            if frame is None:
                return "<loop thread frame unavailable>"
            return "".join(traceback.format_stack(frame))
        except Exception as exc:  # pragma: no cover - diagnostics only
            return f"<stack capture failed: {exc}>"

    def _psi_snapshot() -> str:
        try:
            cg = ""
            with open("/proc/self/cgroup", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("0::"):
                        cg = line.strip().split("::", 1)[1]
                        break
            parts = []
            for kind in ("cpu", "memory"):
                try:
                    with open(f"/sys/fs/cgroup{cg}/{kind}.pressure", encoding="utf-8") as fh:
                        parts.append(f"{kind} {fh.readline().strip()}")
                except OSError:
                    continue
            return "; ".join(parts) or "<unavailable>"
        except Exception:  # pragma: no cover - diagnostics only
            return "<unavailable>"

    while not stop_event.wait(ping_interval):
        pong = threading.Event()
        t0 = time.monotonic()

        def _pong(_evt=pong):
            loop_ident[0] = threading.get_ident()
            _evt.set()

        try:
            loop.call_soon_threadsafe(_pong)
        except RuntimeError:
            return  # loop closed — shutdown race
        next_alarm = stall_threshold
        alarmed = False
        while not pong.wait(0.25):
            if stop_event.is_set():
                return
            lag = time.monotonic() - t0
            if lag >= next_alarm:
                alarmed = True
                logger.error(
                    "event loop blocked for %.1fs — loop thread stack:\n%s\nPSI: %s",
                    lag, _loop_stack(), _psi_snapshot(),
                )
                next_alarm = max(next_alarm * 2, lag + stall_threshold)
        if alarmed:
            logger.warning(
                "event loop recovered after %.1fs stall",
                time.monotonic() - t0,
            )
