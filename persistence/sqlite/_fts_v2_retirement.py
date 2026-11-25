"""Retirement of the ``messages_fts_v2`` index an earlier release left behind, run when a writable store opens.

That release indexed messages into ``messages_fts_v2`` with the loadable ``cjk_unicode61`` tokenizer, then
superseded it with ``messages_fts_cjk`` (retired on open by ``_schema.drop_retired_cjk_index``). A store that
stopped mid-transition kept the v2 table, its triggers on ``messages`` and its ``fts_v2_*`` markers. No release
reads v2 and no process loads its tokenizer since the CJK tokenizer left core, so its insert trigger fails every
write to ``messages``. Retirement drops the triggers and markers, removes the table row and renames the shadow
tables into the FTS trash, which ``optimize_fts_storage`` empties in chunks. Named allowlist, never a prefix scan:
a table that only shares the prefix is not retired, and neither is a ``messages_fts_v2`` whose stored definition
is not the FTS5 index that release built.
"""

from __future__ import annotations

import sqlite3

from ._schema import _FTS_TRASH_PREFIX
from ._schema_ddl import _FTS5_SHADOW_SUFFIXES

__all__ = ["drop_retired_fts_v2", "retired_fts_v2_present"]

_RETIRED_V2_TRIGGERS = ("messages_fts_v2_insert", "messages_fts_v2_delete", "messages_fts_v2_update")
_RETIRED_V2_SHADOWS = tuple(f"messages_fts_v2_{suffix}" for suffix in _FTS5_SHADOW_SUFFIXES)
_RETIRED_V2_OBJECTS = ("messages_fts_v2", *_RETIRED_V2_TRIGGERS, *_RETIRED_V2_SHADOWS)
_RETIRED_V2_MARKERS = ("fts_v2_ready", "fts_v2_backfill_snapshot_max", "fts_v2_backfill_next_lo")


def _placeholders(names: tuple[str, ...]) -> str:
    return ",".join("?" for _ in names)


def _v2_table_recognised(cursor: sqlite3.Cursor) -> bool:
    """False when a ``messages_fts_v2`` table exists that is not the FTS5 index that release built."""
    row = cursor.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages_fts_v2'").fetchone()
    if row is None:
        return True
    sql = str(row[0] or "").lower()
    return (sql.startswith("create virtual table") and "using fts5" in sql
            and all(column in sql for column in ("content", "tool_name", "tool_calls")))


def retired_fts_v2_present(cursor: sqlite3.Cursor) -> bool:
    """True when a schema object of the retired ``messages_fts_v2`` index (its table, a trigger or a shadow table)
    is still in the store and the table, if any, is the index that release built."""
    if cursor.execute(
        f"SELECT 1 FROM sqlite_master WHERE name IN ({_placeholders(_RETIRED_V2_OBJECTS)}) LIMIT 1",
        _RETIRED_V2_OBJECTS,
    ).fetchone() is None:
        return False
    return _v2_table_recognised(cursor)


def _retirement_due(cursor: sqlite3.Cursor) -> bool:
    if retired_fts_v2_present(cursor):
        return True
    # Markers alone are inert rows; clear them too unless an unrecognised messages_fts_v2 owns them.
    return cursor.execute(
        f"SELECT 1 FROM state_meta WHERE key IN ({_placeholders(_RETIRED_V2_MARKERS)}) LIMIT 1", _RETIRED_V2_MARKERS,
    ).fetchone() is not None and _v2_table_recognised(cursor)


def drop_retired_fts_v2(cursor: sqlite3.Cursor) -> bool:
    """Retire the ``messages_fts_v2`` index and its markers; the shadow tables go to the FTS trash. Idempotent;
    True when anything was removed. Reuse a caller's transaction without committing it; otherwise own the atomic
    retirement."""
    if not _retirement_due(cursor):
        return False
    owns_transaction = not cursor.connection.in_transaction
    if owns_transaction:
        cursor.execute("BEGIN IMMEDIATE")
    try:
        removed = _stage_retired_fts_v2(cursor)
        if owns_transaction:
            cursor.connection.commit()
        return removed
    except BaseException:
        if owns_transaction:
            cursor.connection.rollback()
        raise


def _stage_retired_fts_v2(cursor: sqlite3.Cursor) -> bool:
    """Recheck after acquiring the write lock: another opener may have finished retirement."""
    if not _retirement_due(cursor):
        return False
    # Triggers first: while they exist every write to messages needs the tokenizer.
    for trigger in _RETIRED_V2_TRIGGERS:
        cursor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    cursor.execute(f"DELETE FROM state_meta WHERE key IN ({_placeholders(_RETIRED_V2_MARKERS)})", _RETIRED_V2_MARKERS)
    # DROP TABLE constructs the virtual table, which needs the tokenizer this process cannot load.
    cursor.execute("PRAGMA writable_schema=ON")
    try:
        cursor.execute("DELETE FROM sqlite_master WHERE type = 'table' AND name = 'messages_fts_v2'")
    finally:
        cursor.execute("PRAGMA writable_schema=RESET")
    # Nothing references the shadows now, so the rename need not re-parse the schema; the modern rename does, and
    # fails beside another index whose tokenizer this build lacks (trigram), as in drop_retired_cjk_index.
    cursor.execute("PRAGMA legacy_alter_table=ON")
    try:
        for shadow in _RETIRED_V2_SHADOWS:
            if cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (shadow,)).fetchone():
                cursor.execute(f"ALTER TABLE {shadow} RENAME TO {_FTS_TRASH_PREFIX}{shadow}")
    finally:
        cursor.execute("PRAGMA legacy_alter_table=OFF")
    return True
