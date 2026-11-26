"""Track the Socket Mode reconnects slack_sdk starts from an untracked future.

A Slack ``disconnect`` frame (server refresh, ~every 5h) makes the SDK reconnect from an
UNTRACKED ``asyncio.ensure_future(run_message_listeners(...))`` (slack_sdk
socket_mode/async_client.py:131 -> :142). No client attribute points at that task, so the
attribute sweep in ``SlackAdapter._stop_socket_mode_handler`` missed it: when the watchdog saw the
mid-refresh ``is_connected() == False`` and tore the handler down, the in-flight ``connect()``
lost its aiohttp session and retried every ``ping_interval`` forever (2026-10-17 21:23Z, 27.6h of
``Session is closed``).
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any

logger = logging.getLogger(__name__)

# Per-client bookkeeping installed by track_socket_reconnects().
_SOCKET_RECONNECT_TASKS_ATTR = "_hermes_reconnect_tasks"
_SOCKET_SUPERSEDED_ATTR = "_hermes_superseded"


def track_socket_reconnects(client: Any) -> None:
    """Record every task that enters ``connect_to_new_endpoint()`` so teardown can cancel it.

    Instance-level wrap, so an SDK rename degrades to the old behaviour instead of breaking the
    adapter.
    """
    original = getattr(client, "connect_to_new_endpoint", None)
    if client is None or not callable(original):
        return
    if isinstance(getattr(client, _SOCKET_RECONNECT_TASKS_ATTR, None), weakref.WeakSet):
        return  # already wrapped
    tasks: weakref.WeakSet[asyncio.Task] = weakref.WeakSet()

    async def connect_to_new_endpoint(*args: Any, **kwargs: Any) -> Any:
        # A superseded or closed client must never dial again: its session is gone.
        if getattr(client, _SOCKET_SUPERSEDED_ATTR, False) is True or getattr(client, "closed", False) is True:
            return None
        task = asyncio.current_task()
        if task is not None:
            tasks.add(task)
        try:
            return await original(*args, **kwargs)
        finally:
            if task is not None:
                tasks.discard(task)

    try:
        setattr(client, _SOCKET_RECONNECT_TASKS_ATTR, tasks)
        client.connect_to_new_endpoint = connect_to_new_endpoint
    except Exception:  # pragma: no cover - exotic client without instance attrs
        logger.debug("[Slack] Could not track Socket Mode reconnect tasks", exc_info=True)


def socket_reconnect_tasks(client: Any) -> list[Any]:
    """Mark *client* superseded, then snapshot its in-flight reconnect tasks.

    Flag first: a listener that reaches ``connect_to_new_endpoint()`` after the snapshot
    returns at once instead of starting a ``connect()`` nobody will cancel. A client that
    :func:`track_socket_reconnects` could not wrap has no tracked tasks and no gate to close."""
    tasks = getattr(client, _SOCKET_RECONNECT_TASKS_ATTR, None)
    if not isinstance(tasks, weakref.WeakSet):
        return []
    setattr(client, _SOCKET_SUPERSEDED_ATTR, True)
    return list(tasks)
