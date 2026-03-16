"""5.4 — Multi-Harness Timeline (O1 timeline, O3 taxonomy).

Proves: Events from Claude Code (JSONL) + Codex (JSONL) ingested into
the same DB produce events from distinct sources with correct
interleaving by timestamp.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from syke.db import SykeDB
from syke.ingestion.claude_code import ClaudeCodeAdapter
from syke.ingestion.codex import CodexAdapter
from tests.sandbox.helpers import write_claude_code_session, write_codex_session

SANDBOX_USER = "sandbox-user"

CC_TURNS = [
    {"role": "user", "text": "Fix the memory leak in the daemon process."},
    {"role": "assistant", "text": "Found the leak in the file watcher thread pool."},
    {"role": "user", "text": "Apply the fix and add a regression test."},
]

CODEX_TURNS = [
    {"role": "user", "text": "Generate a REST API for the user settings module."},
    {"role": "assistant", "text": "Created endpoints for GET/PUT /api/settings."},
    {"role": "user", "text": "Add rate limiting to the endpoints."},
    {"role": "assistant", "text": "Rate limiting added at 100 req/min per user."},
]


def _setup(tmp_path):
    db = SykeDB(tmp_path / "multi.db")
    home = tmp_path / "sandbox"
    home.mkdir()
    (home / ".claude" / "projects").mkdir(parents=True)
    (home / ".codex" / "sessions").mkdir(parents=True)

    ts_cc = datetime(2026, 3, 16, 10, 0, 0, tzinfo=UTC)
    ts_codex = datetime(2026, 3, 16, 10, 0, 30, tzinfo=UTC)

    write_claude_code_session(home, "cc-session", CC_TURNS, start_time=ts_cc)
    write_codex_session(home, "codex-session", CODEX_TURNS, start_time=ts_codex)

    with patch.dict("os.environ", {"HOME": str(home)}):
        cc_adapter = ClaudeCodeAdapter(db, SANDBOX_USER)
        cc_adapter.ingest()

        codex_adapter = CodexAdapter(db, SANDBOX_USER)
        codex_adapter.ingest()

    return db


def test_multiple_sources(tmp_path):
    db = _setup(tmp_path)
    sources = db.conn.execute(
        "SELECT DISTINCT source FROM events WHERE user_id = ?", (SANDBOX_USER,)
    ).fetchall()
    source_set = {r[0] for r in sources}
    assert "claude-code" in source_set
    assert "codex" in source_set
    assert len(source_set) >= 2
    db.close()


def test_timeline_interleaving(tmp_path):
    db = _setup(tmp_path)
    rows = db.conn.execute(
        "SELECT timestamp, source FROM events WHERE user_id = ? ORDER BY timestamp",
        (SANDBOX_USER,),
    ).fetchall()
    sources_in_order = [r[1] for r in rows]
    assert "claude-code" in sources_in_order
    assert "codex" in sources_in_order
    cc_first_idx = next(i for i, s in enumerate(sources_in_order) if s == "claude-code")
    codex_first_idx = next(i for i, s in enumerate(sources_in_order) if s == "codex")
    assert cc_first_idx < codex_first_idx, (
        "Claude Code events should precede Codex events (earlier start_time)"
    )
    db.close()


def test_both_event_types_present(tmp_path):
    db = _setup(tmp_path)
    types = db.conn.execute(
        "SELECT DISTINCT event_type FROM events WHERE user_id = ?", (SANDBOX_USER,)
    ).fetchall()
    type_set = {r[0] for r in types}
    assert "turn" in type_set
    assert "session.start" in type_set or "session" in type_set
    db.close()


def test_no_cross_source_session_ids(tmp_path):
    db = _setup(tmp_path)
    rows = db.conn.execute(
        """SELECT session_id, COUNT(DISTINCT source) as src_count
           FROM events WHERE user_id = ?
           GROUP BY session_id HAVING src_count > 1""",
        (SANDBOX_USER,),
    ).fetchall()
    assert len(rows) == 0, f"Session IDs shared across sources: {rows}"
    db.close()
