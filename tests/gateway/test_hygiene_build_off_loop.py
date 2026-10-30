"""The hygiene AIAgent is built off the event loop and stays visible to shutdown while it builds.

``AIAgent.__init__`` binds session state with synchronous store reads (the writer lock on SQLite); on the loop
a writer-lock convoy froze every platform heartbeat (2026-07-28 outage), so the build runs on the
loop's default executor. That executor is outside ``self._executor``'s quiesce, so the build is
registered with shutdown at submit time like the compression worker (upstream 0c84aff676): stop()
must not close/checkpoint state.db under its reads. A turn cancelled mid-build must still release
the agent the build returns, because the detached attempt's cleanup never receives it.
"""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import gateway.run as gw_run
import run_agent
from tests.gateway.restart_test_helpers import make_restart_runner
from tests.waiting import async_wait_until


def _install_blocking_agent(monkeypatch):
    """Patch AIAgent with a stand-in whose construction blocks until the test releases it."""
    started = threading.Event()
    release = threading.Event()
    built = []

    class _Compressor:
        bound = None

        def bind_session_state(self, db, session_id):
            self.bound = (db, session_id)

    class _BlockingAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.init_thread = threading.get_ident()
            self.context_compressor = _Compressor()
            built.append(self)
            started.set()
            if not release.wait(30.0):
                raise RuntimeError("the test never released the hygiene build")

    monkeypatch.setattr(run_agent, "AIAgent", _BlockingAgent)
    monkeypatch.setattr(gw_run, "_seed_hygiene_system_prompt", lambda agent, row: True)
    return started, release, built


def _runner(monkeypatch):
    runner, _adapter = make_restart_runner()
    handle = SimpleNamespace(aio=SimpleNamespace(sessions=SimpleNamespace(get=AsyncMock(return_value=None))))
    monkeypatch.setattr(gw_run.GatewayRunner, "persistence", handle)
    return runner


@pytest.mark.asyncio
async def test_build_runs_off_loop_and_is_visible_to_shutdown(monkeypatch):
    started, release, built = _install_blocking_agent(monkeypatch)
    runner = _runner(monkeypatch)
    loop_thread = threading.get_ident()

    build = asyncio.create_task(
        runner._hmwa_hygiene_build_agent("m", {}, SimpleNamespace(session_id="sess-1"))
    )
    try:
        assert await asyncio.to_thread(started.wait, 10.0), "hygiene build never started"
        assert runner._active_deferred_agent_worker_count() == 1, "in-flight build hidden from shutdown"
    finally:
        release.set()
    agent, session_db = await asyncio.wait_for(build, 10.0)

    assert agent is built[0]
    assert agent.init_thread != loop_thread, "AIAgent was constructed on the event loop"
    assert session_db is runner.persistence
    assert agent.context_compressor.bound == (session_db, "sess-1")
    assert agent.compression_in_place is True
    assert agent._end_session_on_close is False
    assert agent.platform == gw_run._GATEWAY_HYGIENE_PLATFORM
    assert runner._active_deferred_agent_worker_count() == 0, "finished build left registered"


@pytest.mark.asyncio
async def test_turn_cancelled_mid_build_releases_the_built_agent(monkeypatch):
    started, release, built = _install_blocking_agent(monkeypatch)
    runner = _runner(monkeypatch)
    runner._cleanup_agent_resources_off_loop = AsyncMock()

    build = asyncio.create_task(
        runner._hmwa_hygiene_build_agent("m", {}, SimpleNamespace(session_id="sess-1"))
    )
    try:
        assert await asyncio.to_thread(started.wait, 10.0), "hygiene build never started"
        build.cancel()
        with pytest.raises(asyncio.CancelledError):
            await build
        # The executor job outlives the cancelled turn; shutdown must keep seeing it until it lands.
        assert runner._active_deferred_agent_worker_count() == 1
        runner._cleanup_agent_resources_off_loop.assert_not_awaited()
    finally:
        release.set()

    await async_wait_until(
        lambda: runner._cleanup_agent_resources_off_loop.await_count, what="orphaned hygiene agent released",
    )
    runner._cleanup_agent_resources_off_loop.assert_awaited_once_with(built[0], context="session hygiene")
    await async_wait_until(
        lambda: runner._active_deferred_agent_worker_count() == 0, what="finished build unregistered",
    )
