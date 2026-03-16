"""5.7 — Idempotency.

Proves: Running the same adapter.ingest() N times on the same data
produces zero new events after the first run. External_id deduplication
holds across multiple adapters.
"""

from __future__ import annotations

from unittest.mock import patch

from syke.db import SykeDB
from syke.ingestion.claude_code import ClaudeCodeAdapter
from syke.ingestion.codex import CodexAdapter
from tests.sandbox.helpers import (
    count_events,
    write_claude_code_session,
    write_codex_session,
)

SANDBOX_USER = "sandbox-idempotent"

CC_TURNS = [
    {"role": "user", "text": "Implement the caching layer with Redis."},
    {"role": "assistant", "text": "Added Redis cache with TTL-based eviction."},
    {"role": "user", "text": "Add cache invalidation on write."},
    {"role": "assistant", "text": "Cache invalidation wired into the write path."},
]

CODEX_TURNS = [
    {"role": "user", "text": "Create database migration for the users table."},
    {"role": "assistant", "text": "Migration created with proper rollback support."},
]


def test_claude_code_idempotent_5x(tmp_path):
    db = SykeDB(tmp_path / "idem.db")
    home = tmp_path / "sandbox"
    home.mkdir()
    (home / ".claude" / "projects").mkdir(parents=True)
    write_claude_code_session(home, "idem-cc", CC_TURNS)

    adapter = ClaudeCodeAdapter(db, SANDBOX_USER)

    with patch.dict("os.environ", {"HOME": str(home)}):
        result1 = adapter.ingest()

    baseline = count_events(db, user_id=SANDBOX_USER)
    assert result1.events_count > 0

    for i in range(4):
        with patch.dict("os.environ", {"HOME": str(home)}):
            result = adapter.ingest()
        assert result.events_count == 0, f"Run {i + 2} inserted {result.events_count} events"

    final = count_events(db, user_id=SANDBOX_USER)
    assert final == baseline, f"Count changed: {baseline} -> {final}"
    db.close()


def test_codex_idempotent_3x(tmp_path):
    db = SykeDB(tmp_path / "idem-codex.db")
    home = tmp_path / "sandbox"
    home.mkdir()
    (home / ".codex" / "sessions").mkdir(parents=True)
    write_codex_session(home, "idem-codex", CODEX_TURNS)

    adapter = CodexAdapter(db, SANDBOX_USER)

    with patch.dict("os.environ", {"HOME": str(home)}):
        result1 = adapter.ingest()

    baseline = count_events(db, user_id=SANDBOX_USER)
    assert result1.events_count > 0

    for i in range(2):
        with patch.dict("os.environ", {"HOME": str(home)}):
            result = adapter.ingest()
        assert result.events_count == 0, f"Run {i + 2} inserted {result.events_count}"

    db.close()


def test_cross_adapter_no_collision(tmp_path):
    db = SykeDB(tmp_path / "idem-cross.db")
    home = tmp_path / "sandbox"
    home.mkdir()
    (home / ".claude" / "projects").mkdir(parents=True)
    (home / ".codex" / "sessions").mkdir(parents=True)

    write_claude_code_session(home, "cross-cc", CC_TURNS)
    write_codex_session(home, "cross-codex", CODEX_TURNS)

    with patch.dict("os.environ", {"HOME": str(home)}):
        cc_adapter = ClaudeCodeAdapter(db, SANDBOX_USER)
        cc_result = cc_adapter.ingest()

        codex_adapter = CodexAdapter(db, SANDBOX_USER)
        codex_result = codex_adapter.ingest()

    total = count_events(db, user_id=SANDBOX_USER)
    assert total == cc_result.events_count + codex_result.events_count

    dupes = db.conn.execute(
        """SELECT external_id, COUNT(*) FROM events
           WHERE user_id = ? GROUP BY external_id HAVING COUNT(*) > 1""",
        (SANDBOX_USER,),
    ).fetchall()
    assert len(dupes) == 0, f"Cross-adapter external_id collisions: {dupes}"
    db.close()
