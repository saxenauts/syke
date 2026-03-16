"""5.7 — Idempotency.

Proves: Running the same adapter.ingest() N times on the same data
produces zero new events after the first run. External_id deduplication
holds across multiple adapters.
"""

from __future__ import annotations

from syke.db import SykeDB
from syke.sense.dynamic_adapter import DynamicAdapter
from tests.sandbox.conftest import _CLAUDE_PARSE_LINE, _CODEX_PARSE_LINE, _write_adapter_to_disk
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

    adapter_dir = _write_adapter_to_disk(tmp_path, "claude-code", _CLAUDE_PARSE_LINE)
    adapter = DynamicAdapter(
        db=db,
        user_id=SANDBOX_USER,
        source_name="claude-code",
        adapter_dir=adapter_dir,
        discover_roots=[home / ".claude"],
    )

    result1 = adapter.ingest()
    baseline = count_events(db, user_id=SANDBOX_USER)
    assert result1.events_count > 0

    for i in range(4):
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

    adapter_dir = _write_adapter_to_disk(tmp_path, "codex", _CODEX_PARSE_LINE)
    adapter = DynamicAdapter(
        db=db,
        user_id=SANDBOX_USER,
        source_name="codex",
        adapter_dir=adapter_dir,
        discover_roots=[home / ".codex"],
    )

    result1 = adapter.ingest()
    baseline = count_events(db, user_id=SANDBOX_USER)
    assert result1.events_count > 0

    for i in range(2):
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

    cc_dir = _write_adapter_to_disk(tmp_path, "claude-code", _CLAUDE_PARSE_LINE)
    cc_adapter = DynamicAdapter(
        db=db,
        user_id=SANDBOX_USER,
        source_name="claude-code",
        adapter_dir=cc_dir,
        discover_roots=[home / ".claude"],
    )
    cc_result = cc_adapter.ingest()

    codex_dir = _write_adapter_to_disk(tmp_path, "codex", _CODEX_PARSE_LINE)
    codex_adapter = DynamicAdapter(
        db=db,
        user_id=SANDBOX_USER,
        source_name="codex",
        adapter_dir=codex_dir,
        discover_roots=[home / ".codex"],
    )
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
