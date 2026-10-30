"""Background WAL checkpoint and FTS merge for a writable store holder.

``StoreHolder._execute_write`` used to run the PASSIVE checkpoint and the bounded FTS merge inline on whichever
thread crossed the Nth write. The checkpoint takes the holder's writer lock, so every other writer queued behind a
multi-GB WAL flush (2026-07-28 event-loop stall ingredient). The write path now only advances ``_write_count``; a
``session-db-maintenance`` daemon thread runs both on the same cadence. A holder whose thread cannot start keeps the
inline cadence, so opening the store never depends on the thread.
"""

from __future__ import annotations

import logging
import threading
import weakref
from typing import TYPE_CHECKING

from persistence.sqlite._state.errors import StateDbReplacedError  # DeletedWalGenerationError subclasses it

from ._schema import _try_incremental_merge_fts

if TYPE_CHECKING:
    from ._holder import StoreHolder

# Pre-split logger identity, like the holder's: caplog tests pin "hermes_state".
logger = logging.getLogger("hermes_state")

MAINTENANCE_INTERVAL_S = 5.0


def _maintenance_loop(
    holder_ref: weakref.ReferenceType, stop_event: threading.Event, interval: float, opener_ident: int | None = None,
) -> None:
    """Drive checkpoint/FTS maintenance without retaining the holder: an unclosed holder stays collectable."""
    # A ``threading.Thread`` stand-in that runs its target inline would run this loop on the thread
    # that is inside StoreHolder() and block the open forever; only a separate thread may loop.
    if threading.get_ident() == opener_ident:
        return
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
    """Start the holder's maintenance thread; a thread that cannot start never fails or blocks the open.

    Runs inside ``StoreHolder.__init__``'s open block. A raise here (thread or pid limit) made the whole store
    unopenable, so every caller reported "storage unavailable" because of a best-effort helper. A process-wide
    ``threading.Thread`` stand-in that runs targets inline either raised the same way or ran the loop on the opener
    and never returned. Opening never depended on a thread while this work ran inline, so a holder without a
    running maintenance thread keeps that inline cadence instead."""
    if holder.read_only or holder._maint_thread is not None:
        return
    try:
        thread = threading.Thread(
            target=_maintenance_loop,
            args=(weakref.ref(holder), holder._maint_stop, MAINTENANCE_INTERVAL_S, threading.get_ident()),
            name="session-db-maintenance",
            daemon=True,
        )
        holder._maint_thread = thread
        thread.start()
        # A real thread is alive here: start() waits for it, and the loop first waits one interval.
        if thread.is_alive():
            return
        failure = "not running after start()"
    except Exception as exc:  # health: allow BLE001 -- any start failure falls back to the inline cadence
        failure = f"{type(exc).__name__}: {exc}"
    holder._maint_thread = None
    holder._maint_inline = True
    logger.warning(
        "state.db maintenance thread did not start for %s (%s); running WAL checkpoint and FTS "
        "merge inline on the write path for this handle", holder.db_path, failure)


def inline_maintenance_tick(holder: StoreHolder) -> None:
    """Write-path fallback for a holder whose maintenance thread did not start. Never raises: the write
    already committed, and an error reaching ``_execute_write``'s retry handlers would replay it."""
    try:
        maintenance_tick(holder)
    except Exception:  # health: allow BLE001 -- the write has committed; no maintenance failure may escape
        logger.debug("inline db maintenance tick failed", exc_info=True)


def stop_maintenance_thread(holder: StoreHolder) -> None:
    """Stop the holder's maintenance thread and wait briefly for it."""
    holder._maint_stop.set()
    maint = holder._maint_thread
    # The loop drops its strong reference on the maintenance thread itself, so __del__ -> close()
    # can run there; joining the current thread would raise and abort the rest of close().
    if maint is not None and maint.is_alive() and maint is not threading.current_thread():
        maint.join(timeout=2.0)


def _generation_intact(holder: StoreHolder) -> bool:
    """Re-run the writers' in-lock replaced/lost-generation probe before background maintenance.

    Inline, the checkpoint and FTS merge ran right after the triggering write's probe; on this
    thread up to one interval passes without one, and a checkpoint on a lost WAL generation writes
    stale frames into the main DB (#105670). A positive probe sets the same sticky quarantine a
    write would, so every later tick (and close()) skips the file too."""
    if holder._quarantine_reason() is not None:
        return False
    with holder._lock:
        if holder._conn is None:  # closed underneath the tick: nothing to maintain
            return False
        try:
            holder._raise_if_db_replaced()
        except StateDbReplacedError:
            return False
    return True


def maintenance_tick(holder: StoreHolder) -> None:
    """Run due maintenance outside any caller's write transaction."""
    write_count = holder._write_count
    checkpoint_due = write_count - holder._last_checkpoint_write_count >= holder._CHECKPOINT_EVERY_N_WRITES
    fts_merge_due = write_count - holder._last_fts_merge_write_count >= holder._FTS_MERGE_EVERY_N_WRITES
    if not (checkpoint_due or fts_merge_due) or not _generation_intact(holder):
        return
    if checkpoint_due:
        holder._last_checkpoint_write_count = write_count
        holder._try_wal_checkpoint()
    if fts_merge_due:
        holder._last_fts_merge_write_count = write_count
        _try_incremental_merge_fts(holder)
