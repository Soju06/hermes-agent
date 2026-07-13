"""Races between the request-client reuse cache and the abort machinery.

Review follow-ups to the per-request wire-client reuse patch:

1. A worker-side interrupt used to ``break`` out of the SSE chunk loop
   without closing the stream. The partial response built afterwards made
   the worker's finally report a reuse-reason close, so the cached client
   kept a permanently checked-out (half-read) connection — one more leaked
   per interrupt until the pool hit ``max_connections`` and every request
   died with PoolTimeout. The break must close the stream first (owner
   thread), and poison the slot if that close fails.

2. The stranger-thread abort (stale detector / interrupt loop) used to read
   the holder under ``request_client_lock`` but fire the abort after
   releasing it. In that window the worker's finally could pop + cache the
   client and the NEXT call check it out — the late abort then poisoned the
   slot and shut down an innocent in-flight request's sockets. The holder
   read and the abort must be atomic: the abort now runs under the lock.
"""
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_agent():
    from run_agent import AIAgent

    agent = AIAgent(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="test/model",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )
    agent.api_mode = "chat_completions"
    return agent


def _chunk(content=None, finish_reason=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                delta=SimpleNamespace(
                    content=content,
                    tool_calls=None,
                    reasoning_content=None,
                    reasoning=None,
                ),
                finish_reason=finish_reason,
            )
        ],
        model="test/model",
        usage=None,
    )


class _FakeStream:
    """SSE stream stand-in.

    Deliberately has NO ``choices`` attribute so ``_call_chat_completions``
    treats it as a genuine token stream (a MagicMock would auto-create
    ``choices`` and get misread as a completed response object).
    """

    response = None

    def __init__(self, chunk_iter_factory, close_raises=False):
        self._factory = chunk_iter_factory
        self._close_raises = close_raises
        self.close_calls = 0

    def __iter__(self):
        return self._factory()

    def close(self):
        self.close_calls += 1
        if self._close_raises:
            raise RuntimeError("close failed")


def _mock_wire_client(stream):
    client = MagicMock()
    client.chat.completions.create.return_value = stream
    return client


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_worker_interrupt_break_closes_stream():
    """Interrupt noticed between chunks must close the half-read stream.

    Without the close, the connection stays checked out of the httpx pool
    while the partial-response finally caches the client for reuse — the
    leak that eventually exhausted the pool (PoolTimeout on every request).
    """
    agent = _make_agent()

    def chunks():
        yield _chunk(content="partial ")
        # /stop arrives while the provider is still streaming.
        agent._interrupt_requested = True
        yield _chunk(content="never processed")

    stream = _FakeStream(chunks)

    with patch.object(
        agent, "_create_request_openai_client", return_value=_mock_wire_client(stream)
    ), patch.object(agent, "_close_request_openai_client"):
        with pytest.raises(InterruptedError):
            agent._interruptible_streaming_api_call({})

    assert stream.close_calls == 1


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_worker_interrupt_break_poisons_slot_when_stream_close_fails():
    """If the half-read stream can't be released, the client must not be
    cached: the owner-thread abort poisons the slot so the worker's finally
    really closes the pool (leaked connection and all)."""
    agent = _make_agent()

    def chunks():
        yield _chunk(content="partial ")
        agent._interrupt_requested = True
        yield _chunk(content="never processed")

    stream = _FakeStream(chunks, close_raises=True)
    abort_reasons = []

    with patch.object(
        agent, "_create_request_openai_client", return_value=_mock_wire_client(stream)
    ), patch.object(agent, "_close_request_openai_client"), patch.object(
        agent,
        "_abort_request_openai_client",
        side_effect=lambda client, *, reason: abort_reasons.append(reason),
    ):
        with pytest.raises(InterruptedError):
            agent._interruptible_streaming_api_call({})

    assert stream.close_calls == 1
    assert "interrupt_stream_close_failed" in abort_reasons


def test_stale_abort_is_atomic_with_holder_read(monkeypatch):
    """The stranger-thread abort must complete before the worker's finally
    can pop + cache the client.

    The abort runs under ``request_client_lock``; a worker that finishes
    while the abort is in flight must block in its finally until the abort
    returns. Pre-fix, the worker could cache the client (and the next call
    check it out) between the holder read and the abort — the abort then
    killed an innocent request's sockets.
    """
    monkeypatch.setenv("HERMES_STREAM_STALE_TIMEOUT", "0.05")
    agent = _make_agent()

    allow_finish = threading.Event()
    worker_close_reasons = []
    abort_reasons = []
    observed = {"worker_finished_during_abort": None}

    def chunks():
        yield _chunk(content="hello")
        # Stall long enough for the stale detector to fire, then finish
        # cleanly the moment the abort (below) unblocks us.
        allow_finish.wait(timeout=5.0)
        yield _chunk(finish_reason="stop")

    stream = _FakeStream(chunks)

    def fake_abort(client, *, reason):
        abort_reasons.append(reason)
        # Let the worker race toward its finally while the abort is still
        # in flight. Under the fix it must block on the holder lock, so the
        # owner-side close cannot land until this abort returns.
        allow_finish.set()
        deadline = time.time() + 0.6
        while time.time() < deadline and not worker_close_reasons:
            time.sleep(0.02)
        observed["worker_finished_during_abort"] = bool(worker_close_reasons)

    with patch.object(
        agent, "_create_request_openai_client", return_value=_mock_wire_client(stream)
    ), patch.object(
        agent,
        "_close_request_openai_client",
        side_effect=lambda client, *, reason: worker_close_reasons.append(reason),
    ), patch.object(
        agent, "_abort_request_openai_client", side_effect=fake_abort
    ), patch.object(
        agent, "_replace_primary_openai_client"
    ):
        response = agent._interruptible_streaming_api_call({})

    assert response is not None
    assert "stale_stream_kill" in abort_reasons
    # The atomicity contract: no owner-side close slipped in mid-abort.
    assert observed["worker_finished_during_abort"] is False
    # ...and the worker's own finally still performed its close afterwards.
    assert worker_close_reasons == ["stream_request_complete"]
