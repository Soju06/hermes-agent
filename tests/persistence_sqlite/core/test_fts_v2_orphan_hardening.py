"""A store that stopped mid-transition keeps the superseded ``messages_fts_v2`` generation (its vtable,
triggers on ``messages`` and ``fts_v2_*`` markers) while no code reads it. No process loads its tokenizer, so
its insert trigger fails every write to ``messages``: a writable open retires it (shadows staged for
optimize-storage's chunked teardown), the repair probe judges the store as that open will leave it, and the
legacy demote stages only the exact FTS5 shadows of the vtables it demotes (never a ``messages_fts_%`` prefix
sweep)."""

import contextlib
import logging
import sqlite3
import time

import pytest

from persistence.sqlite._fts_v2_retirement import drop_retired_fts_v2
from persistence.sqlite._holder import StoreHolder
from persistence.sqlite._schema import _has_fts_trash
from persistence.sqlite._schema_ddl import FTS_STORAGE_VERSION, LEGACY_FTS_SQL, LEGACY_FTS_TRIGRAM_SQL, SCHEMA_SQL
from persistence.sqlite._state import repair
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


@pytest.fixture
def v2_db(tmp_path):
    """A current-layout store that still carries the v2 generation, as a process without its tokenizer sees it."""
    db_path = tmp_path / "state.db"
    seeded = StoreHolder(db_path=db_path)
    try:
        create_session(seeded, "s1", source="cli")
        append_message(seeded, "s1", role="user", content="standalone orphan historicalneedle")
    finally:
        seeded.close()
    _add_retired_v2_with_unavailable_tokenizer(db_path)
    with pytest.raises(sqlite3.OperationalError, match="missing_test_tokenizer"):
        _outside_insert(db_path)
    return db_path


def _object_names(conn, prefix: str) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE ?", (prefix + "%",))}


def _v2_residue(conn) -> list[str]:
    """Every live v2 object and marker (staged trash excluded)."""
    objects = conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'messages\\_fts\\_v2%' ESCAPE '\\'")
    markers = conn.execute("SELECT key FROM state_meta WHERE key LIKE 'fts\\_v2\\_%' ESCAPE '\\'")
    return [row[0] for row in (*objects.fetchall(), *markers.fetchall())]


def _staged_v2_shadows(conn) -> set[str]:
    return _object_names(conn, "fts_v22_trash_messages_fts_v2")


def _store_snapshot(db_path) -> tuple:
    with contextlib.closing(sqlite3.connect(db_path)) as raw:
        schema = raw.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        return schema, raw.execute("SELECT key, value FROM state_meta ORDER BY key").fetchall()


def _outside_insert(db_path) -> None:
    with contextlib.closing(sqlite3.connect(db_path)) as raw, raw:
        raw.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES ('s1', 'user', 'outside', 0)")


def test_demote_only_stages_exact_legacy_generation(tmp_path):
    db_path = tmp_path / "state.db"
    _legacy_inline_store(db_path)
    with contextlib.closing(sqlite3.connect(db_path)) as raw, raw:
        raw.execute("CREATE VIRTUAL TABLE messages_fts_future USING fts5(content)")
        sibling_before = _object_names(raw, "messages_fts_future")

    db = StoreHolder(db_path=db_path)
    try:
        _FtsStorageOptimizer(db)._demote_legacy_fts_to_trash()
        assert _object_names(db._conn, "messages_fts_future") == sibling_before
        assert not _object_names(db._conn, "fts_v22_trash_messages_fts_future")
        # ...while the demoted generation's own shadows are staged.
        assert "fts_v22_trash_messages_fts_data" in _object_names(db._conn, "fts_v22_trash_")
    finally:
        db.close()


def test_open_retires_poisoned_v2_beside_the_retired_cjk_index(tmp_path):
    db_path = tmp_path / "state.db"
    _legacy_inline_store(db_path)
    _add_cjk_sibling(db_path)
    _add_retired_v2_with_unavailable_tokenizer(db_path)
    with pytest.raises(sqlite3.OperationalError, match="missing_test_tokenizer"):
        _outside_insert(db_path)

    StoreHolder(db_path=db_path).close()
    # Writers that never load any optional tokenizer work again, and stock integrity_check passes.
    _outside_insert(db_path)
    with contextlib.closing(sqlite3.connect(db_path)) as raw:
        assert _v2_residue(raw) == []
        assert "fts_v22_trash_messages_fts_v2_data" in _staged_v2_shadows(raw)
        assert raw.execute("PRAGMA integrity_check").fetchone() == ("ok",)

    db = StoreHolder(db_path=db_path)
    try:
        optimizer = _FtsStorageOptimizer(db)
        assert optimizer.fts_optimize_available()
        assert optimizer.optimize_fts_storage(vacuum=False)["ok"] is True
        assert db.get_meta("fts_storage_version") == str(FTS_STORAGE_VERSION)
        assert search_messages(db, "historicalneedle")
        # The cjk generation went through upstream's open-time retirement, the v2 shadows through the teardown.
        assert _object_names(db._conn, "messages_fts_cjk") == set()
        assert not _staged_v2_shadows(db._conn)
        assert not _has_fts_trash(db, db._conn)
        assert not optimizer.fts_optimize_available()
    finally:
        db.close()


def test_open_lets_a_current_store_take_messages_again(v2_db, caplog):
    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db = StoreHolder(db_path=v2_db)
    try:
        append_message(db, "s1", role="user", content="after retirement")
        assert _v2_residue(db._conn) == []
        # The holder's shared writer connection is handed back with both schema pragmas off.
        assert db._conn.execute("PRAGMA legacy_alter_table").fetchone()[0] == 0
        assert db._conn.execute("PRAGMA writable_schema").fetchone()[0] == 0
        optimizer = _FtsStorageOptimizer(db)
        assert optimizer.fts_optimize_available() is True  # the staged shadows
        assert optimizer.optimize_fts_storage(vacuum=False)["ok"] is True
        assert optimizer.fts_optimize_available() is False
        assert not _staged_v2_shadows(db._conn)
        assert len(search_messages(db, "historicalneedle")) == 1
    finally:
        db.close()
    assert [r for r in caplog.records if "messages_fts_v2" in r.getMessage()]
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        StoreHolder(db_path=v2_db).close()
    assert not [r for r in caplog.records if "messages_fts_v2" in r.getMessage()]
    _outside_insert(v2_db)


def test_repair_probe_judges_the_store_with_v2_retired(v2_db):
    """The probe's write check would trip the v2 trigger and call a store the next open repairs by itself damaged."""
    before = _store_snapshot(v2_db)

    assert repair._db_opens_cleanly(v2_db) is None
    assert _store_snapshot(v2_db) == before


def test_retirement_beside_an_index_whose_tokenizer_is_missing(v2_db):
    """Renaming the v2 shadows must not re-parse the trigram triggers. Discriminates only on SQLite whose modern
    rename re-parses triggers (3.45.1); on 3.53.1 the pragma reset is what this checks."""
    with contextlib.closing(sqlite3.connect(v2_db, isolation_level=None)) as raw:
        raw.execute("PRAGMA writable_schema=ON")
        raw.execute("UPDATE sqlite_master SET sql = replace(sql, \"tokenize='trigram'\", \"tokenize='absent_trigram'\") "
                    "WHERE name = 'messages_fts_trigram'")
        version = raw.execute("PRAGMA schema_version").fetchone()[0]
        raw.execute(f"PRAGMA schema_version={version + 1}")
        raw.execute("PRAGMA writable_schema=OFF")
    with contextlib.closing(sqlite3.connect(v2_db, isolation_level=None)) as conn:
        assert drop_retired_fts_v2(conn.cursor())
        assert not conn.in_transaction
        assert conn.execute("PRAGMA legacy_alter_table").fetchone()[0] == 0
        assert _v2_residue(conn) == []
        assert "fts_v22_trash_messages_fts_v2_data" in _staged_v2_shadows(conn)


@pytest.mark.parametrize("outer_transaction", [False, True])
def test_failed_retirement_preserves_the_store(v2_db, outer_transaction):
    before = _store_snapshot(v2_db)

    class FailingCursor(sqlite3.Cursor):
        def execute(self, sql, parameters=()):
            if sql.startswith("ALTER TABLE messages_fts_v2_data RENAME"):
                raise sqlite3.OperationalError("injected rename failure")
            return super().execute(sql, parameters)

    with contextlib.closing(sqlite3.connect(v2_db, isolation_level=None)) as conn:
        if outer_transaction:
            conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="injected rename failure"):
            drop_retired_fts_v2(conn.cursor(factory=FailingCursor))
        assert conn.in_transaction is outer_transaction
        if outer_transaction:
            conn.rollback()
    assert _store_snapshot(v2_db) == before


def test_an_unrecognised_messages_fts_v2_table_is_left_alone(tmp_path):
    """Only the FTS5 index that release built is retired: a table that merely carries the name keeps its rows."""
    db_path = tmp_path / "state.db"
    StoreHolder(db_path=db_path).close()
    with contextlib.closing(sqlite3.connect(db_path)) as raw, raw:
        raw.execute("CREATE TABLE messages_fts_v2 (note TEXT)")
        raw.execute("INSERT INTO messages_fts_v2 VALUES ('kept')")
        raw.execute("INSERT INTO state_meta (key, value) VALUES ('fts_v2_ready', '1')")
    before = _store_snapshot(db_path)

    StoreHolder(db_path=db_path).close()
    assert _store_snapshot(db_path) == before
    with contextlib.closing(sqlite3.connect(db_path)) as raw:
        assert raw.execute("SELECT note FROM messages_fts_v2").fetchall() == [("kept",)]
        assert not _staged_v2_shadows(raw)
