"""A store that stopped mid-transition keeps the superseded ``messages_fts_v2`` generation (its vtable,
triggers on ``messages`` and ``fts_v2_*`` markers) while no code reads it. Its tokenizer is never loaded,
so optimize-storage must retire it before any legacy ALTER TABLE, and the legacy demote must stage only the
exact FTS5 shadows of the vtables it demotes (never a ``messages_fts_%`` prefix sweep)."""

import sqlite3
import time

import pytest

from persistence.sqlite._holder import StoreHolder
from persistence.sqlite._schema import _db_has_legacy_inline_fts, _has_fts_trash
from persistence.sqlite._schema_ddl import FTS_STORAGE_VERSION, LEGACY_FTS_SQL, LEGACY_FTS_TRIGRAM_SQL, SCHEMA_SQL
from persistence.sqlite.admin._maintenance import _FtsStorageOptimizer
from persistence.sqlite.conversation._store.messages import append_message
from persistence.sqlite.conversation._store.search import search_messages
from persistence.sqlite.conversation._store.sessions import create_session

_V2_TRIGGERS = ("messages_fts_v2_insert", "messages_fts_v2_delete", "messages_fts_v2_update")
_CJK_TRIGGERS = ("messages_fts_cjk_insert", "messages_fts_cjk_delete", "messages_fts_cjk_update")


def _legacy_inline_store(db_path) -> None:
    raw = sqlite3.connect(str(db_path))
    try:
        raw.executescript(SCHEMA_SQL + LEGACY_FTS_SQL + LEGACY_FTS_TRIGRAM_SQL)
        raw.execute("INSERT INTO state_meta (key, value) VALUES ('fts_optimize_available', '1')")
        raw.execute("INSERT INTO sessions (id, source, started_at) VALUES ('s1', 'cli', ?)", (time.time(),))
        for role, content in (
            ("user", "historicalneedle deployment notes"),
            ("assistant", "historicalneedle response"),
            ("tool", "historicalneedle tool payload"),
        ):
            raw.execute("INSERT INTO messages (session_id, timestamp, role, content) VALUES ('s1', ?, ?, ?)",
                        (time.time(), role, content))
        raw.commit()
    finally:
        raw.close()


def _add_retired_v2_with_unavailable_tokenizer(db_path) -> None:
    """Build a valid v2 index, then poison only its stored tokenizer DDL (as a process without
    cjk_unicode61 sees it)."""
    raw = sqlite3.connect(str(db_path))
    try:
        raw.executescript(
            """
            CREATE VIRTUAL TABLE messages_fts_v2 USING fts5(content, tool_name, tool_calls, tokenize='unicode61');
            CREATE TRIGGER messages_fts_v2_insert AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts_v2(rowid, content, tool_name, tool_calls)
                VALUES (new.id, new.content, new.tool_name, new.tool_calls);
            END;
            CREATE TRIGGER messages_fts_v2_delete AFTER DELETE ON messages BEGIN
                DELETE FROM messages_fts_v2 WHERE rowid = old.id;
            END;
            CREATE TRIGGER messages_fts_v2_update AFTER UPDATE OF content, tool_name, tool_calls ON messages BEGIN
                DELETE FROM messages_fts_v2 WHERE rowid = old.id;
                INSERT INTO messages_fts_v2(rowid, content, tool_name, tool_calls)
                VALUES (new.id, new.content, new.tool_name, new.tool_calls);
            END;
            INSERT INTO messages_fts_v2(rowid, content, tool_name, tool_calls)
            SELECT id, content, tool_name, tool_calls FROM messages;
            INSERT INTO state_meta (key, value) VALUES ('fts_v2_ready', '1');
            INSERT INTO state_meta (key, value)
            SELECT 'fts_v2_backfill_snapshot_max', CAST(MAX(id) AS TEXT) FROM messages;
            INSERT INTO state_meta (key, value) VALUES ('fts_v2_backfill_next_lo', '1');
            """
        )
        raw.execute("PRAGMA writable_schema=ON")
        raw.execute(
            "UPDATE sqlite_master SET sql = replace(sql, ?, ?) WHERE type = 'table' AND name = 'messages_fts_v2'",
            ("tokenize='unicode61'", "tokenize='missing_test_tokenizer'"),
        )
        raw.execute("PRAGMA writable_schema=RESET")
        raw.commit()
    finally:
        raw.close()


def _add_cjk_sibling(db_path) -> None:
    """The messages_fts_cjk generation that superseded v2 (retired on open since the CJK tokenizer left core)."""
    raw = sqlite3.connect(str(db_path))
    try:
        raw.executescript(
            "CREATE VIEW messages_fts_cjk_src AS SELECT id, role, content, tool_name, tool_calls FROM messages"
            " WHERE role <> 'tool';"
            "CREATE VIRTUAL TABLE messages_fts_cjk USING fts5(content, tool_name, tool_calls,"
            " content='messages_fts_cjk_src', content_rowid='id', tokenize='unicode61');"
            "INSERT INTO messages_fts_cjk(rowid, content, tool_name, tool_calls)"
            " SELECT id, content, tool_name, tool_calls FROM messages WHERE role <> 'tool';"
        )
        for name, event, row in zip(_CJK_TRIGGERS, ("INSERT", "DELETE", "UPDATE"), ("new", "old", "new")):
            raw.execute(f"CREATE TRIGGER {name} AFTER {event} ON messages BEGIN"
                        f" INSERT INTO messages_fts_cjk(rowid, content) VALUES ({row}.id, {row}.content); END")
        raw.commit()
    finally:
        raw.close()


def _object_names(conn, prefix: str) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE ?", (prefix + "%",))}


def _assert_v2_retired(conn) -> None:
    assert _object_names(conn, "messages_fts_v2") == set()
    assert _object_names(conn, "fts_v22_trash_messages_fts_v2") == set()  # torn down, not left staged
    for trigger in _V2_TRIGGERS:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                            (trigger,)).fetchone() is None
    assert conn.execute("SELECT 1 FROM state_meta WHERE key LIKE 'fts_v2_%'").fetchone() is None


def _outside_insert(db_path) -> None:
    with sqlite3.connect(db_path) as raw:
        raw.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES ('s1', 'user', 'outside', 0)")


def test_demote_only_stages_exact_legacy_generation(tmp_path):
    db_path = tmp_path / "state.db"
    _legacy_inline_store(db_path)
    with sqlite3.connect(db_path) as raw:
        raw.execute("CREATE VIRTUAL TABLE messages_fts_future USING fts5(content)")
        sibling_before = _object_names(raw, "messages_fts_future")
    raw.close()

    db = StoreHolder(db_path=db_path)
    try:
        _FtsStorageOptimizer(db)._demote_legacy_fts_to_trash()
        assert _object_names(db._conn, "messages_fts_future") == sibling_before
        assert not _object_names(db._conn, "fts_v22_trash_messages_fts_future")
        # ...while the demoted generation's own shadows are staged.
        assert "fts_v22_trash_messages_fts_data" in _object_names(db._conn, "fts_v22_trash_")
    finally:
        db.close()


def test_optimize_retires_poisoned_v2_beside_the_retired_cjk_index(tmp_path):
    db_path = tmp_path / "state.db"
    _legacy_inline_store(db_path)
    _add_cjk_sibling(db_path)
    _add_retired_v2_with_unavailable_tokenizer(db_path)
    with pytest.raises(sqlite3.OperationalError, match="missing_test_tokenizer"):
        _outside_insert(db_path)

    db = StoreHolder(db_path=db_path)
    try:
        optimizer = _FtsStorageOptimizer(db)
        assert optimizer.fts_optimize_available()
        result = optimizer.optimize_fts_storage(vacuum=False)

        assert result["ok"] is True
        assert db.get_meta("fts_storage_version") == str(FTS_STORAGE_VERSION)
        assert search_messages(db, "historicalneedle")
        assert db._conn.execute("SELECT EXISTS(SELECT 1 FROM messages_fts_docsize)").fetchone()[0] == 1
        _assert_v2_retired(db._conn)
        # The cjk generation went through upstream's open-time retirement, untouched by the v2 path.
        assert _object_names(db._conn, "messages_fts_cjk") == set()
        assert not _has_fts_trash(db, db._conn)
        assert not optimizer.fts_optimize_available()
    finally:
        db.close()
    # Writers that never load any optional tokenizer work again, and stock integrity_check passes.
    _outside_insert(db_path)
    with sqlite3.connect(db_path) as raw:
        assert raw.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_orphaned_v2_alone_is_offered_and_retired(tmp_path):
    """A compact-layout store still offers cleanup for the retired generation."""
    db_path = tmp_path / "state.db"
    seeded = StoreHolder(db_path=db_path)
    try:
        create_session(seeded, "s1", source="cli")
        append_message(seeded, "s1", role="user", content="standalone orphan historicalneedle")
    finally:
        seeded.close()
    _add_retired_v2_with_unavailable_tokenizer(db_path)

    db = StoreHolder(db_path=db_path)
    try:
        assert _db_has_legacy_inline_fts(db._conn) is False
        optimizer = _FtsStorageOptimizer(db)
        assert optimizer.fts_optimize_available() is True

        result = optimizer.optimize_fts_storage(vacuum=False)

        assert result["ok"] is True
        assert optimizer.fts_optimize_available() is False
        _assert_v2_retired(db._conn)
        assert search_messages(db, "historicalneedle")
    finally:
        db.close()
    _outside_insert(db_path)


def test_unverifiable_base_index_leaves_v2_untouched(tmp_path):
    """Fail closed: a base index that does not cover every message is never left as the only generation."""
    db_path = tmp_path / "state.db"
    _legacy_inline_store(db_path)
    _add_retired_v2_with_unavailable_tokenizer(db_path)
    with sqlite3.connect(db_path) as raw:
        raw.execute("DELETE FROM messages_fts WHERE rowid = (SELECT MIN(id) FROM messages)")
    raw.close()

    db = StoreHolder(db_path=db_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="row-count mismatch"):
            _FtsStorageOptimizer(db).optimize_fts_storage(vacuum=False)
        assert "messages_fts_v2" in _object_names(db._conn, "messages_fts_v2")
        assert set(_V2_TRIGGERS) <= _object_names(db._conn, "messages_fts_v2")
        assert db.get_meta("fts_v2_ready") == "1"
        assert not _object_names(db._conn, "fts_v22_trash_messages_fts_v2")
    finally:
        db.close()
