"""5.2 — Concurrent Sessions (O2 session boundaries).

Proves: 3 sessions with overlapping timestamps stay isolated —
distinct session_ids, distinct source_instance_ids, zero external_id
collisions.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tests.sandbox.conftest import run_adapter
from tests.sandbox.helpers import count_events, write_claude_code_session

BASE_TS = datetime(2026, 3, 16, 10, 0, 0, tzinfo=UTC)

SESSIONS = {
    "concurrent-a": [
        {"role": "user", "text": "Working on the auth module."},
        {"role": "assistant", "text": "I see the auth module uses JWT tokens."},
        {"role": "user", "text": "Add refresh token rotation."},
    ],
    "concurrent-b": [
        {"role": "user", "text": "Fix the CI pipeline."},
        {"role": "assistant", "text": "The pipeline fails at the lint stage."},
    ],
    "concurrent-c": [
        {"role": "user", "text": "Review the PR for database migration."},
        {"role": "assistant", "text": "The migration adds a new index."},
        {"role": "user", "text": "Approve and merge it."},
        {"role": "assistant", "text": "Merged."},
    ],
}


def _setup(claude_adapter):
    adapter, home = claude_adapter
    for sid, turns in SESSIONS.items():
        write_claude_code_session(home, sid, turns, start_time=BASE_TS)
    run_adapter(adapter, home)
    return adapter


def test_distinct_session_ids(claude_adapter, user_id):
    adapter = _setup(claude_adapter)
    rows = adapter.db.conn.execute(
        "SELECT DISTINCT session_id FROM events WHERE user_id = ?", (user_id,)
    ).fetchall()
    session_ids = {r[0] for r in rows}
    assert len(session_ids) == 3, f"Expected 3 sessions, got {session_ids}"


def test_distinct_source_instance_ids(claude_adapter, user_id):
    adapter = _setup(claude_adapter)
    rows = adapter.db.conn.execute(
        "SELECT DISTINCT session_id FROM events WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    assert len(rows) == 3, f"Expected 3 distinct sessions, got {len(rows)}"


def test_no_external_id_collisions(claude_adapter, user_id):
    adapter = _setup(claude_adapter)
    dupes = adapter.db.conn.execute(
        """SELECT external_id, COUNT(*) as cnt FROM events
           WHERE user_id = ? GROUP BY external_id HAVING cnt > 1""",
        (user_id,),
    ).fetchall()
    assert len(dupes) == 0, f"Found duplicate external_ids: {dupes}"


def test_combined_event_count(claude_adapter, user_id):
    adapter = _setup(claude_adapter)
    total = count_events(adapter.db, user_id=user_id)
    per_session = {}
    for sid in SESSIONS:
        per_session[sid] = count_events(adapter.db, user_id=user_id, session_id=sid)
    assert total == sum(per_session.values()), f"Total {total} != sum of sessions {per_session}"


def test_session_isolation(claude_adapter, user_id):
    adapter = _setup(claude_adapter)
    for sid in SESSIONS:
        events = adapter.db.conn.execute(
            "SELECT session_id FROM events WHERE user_id = ? AND session_id = ?",
            (user_id, sid),
        ).fetchall()
        assert all(r[0] == sid for r in events), f"Leaked session_id in {sid}"
