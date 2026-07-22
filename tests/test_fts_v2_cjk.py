"""Tests for the messages_fts_v2 CJK-bigram index (patch fts5-cjk-bigram-index).

Builds the loadable tokenizer from native/fts5_cjk/fts5_cjk.c on the fly;
skips when no C toolchain / sqlite3ext.h is available.
"""

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

import hermes_state
from hermes_state import FTS_V2_SQL, SessionDB

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native" / "fts5_cjk" / "fts5_cjk.c"


@pytest.fixture(scope="session")
def cjk_so(tmp_path_factory):
    if shutil.which("gcc") is None or not SRC.exists():
        pytest.skip("no C toolchain / tokenizer source")
    out = tmp_path_factory.mktemp("fts5cjk") / "libfts5_cjk.so"
    try:
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-O2", str(SRC), "-o", str(out)],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        pytest.skip(f"tokenizer build failed: {e.stderr[:200]}")
    # Loadability probe (extension loading may be disabled in this build).
    probe = sqlite3.connect(":memory:")
    try:
        probe.enable_load_extension(True)
        probe.load_extension(str(out))
    except Exception as e:
        pytest.skip(f"extension loading unavailable: {e}")
    finally:
        probe.close()
    return out


@pytest.fixture()
def db(cjk_so, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(cjk_so))
    monkeypatch.setenv("HERMES_FTS_V2_READ", "1")
    d = SessionDB(db_path=tmp_path / "state.db")
    assert d._fts_cjk_loaded, "tokenizer must load on the writer connection"
    # migration step: table + triggers (idempotent DDL from the module)
    with d._lock:
        d._conn.executescript(FTS_V2_SQL)
    d._fts_v2_ready = d._probe_fts_v2()
    assert d._fts_v2_ready
    d.create_session(session_id="s1", source="cli", model="m")
    d.append_message("s1", role="user", content="웅기가 shared default 프로필을 요청했다")
    d.append_message("s1", role="assistant", content="일본 MCP 후보 우선순위 정리했습니다")
    d.append_message("s1", role="user", content="graphiti daemon looks healthy")
    yield d
    d.close()


def test_two_char_korean_hits_v2(db):
    rows = db.search_messages("웅기", limit=10)
    assert rows and "웅기" in rows[0]["snippet"]
    rows = db.search_messages("일본", limit=10)
    assert rows


def test_mixed_and_ascii_queries(db):
    assert db.search_messages("graphiti", limit=10)
    assert db.search_messages('"shared default" AND 웅기', limit=10)
    assert db.search_messages("우선순위", limit=10)


def test_no_false_positive_across_words(db):
    # 기가/했다 exist inside runs; a bigram crossing a word boundary must not.
    assert db.search_messages("다프로", limit=10) == []


def test_lone_single_cjk_char_routes_legacy(db, monkeypatch):
    called = {"v2": 0}
    orig = db._query_fts_v2

    def spy(*a, **k):
        called["v2"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(db, "_query_fts_v2", spy)
    db.search_messages("가", limit=10)
    assert called["v2"] == 0, "1-char CJK query must stay on the legacy path"


def test_read_flag_off_uses_legacy(db, monkeypatch):
    monkeypatch.setenv("HERMES_FTS_V2_READ", "0")
    # trigram (>=3 chars) still answers on the legacy path
    assert db.search_messages("우선순위", limit=10)


def test_triggers_mirror_updates_and_deletes(db):
    db.append_message("s1", role="user", content="자바스크립트 리팩토링")
    assert db.search_messages("리팩토링", limit=10)
    with db._lock:
        db._conn.execute(
            "UPDATE messages SET content = '파이썬 리라이트' WHERE content LIKE '%리팩토링%'"
        )
    assert db.search_messages("리팩토링", limit=10) == []
    assert db.search_messages("리라이트", limit=10)
    with db._lock:
        db._conn.execute("DELETE FROM messages WHERE content = '파이썬 리라이트'")
    assert db.search_messages("리라이트", limit=10) == []


def test_flag_only_update_does_not_fire_v2_trigger(db):
    """active-flag flips must not re-tokenize (AFTER UPDATE OF scoping)."""
    rows = db.search_messages("웅기", limit=10)
    mid = rows[0]["id"]
    with db._lock:
        before = db._conn.execute(
            "SELECT count(*) FROM messages_fts_v2 WHERE rowid = ?", (mid,)
        ).fetchone()[0]
        db._conn.execute("UPDATE messages SET active = 1 WHERE id = ?", (mid,))
        after = db._conn.execute(
            "SELECT count(*) FROM messages_fts_v2 WHERE rowid = ?", (mid,)
        ).fetchone()[0]
    assert before == after == 1


def test_self_heal_drops_triggers_without_tokenizer(cjk_so, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(cjk_so))
    d = SessionDB(db_path=tmp_path / "heal.db")
    with d._lock:
        d._conn.executescript(FTS_V2_SQL)
    d.close()

    # Reopen WITHOUT the extension: triggers must be dropped, writes must work.
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(tmp_path / "missing.so"))
    d2 = SessionDB(db_path=tmp_path / "heal.db")
    try:
        assert not d2._fts_cjk_loaded
        assert not d2._fts_v2_ready
        with d2._lock:
            trig = d2._conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'messages_fts_v2%'"
            ).fetchone()[0]
        assert trig == 0
        d2.create_session(session_id="w", source="cli", model="m")
        d2.append_message("w", role="user", content="writes still work")
    finally:
        d2.close()


def test_fresh_db_is_v2_native(cjk_so, tmp_path, monkeypatch):
    """A fresh DB with a loadable tokenizer runs on v2 alone (no v1 tables)."""
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(cjk_so))
    monkeypatch.delenv("HERMES_FTS_V2_READ", raising=False)
    d = SessionDB(db_path=tmp_path / "plain.db")
    try:
        assert d._fts_v2_ready  # empty DB → ready marker set at init
        assert not d._fts_v1_present
        with d._lock:
            v1 = d._conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE name IN "
                "('messages_fts','messages_fts_trigram')"
            ).fetchone()[0]
        assert v1 == 0
        d.create_session(session_id="p", source="cli", model="m")
        d.append_message("p", role="user", content="일본 MCP 정리")
        assert d.search_messages("일본 MCP", limit=5)  # default-on v2
        # With no v1 fallback, the off-flag must NOT blind search.
        monkeypatch.setenv("HERMES_FTS_V2_READ", "0")
        assert d.search_messages("일본 MCP", limit=5)
    finally:
        d.close()


def test_no_tokenizer_falls_back_to_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(tmp_path / "missing.so"))
    d = SessionDB(db_path=tmp_path / "legacy.db")
    try:
        assert not d._fts_v2_ready
        d.create_session(session_id="p", source="cli", model="m")
        d.append_message("p", role="user", content="일본 MCP 정리")
        assert d.search_messages("일본 MCP", limit=5)  # legacy trigram/LIKE
    finally:
        d.close()


def test_partial_backfill_not_served(cjk_so, tmp_path, monkeypatch):
    """v2 triggers without the ready marker must not serve reads."""
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(tmp_path / "missing.so"))
    d = SessionDB(db_path=tmp_path / "partial.db")
    d.create_session(session_id="p", source="cli", model="m")
    d.append_message("p", role="user", content="백필 전 메시지")
    d.close()
    # Reopen with the tokenizer: init ensures v2 table+triggers, but the DB
    # is non-empty and unmigrated → no marker → probe must refuse.
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(cjk_so))
    d2 = SessionDB(db_path=tmp_path / "partial.db")
    try:
        assert d2._fts_cjk_loaded
        assert not d2._fts_v2_ready
        assert d2.search_messages("백필", limit=5)  # legacy still answers
    finally:
        d2.close()


def test_runtime_rebuild_covers_v2_only_db(cjk_so, tmp_path, monkeypatch):
    """Write-path corruption self-heal must rebuild the v2 index on a DB
    with no v1 tables (fresh v2-native, or after scripts/fts_v1_drop.py) —
    upstream's rebuild only knows the v1 tables and would return 0 there.
    """
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(cjk_so))
    monkeypatch.delenv("HERMES_FTS_V2_READ", raising=False)
    d = SessionDB(db_path=tmp_path / "v2only.db")
    try:
        assert not d._fts_v1_present
        assert d._fts_maintenance_tables() == d._FTS_TABLES + ("messages_fts_v2",)
        d.create_session(session_id="p", source="cli", model="m")
        d.append_message("p", role="user", content="첫 메시지 graphiti")

        raw = sqlite3.connect(str(tmp_path / "v2only.db"))
        raw.execute(
            "UPDATE messages_fts_v2_data SET block = "
            "X'DEADBEEFDEADBEEFDEADBEEFDEADBEEF'"
        )
        raw.commit()
        raw.close()

        # Before the _fts_maintenance_tables() integration this raised: the
        # rebuild found no v1 tables, returned 0, and the error propagated.
        msg_id = d.append_message("p", role="user", content="heal 일본 MCP")
        assert msg_id is not None
        assert d._fts_runtime_rebuild_attempted
        assert d.search_messages("일본 MCP", limit=5)
    finally:
        d.close()


def test_maintenance_tables_exclude_v2_without_tokenizer(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(tmp_path / "missing.so"))
    d = SessionDB(db_path=tmp_path / "legacy.db")
    try:
        assert d._fts_maintenance_tables() == d._FTS_TABLES
    finally:
        d.close()


def test_repair_dropped_v2_is_not_served_stale(cjk_so, tmp_path, monkeypatch):
    """The offline repair path drops every messages_fts%% object — v1 AND v2 —
    but state_meta (and thus a stale fts_v2_ready marker) survives. Reopening
    a populated DB must NOT serve the freshly recreated empty v2 index off
    the stale marker; it must clear the marker and rebuild v1 so search keeps
    answering until fts_v2_migrate.py reruns."""
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(cjk_so))
    db_path = tmp_path / "repaired.db"
    d = SessionDB(db_path=db_path)
    d.create_session(session_id="p", source="cli", model="m")
    d.append_message("p", role="user", content="복구 전 메시지 graphiti")
    assert d._fts_v2_ready  # fresh v2-native, marker set
    d.close()

    # Simulate the drop-FTS repair: remove the v2 objects, leave state_meta.
    raw = sqlite3.connect(str(db_path))
    raw.enable_load_extension(True)
    raw.load_extension(str(cjk_so))
    for trg in ("messages_fts_v2_insert", "messages_fts_v2_delete",
                "messages_fts_v2_update"):
        raw.execute(f"DROP TRIGGER IF EXISTS {trg}")
    raw.execute("DROP TABLE IF EXISTS messages_fts_v2")
    marker = raw.execute(
        "SELECT value FROM state_meta WHERE key = 'fts_v2_ready'"
    ).fetchone()
    assert marker and marker[0] == "1"  # stale marker survives the repair
    raw.commit()
    raw.close()

    d2 = SessionDB(db_path=db_path)
    try:
        assert not d2._fts_v2_ready  # stale marker was invalidated
        assert d2._fts_v1_present  # v1 recreated + rebuilt from messages
        assert d2.search_messages("graphiti", limit=5)
        with d2._lock:
            marker = d2._conn.execute(
                "SELECT value FROM state_meta WHERE key = 'fts_v2_ready'"
            ).fetchone()
        assert not marker
    finally:
        d2.close()
