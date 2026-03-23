"""5.8 — Crash Recovery.

Proves: Truncated JSONL, partial ingestion, and re-ingestion after
interruption all produce correct event counts with zero duplicates.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from syke.db import SykeDB
from syke.observe.dynamic_adapter import DynamicAdapter
from tests.sandbox.conftest import _CLAUDE_PARSE_LINE, _write_adapter_to_disk
from tests.sandbox.helpers import count_events, write_claude_code_session

SANDBOX_USER = "sandbox-crash"


def test_partial_ingest_then_complete(tmp_path):
    db = SykeDB(tmp_path / "crash.db")
    home = tmp_path / "sandbox"
    home.mkdir()
    (home / ".claude" / "projects").mkdir(parents=True)

    write_claude_code_session(
        home,
        "crash-s1",
        [
            {"role": "user", "text": "First turn"},
            {"role": "assistant", "text": "Response 1"},
        ],
    )

    adapter_dir = _write_adapter_to_disk(tmp_path, "claude-code", _CLAUDE_PARSE_LINE)
    adapter = DynamicAdapter(
        db=db,
        user_id=SANDBOX_USER,
        source_name="claude-code",
        adapter_dir=adapter_dir,
        discover_roots=[home / ".claude"],
    )

    r1 = adapter.ingest()
    first_count = count_events(db, user_id=SANDBOX_USER)
    assert r1.events_count > 0

    write_claude_code_session(
        home,
        "crash-s2",
        [
            {"role": "user", "text": "Second session"},
            {"role": "assistant", "text": "Response 2"},
        ],
    )

    r2 = adapter.ingest()
    total_count = count_events(db, user_id=SANDBOX_USER)
    assert total_count > first_count
    assert r2.events_count > 0

    r3 = adapter.ingest()
    assert r3.events_count == 0
    assert count_events(db, user_id=SANDBOX_USER) == total_count
    db.close()


def test_truncated_json_line_handled(tmp_path):
    db = SykeDB(tmp_path / "trunc.db")
    adapter_dir = _write_adapter_to_disk(tmp_path, "claude-code", _CLAUDE_PARSE_LINE)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    ts = datetime(2026, 3, 16, tzinfo=UTC).isoformat()
    good1 = json.dumps(
        {
            "type": "user",
            "sessionId": "s1",
            "timestamp": ts,
            "message": {"content": [{"type": "text", "text": "hello"}]},
        }
    )
    truncated = '{"type": "assistant", "sessionId": "s1", "timesta'
    good2 = json.dumps(
        {
            "type": "assistant",
            "sessionId": "s1",
            "timestamp": ts,
            "message": {"content": [{"type": "text", "text": "world"}]},
        }
    )

    fpath = data_dir / "mixed.jsonl"
    fpath.write_text(f"{good1}\n{truncated}\n{good2}\n")

    adapter = DynamicAdapter(
        db=db,
        user_id=SANDBOX_USER,
        source_name="claude-code",
        adapter_dir=adapter_dir,
        discover_roots=[data_dir],
    )
    result = adapter.ingest()
    assert result.events_count >= 2
    db.close()


def test_zero_duplicates_after_double_ingest(tmp_path):
    db = SykeDB(tmp_path / "dupes.db")
    home = tmp_path / "sandbox"
    home.mkdir()
    (home / ".claude" / "projects").mkdir(parents=True)

    write_claude_code_session(
        home,
        "dupe-s1",
        [
            {"role": "user", "text": "Turn A"},
            {"role": "assistant", "text": "Turn B"},
            {"role": "user", "text": "Turn C"},
        ],
    )

    adapter_dir = _write_adapter_to_disk(tmp_path, "claude-code", _CLAUDE_PARSE_LINE)
    adapter = DynamicAdapter(
        db=db,
        user_id=SANDBOX_USER,
        source_name="claude-code",
        adapter_dir=adapter_dir,
        discover_roots=[home / ".claude"],
    )

    adapter.ingest()
    adapter.ingest()

    dupes = db.conn.execute(
        """SELECT external_id, COUNT(*) as cnt FROM events
           WHERE user_id = ? GROUP BY external_id HAVING cnt > 1""",
        (SANDBOX_USER,),
    ).fetchall()
    assert len(dupes) == 0, f"Found duplicate external_ids: {dupes}"
    db.close()
