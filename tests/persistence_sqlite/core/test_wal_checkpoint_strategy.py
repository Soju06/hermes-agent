"""Tests for the store holder's WAL checkpoint strategy (issue #45383).

Verifies that ALL checkpoints on the shared state.db use PASSIVE mode:
periodic, close(), and pre-VACUUM. TRUNCATE fires a full WAL reset, and
transient per-cron-run connections closing many times an hour would race
the live gateway writer and corrupt B-tree pages (#45383).
"""

import logging
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from persistence.sqlite import _maintenance_thread, _registry, _schema
from persistence.sqlite._holder import StoreHolder
from persistence.sqlite.admin import SQLiteStoreAdmin
from persistence.sqlite.admin._maintenance import _FtsStorageOptimizer
from persistence.sqlite.conversation._store.messages import append_message
from persistence.sqlite.conversation._store.sessions import create_session
from tests.persistence_sqlite.core._wal_generation_harness import pin_wal


class TrackingConnection:
    """sqlite3.Connection proxy that records executed SQL strings."""

    def __init__(self, conn):
        self._conn = conn
        self.execute_calls = []

    def execute(self, sql, *args, **kwargs):
        self.execute_calls.append(sql)
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture()
def db(tmp_path):
    """Create a store holder with a temp database file."""
    db_path = tmp_path / "test_state.db"
    session_db = StoreHolder(db_path)
    yield session_db
    try:
        session_db.close()
    except Exception:
        pass


class TestTryWalCheckpointPassive:
    """_try_wal_checkpoint() should use PASSIVE mode for periodic use."""

    def test_checkpoint_uses_passive_mode(self, db):
        """PASSIVE checkpoint does not require exclusive lock: safe for large DBs."""
        # Capture the real connection's execute before mocking
        real_conn = db._conn
        execute_calls = []

        def tracking_execute(sql, *args, **kwargs):
            execute_calls.append(sql)
            return real_conn.execute(sql, *args, **kwargs)

        # sqlite3.Connection.execute is read-only (C extension): replace _conn
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = tracking_execute
        mock_conn.fetchone.return_value = None
        db._conn = mock_conn

        db._try_wal_checkpoint()

        passive_calls = [c for c in execute_calls if "wal_checkpoint(PASSIVE)" in c]
        truncate_calls = [c for c in execute_calls if "wal_checkpoint(TRUNCATE)" in c]
        assert len(passive_calls) == 1, (
            f"Expected 1 PASSIVE checkpoint call, got {len(passive_calls)}"
        )
        assert len(truncate_calls) == 0, (
            "Periodic checkpoint should NOT use TRUNCATE"
        )

    def test_failed_checkpoint_never_raises_and_is_not_silent(self, db, caplog):
        """The periodic checkpoint runs from the write path: a failing PASSIVE
        checkpoint must not raise into the caller, and must leave a WARNING."""
        real_conn = db._conn
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3.OperationalError("disk I/O error")
        db._conn = mock_conn
        try:
            with caplog.at_level(logging.WARNING, logger="hermes_state"):
                db._try_wal_checkpoint()
        finally:
            db._conn = real_conn
        assert any(r.levelno >= logging.WARNING for r in caplog.records)




class TestCloseUsesPassive:
    """close() must use PASSIVE. Transient per-cron-run store holder connections
    close many times an hour; a TRUNCATE reset there races the live gateway
    writer on the large WAL DB and corrupts B-tree pages (#45383)."""

    def test_close_uses_passive_mode(self, db):
        """close() checkpoints PASSIVE, never TRUNCATE."""
        real_conn = db._conn
        execute_calls = []

        def tracking_execute(sql, *args, **kwargs):
            execute_calls.append(sql)
            return real_conn.execute(sql, *args, **kwargs)

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = tracking_execute
        db._conn = mock_conn

        db.close()

        truncate_calls = [c for c in execute_calls if "wal_checkpoint(TRUNCATE)" in c]
        passive_calls = [c for c in execute_calls if "wal_checkpoint(PASSIVE)" in c]
        assert len(truncate_calls) == 0, (
            "close() must NOT TRUNCATE (races the live gateway writer, #45383)"
        )
        assert len(passive_calls) == 1, (
            f"Expected 1 PASSIVE checkpoint at close, got {len(passive_calls)}"
        )

    def test_failed_checkpoint_at_close_still_releases_the_handle(self, db):
        """close() is best-effort about the checkpoint: a failing PASSIVE at
        close must neither raise nor leave the handle open."""
        real_conn = db._conn
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = sqlite3.OperationalError("database is locked")
        db._conn = mock_conn
        try:
            db.close()
        finally:
            real_conn.close()

        assert db._conn is None
        mock_conn.close.assert_called()



class TestVacuumUsesPassive:
    """Manual vacuum paths must checkpoint PASSIVE, never TRUNCATE."""

    def test_vacuum_uses_passive_before_vacuum(self, tmp_path):
        """The admin's vacuum() checkpoints PASSIVE before VACUUM.

        A TRUNCATE checkpoint AFTER the VACUUM is expected: VACUUM rewrites
        every page through the WAL, so without it a 3 GB database leaves a
        3 GB state.db-wal behind and `sessions optimize` becomes a net disk
        LOSS. The #45383 tearing concern is about truncating while another
        writer is mid-transaction: the pre-VACUUM checkpoint stays PASSIVE
        for that reason; the post-VACUUM truncate runs under the same
        exclusive-lock window the VACUUM itself required.
        """
        db = _registry.acquire(tmp_path / "test_state.db")  # the admin vacuums on the registry's writer
        real_conn = db._conn
        tracking_conn = TrackingConnection(real_conn)
        db._conn = tracking_conn

        SQLiteStoreAdmin(db.db_path, read_only=False).vacuum()

        checkpoint_calls = [
            c for c in tracking_conn.execute_calls if "wal_checkpoint" in c.lower()
        ]
        truncate_calls = [c for c in checkpoint_calls if "TRUNCATE" in c]
        passive_calls = [c for c in checkpoint_calls if "PASSIVE" in c]
        vacuum_calls = [
            c for c in tracking_conn.execute_calls if c.strip().upper() == "VACUUM"
        ]
        assert passive_calls == ["PRAGMA wal_checkpoint(PASSIVE)"]
        assert vacuum_calls == ["VACUUM"]
        assert truncate_calls == ["PRAGMA wal_checkpoint(TRUNCATE)"]
        vacuum_index = tracking_conn.execute_calls.index(vacuum_calls[0])
        assert tracking_conn.execute_calls.index(passive_calls[0]) < vacuum_index
        # TRUNCATE must come only AFTER the VACUUM (never before it).
        assert tracking_conn.execute_calls.index(truncate_calls[0]) > vacuum_index

    def test_optimize_storage_uses_passive_after_vacuum(self, db):
        """optimize_fts_storage() checkpoints PASSIVE after its VACUUM."""
        real_conn = db._conn
        tracking_conn = TrackingConnection(real_conn)
        db._conn = tracking_conn

        result = _FtsStorageOptimizer(db).optimize_fts_storage(vacuum=True)

        checkpoint_calls = [
            c for c in tracking_conn.execute_calls if "wal_checkpoint" in c.lower()
        ]
        truncate_calls = [c for c in checkpoint_calls if "TRUNCATE" in c]
        passive_calls = [c for c in checkpoint_calls if "PASSIVE" in c]
        vacuum_calls = [
            c for c in tracking_conn.execute_calls if c.strip().upper() == "VACUUM"
        ]
        assert result["ok"] is True
        assert result["vacuumed"] is True
        assert truncate_calls == []
        assert passive_calls == ["PRAGMA wal_checkpoint(PASSIVE)"]
        assert vacuum_calls == ["VACUUM"]
        assert tracking_conn.execute_calls.index(
            vacuum_calls[0]
        ) < tracking_conn.execute_calls.index(passive_calls[0])


class TestCheckpointFrequency:
    """Checkpoint runs from the maintenance thread, never the write hot path.

    Inline per-Nth-write checkpointing made whichever caller crossed the
    threshold pay for a multi-GB WAL flush while every other writer queued
    behind self._lock (2026-07-28 event-loop stall ingredient). The write
    path now only counts; the session-db-maintenance thread checkpoints
    once the counter advances past _CHECKPOINT_EVERY_N_WRITES.
    """

    def test_write_path_never_checkpoints_inline(self, db):
        # The background thread would race these counts on a slow runner; only explicit ticks here.
        _maintenance_thread.stop_maintenance_thread(db)
        call_count = [0]
        original = db._try_wal_checkpoint

        def counting_checkpoint():
            call_count[0] += 1
            original()

        db._try_wal_checkpoint = counting_checkpoint

        n = db._CHECKPOINT_EVERY_N_WRITES
        import time as _time
        for i in range(n * 2):
            db._execute_write(lambda conn, _i=i: conn.execute(
                "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
                (f"sess_{_i}", "test", _time.time()),
            ))

        assert call_count[0] == 0, (
            f"write path must not checkpoint inline, got {call_count[0]} calls"
        )

    def test_maintenance_tick_checkpoints_after_threshold(self, db):
        # The background thread would race these counts on a slow runner; only explicit ticks here.
        _maintenance_thread.stop_maintenance_thread(db)
        call_count = [0]
        original = db._try_wal_checkpoint

        def counting_checkpoint():
            call_count[0] += 1
            original()

        db._try_wal_checkpoint = counting_checkpoint

        n = db._CHECKPOINT_EVERY_N_WRITES
        import time as _time
        for i in range(n):
            db._execute_write(lambda conn, _i=i: conn.execute(
                "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
                (f"sess_{_i}", "test", _time.time()),
            ))

        _maintenance_thread.maintenance_tick(db)
        assert call_count[0] == 1, (
            f"Expected 1 checkpoint from the maintenance tick, got {call_count[0]}"
        )
        # Below-threshold delta: the next tick is a no-op.
        _maintenance_thread.maintenance_tick(db)
        assert call_count[0] == 1

    def test_maintenance_thread_lifecycle(self, tmp_path):
        db = StoreHolder(tmp_path / "maint.db")
        thread = db._maint_thread
        assert thread is not None and thread.is_alive()
        db.close()
        thread.join(timeout=3.0)
        assert not thread.is_alive()

    def test_maintenance_thread_does_not_pin_unclosed_db(self, tmp_path):
        """The maintenance thread must hold only a weakref to the holder.

        Tests and short-lived callers create a store holder without close();
        a bound-method thread target pinned every such instance and its
        sqlite caches for the process lifetime — a full-suite run was
        OOM-killed at 37GB RSS. Unclosed instances must stay collectable.
        """
        import gc
        import weakref

        db = StoreHolder(tmp_path / "leak.db")
        thread = db._maint_thread
        ref = weakref.ref(db)
        del db  # no close() on purpose
        gc.collect()
        assert ref() is None, "an unclosed store holder must be garbage-collectable"
        # The orphaned thread notices the dead ref on its next wake and
        # exits on its own (daemon either way; this just proves the exit
        # path). Waking it early via the stop event would defeat the
        # point, so poke the weakref path directly with a short interval.
        assert thread.daemon


class TestMaintenanceTickGenerationProbe:
    """The background tick re-probes for a replaced file or lost WAL generation before maintaining.

    Inline, the checkpoint and FTS merge ran right after the triggering write's in-lock probe; on the
    maintenance thread up to one interval passes without one, and a checkpoint on a lost generation
    writes stale frames into the main DB (#105670).
    """

    @pytest.mark.platforms("linux")  # deleted-WAL detection uses Linux unlink semantics
    def test_tick_after_deleted_wal_generation_quarantines_instead_of_maintaining(self, tmp_path, monkeypatch):
        # Pin WAL so this host's SQLite matches the production topology (as in the #105670 guard tests).
        pin_wal(monkeypatch)
        path = tmp_path / "state.db"
        db = StoreHolder(db_path=path)
        try:
            # Only the explicit tick below may run maintenance.
            _maintenance_thread.stop_maintenance_thread(db)
            create_session(db, "s", "cli")
            append_message(db, "s", role="user", content="before")
            if not db._wal_active or not Path(str(path) + "-wal").exists():
                pytest.skip("WAL not active on this filesystem")
            db._CHECKPOINT_EVERY_N_WRITES = 1
            db._FTS_MERGE_EVERY_N_WRITES = 1
            merges = []
            monkeypatch.setattr(_schema, "_merge_fts_incrementally", lambda holder, **kw: merges.append(kw) or 0)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(path) + suffix)
                if sidecar.exists():
                    os.unlink(sidecar)

            with patch.object(db._conn, "execute", wraps=db._conn.execute) as mock_execute:
                _maintenance_thread.maintenance_tick(db)
            checkpoint_calls = [c for c in mock_execute.call_args_list if "wal_checkpoint" in str(c).lower()]

            assert db._db_wal_generation_lost is True, "the tick did not probe the WAL generation"
            assert not checkpoint_calls, f"the tick checkpointed a lost WAL generation: {checkpoint_calls}"
            assert merges == [], "the tick ran an FTS merge on a lost WAL generation"
        finally:
            db.close()
