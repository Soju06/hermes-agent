"""Loop-stall watchdog: platform-independent event-loop freeze detection.

2026-07-28 outage: the loop blocked 160s on the SessionDB writer lock and
the only diagnostic was discord.py's heartbeat warning (absent when Discord
isn't connected). The watchdog pings the loop from a thread and logs ERROR
with the MainThread stack + PSI snapshot when the pong lags.
"""

import asyncio
import threading
import time

from gateway.loop_stall_watchdog import _run_loop_stall_watchdog


def _spin_loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def _teardown_loop(loop, thread):
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=3)
    loop.close()


def _watch_pongs(loop, count=1):
    """An Event set once ``count`` callbacks scheduled through ``call_soon_threadsafe`` (the watchdog's
    pings) have run on the loop, so a test waits for the watchdog's progress instead of the clock."""
    reached = threading.Event()
    done = [0]
    schedule = loop.call_soon_threadsafe

    def _tracking(callback, *args, **kwargs):
        def _pong_then_count(*cb_args):
            callback(*cb_args)
            done[0] += 1
            if done[0] >= count:
                reached.set()

        return schedule(_pong_then_count, *args, **kwargs)

    loop.call_soon_threadsafe = _tracking
    return reached


def test_stall_logs_error_and_recovery(caplog):
    loop, loop_thread = _spin_loop()
    stop = threading.Event()
    watchdog = threading.Thread(
        target=_run_loop_stall_watchdog,
        args=(stop, loop),
        kwargs={"ping_interval": 0.05, "stall_threshold": 0.2},
        daemon=True,
    )
    first_pong = _watch_pongs(loop)
    try:
        with caplog.at_level("WARNING", logger="gateway.run"):
            watchdog.start()
            # Let at least one clean ping/pong land so the watchdog has
            # recorded the loop thread's ident (mirrors production, where
            # the loop is healthy at startup).
            assert first_pong.wait(5.0), "the watchdog never pinged the loop"
            # Freeze the loop well past the threshold.
            loop.call_soon_threadsafe(time.sleep, 0.8)
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                if any(
                    "event loop recovered" in r.getMessage()
                    for r in caplog.records
                ):
                    break
                time.sleep(0.05)
        errors = [
            r for r in caplog.records
            if r.levelname == "ERROR" and "event loop blocked" in r.getMessage()
        ]
        assert errors, "expected an ERROR for the stalled loop"
        # The dump must be a real stack of the LOOP thread: time.sleep is a
        # builtin (no Python frame), so the deepest visible frames are the
        # loop machinery executing the blocking callback.
        assert any(
            "_run_once" in r.getMessage() or "run_forever" in r.getMessage()
            for r in errors
        ), "expected the loop thread's stack in the error dump"
        assert any(
            "event loop recovered" in r.getMessage() for r in caplog.records
        ), "expected a recovery log after the stall cleared"
    finally:
        stop.set()
        watchdog.join(timeout=3)
        _teardown_loop(loop, loop_thread)


def test_quiet_loop_stays_silent(caplog):
    loop, loop_thread = _spin_loop()
    stop = threading.Event()
    watchdog = threading.Thread(
        target=_run_loop_stall_watchdog,
        args=(stop, loop),
        kwargs={"ping_interval": 0.05, "stall_threshold": 0.5},
        daemon=True,
    )
    # Twelve pings at 0.05s cover more than the 0.5s threshold of a healthy loop.
    pings_landed = _watch_pongs(loop, count=12)
    try:
        with caplog.at_level("WARNING", logger="gateway.run"):
            watchdog.start()
            assert pings_landed.wait(10.0), "the watchdog stopped pinging the loop"
        assert not [
            r for r in caplog.records if "event loop" in r.getMessage()
        ]
    finally:
        stop.set()
        watchdog.join(timeout=3)
        _teardown_loop(loop, loop_thread)
