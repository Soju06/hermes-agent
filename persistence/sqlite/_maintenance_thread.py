"""Background WAL checkpoint and FTS merge for a writable store holder.

``StoreHolder._execute_write`` used to run the PASSIVE checkpoint and the bounded FTS merge inline on whichever
thread crossed the Nth write. The checkpoint takes the holder's writer lock, so every other writer queued behind a
multi-GB WAL flush (2026-07-28 event-loop stall ingredient). The write path now only advances ``_write_count``; a
``session-db-maintenance`` daemon thread runs both on the same cadence.
"""

from __future__ import annotations

import logging
import threading
import weakref
from typing import TYPE_CHECKING

from ._schema import _try_incremental_merge_fts

if TYPE_CHECKING:
    from ._holder import StoreHolder

# Pre-split logger identity, like the holder's: caplog tests pin "hermes_state".
logger = logging.getLogger("hermes_state")

MAINTENANCE_INTERVAL_S = 5.0


def _maintenance_loop(holder_ref: weakref.ReferenceType, stop_event: threading.Event, interval: float) -> None:
    """Drive checkpoint/FTS maintenance without retaining the holder: an unclosed holder stays collectable."""
    while not stop_event.wait(interval):
        holder = holder_ref()
        if holder is None:
            return
        try:
            maintenance_tick(holder)
        except Exception:  # health: allow BLE001 -- best-effort maintenance; the next tick retries
            logger.debug("db maintenance tick failed", exc_info=True)
        finally:
            del holder


def start_maintenance_thread(holder: StoreHolder) -> None:
    """Start the holder's maintenance thread (writable holders only, once)."""
    if holder.read_only or holder._maint_thread is not None:
        return
    thread = threading.Thread(
        target=_maintenance_loop,
        args=(weakref.ref(holder), holder._maint_stop, MAINTENANCE_INTERVAL_S),
        name="session-db-maintenance",
        daemon=True,
    )
    holder._maint_thread = thread
    thread.start()


def stop_maintenance_thread(holder: StoreHolder) -> None:
    """Stop the holder's maintenance thread and wait briefly for it."""
    holder._maint_stop.set()
    maint = holder._maint_thread
    # The loop drops its strong reference on the maintenance thread itself, so __del__ -> close()
    # can run there; joining the current thread would raise and abort the rest of close().
    if maint is not None and maint.is_alive() and maint is not threading.current_thread():
        maint.join(timeout=2.0)


def maintenance_tick(holder: StoreHolder) -> None:
    """Run due maintenance outside any caller's write transaction."""
    write_count = holder._write_count
    if write_count - holder._last_checkpoint_write_count >= holder._CHECKPOINT_EVERY_N_WRITES:
        holder._last_checkpoint_write_count = write_count
        holder._try_wal_checkpoint()
    if write_count - holder._last_fts_merge_write_count >= holder._FTS_MERGE_EVERY_N_WRITES:
        holder._last_fts_merge_write_count = write_count
        _try_incremental_merge_fts(holder)
