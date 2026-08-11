"""SessionStore.deactivate_messages, the reversible refusal mask (fork #58 refusal-hop-clean-fork).

The model router hides a refused assistant row by flipping it inactive on the store that owns the
session (``messages.deactivate_message``, the soft-delete /undo uses). ``compacted`` is left alone,
so a masked row keeps the rewind meaning (``active=0, compacted=0``) instead of turning into a
compaction archive, and only rows that were active count as masked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.config import GatewayConfig
from gateway.session import SessionStore
from persistence.conversation.messages import TranscriptRead, TranscriptView
from tests.fixtures.storage_holder import raw_holder
from tests.fixtures.storage_seed import seed_messages, seed_session


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    s = SessionStore(sessions_dir=tmp_path / "sessions", config=GatewayConfig())
    yield s
    s.close_all_db_handles()


def _rows(store, sid):
    rows = store.persistence.messages.read_transcript(sid, TranscriptRead(view=TranscriptView.ALL))
    return {row.id: row.as_wire_row() for row in rows}


def test_deactivate_flips_only_listed_active_rows_and_keeps_compacted(store):
    sid = seed_session(store.persistence, "s1", "telegram")
    first, compacted, unlisted, already_inactive = seed_messages(
        store.persistence, sid,
        {"role": "assistant", "content": "first refusal"},
        {"role": "assistant", "content": "archived-looking row"},
        {"role": "user", "content": "keep visible"},
        {"role": "assistant", "content": "already hidden"},
    )

    def _shape(conn):
        conn.execute("UPDATE messages SET compacted = 1 WHERE id = ?", (compacted,))
        conn.execute("UPDATE messages SET active = 0 WHERE id = ?", (already_inactive,))

    raw_holder(store.persistence)._execute_write(_shape)

    assert store.deactivate_messages(sid, [first, compacted, already_inactive]) == 2
    assert store.deactivate_messages(sid, []) == 0
    assert list(store.persistence.messages.active_message_ids(sid)) == [unlisted]

    rows = _rows(store, sid)
    assert (rows[first]["active"], rows[first]["compacted"]) == (0, 0)
    assert (rows[compacted]["active"], rows[compacted]["compacted"]) == (0, 1)
    assert (rows[already_inactive]["active"], rows[already_inactive]["compacted"]) == (0, 0)


def test_deactivate_counts_a_repeated_id_once(store):
    sid = seed_session(store.persistence, "s2", "telegram")
    (row,) = seed_messages(store.persistence, sid, {"role": "assistant", "content": "refusal"})

    assert store.deactivate_messages(sid, [row, row]) == 1
    assert store.deactivate_messages(sid, [row]) == 0
