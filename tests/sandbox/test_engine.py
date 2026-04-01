"""Sandbox engine tests — verify isolation, fixtures, and Sense stack."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from syke.models import Event
from tests.sandbox.conftest import SandboxSense, sandbox_env


def test_sandbox_creates_isolated_db(sandbox_db, user_id):
    event = Event(
        id="test-001",
        user_id=user_id,
        source="test",
        timestamp=datetime(2026, 3, 16, 0, 0, 0, tzinfo=UTC),
        event_type="test.event",
        title="Test Event",
        content="Test content",
        external_id="test:001",
    )
    sandbox_db.insert_event(event)

    count = sandbox_db.conn.execute(
        "SELECT COUNT(*) FROM events WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    assert count == 1, f"Expected 1 event, got {count}"

    source = sandbox_db.conn.execute(
        "SELECT source FROM events WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    assert source == "test", f"Expected source='test', got {source}"


def test_sandbox_fixtures_load():
    fixtures_dir = Path(__file__).parent / "fixtures"
    assert fixtures_dir.exists(), f"Fixtures directory not found: {fixtures_dir}"

    claude_fixture = fixtures_dir / "sample_claude_code.jsonl"
    assert claude_fixture.exists(), f"Claude fixture not found: {claude_fixture}"
    lines = claude_fixture.read_text().strip().split("\n")
    assert len(lines) >= 2, f"Expected at least 2 lines in Claude fixture, got {len(lines)}"
    first = json.loads(lines[0])
    assert "sessionId" in first, f"Claude fixture missing sessionId: {first}"
    assert "type" in first, f"Claude fixture missing type: {first}"

    codex_fixture = fixtures_dir / "sample_codex.jsonl"
    assert codex_fixture.exists(), f"Codex fixture not found: {codex_fixture}"
    lines = codex_fixture.read_text().strip().split("\n")
    assert len(lines) >= 2, f"Expected at least 2 lines in Codex fixture, got {len(lines)}"
    first = json.loads(lines[0])
    assert first.get("type") == "session_meta", (
        f"Codex fixture should start with session_meta: {first}"
    )

    opencode_fixture = fixtures_dir / "sample_opencode.sql"
    assert opencode_fixture.exists(), f"OpenCode fixture not found: {opencode_fixture}"
    sql_content = opencode_fixture.read_text()
    assert "CREATE TABLE" in sql_content, (
        f"OpenCode fixture missing CREATE TABLE: {sql_content[:200]}"
    )
    assert "session" in sql_content.lower(), (
        f"OpenCode fixture missing session table: {sql_content[:200]}"
    )


def test_sandbox_sense_starts_watchers(sandbox_sense: SandboxSense):
    assert sandbox_sense.db is not None
    assert sandbox_sense.writer is not None
    assert sandbox_sense.observer is not None
    assert sandbox_sense.user_id == "sandbox-user"

    sandbox_sense.writer.start()
    try:
        event = Event(
            user_id=sandbox_sense.user_id,
            source="test-watcher",
            timestamp=datetime.now(UTC),
            event_type="test.event",
            title="Watcher Test",
            content="Testing writer",
            external_id="test:watcher:001",
        )
        sandbox_sense.writer.enqueue(event)

        import time

        time.sleep(0.1)

        count = sandbox_sense.db.conn.execute(
            "SELECT COUNT(*) FROM events WHERE source = ?", ("test-watcher",)
        ).fetchone()[0]
        assert count >= 1, f"Expected at least 1 event from writer, got {count}"
    finally:
        sandbox_sense.writer.stop(timeout=2.0)


def test_sandbox_env_sets_db_path(sandbox_db, tmp_path):
    db_path = Path(sandbox_db.db_path)
    with sandbox_env(db_path):
        import os

        assert os.environ.get("SYKE_DB") == str(db_path), (
            f"SYKE_DB not set correctly: {os.environ.get('SYKE_DB')}"
        )

    import os

    assert "SYKE_DB" not in os.environ or os.environ.get("SYKE_DB") != str(db_path), (
        "SYKE_DB should be unset after context exit"
    )


def test_sandbox_observer_records_events(sandbox_sense: SandboxSense):
    sandbox_sense.observer.record("test.observation", {"key": "value"})

    rows = sandbox_sense.db.conn.execute(
        "SELECT * FROM events WHERE source = 'syke' AND event_type = ?", ("test.observation",)
    ).fetchall()
    assert len(rows) >= 1, f"Expected at least 1 self-observation event, got {len(rows)}"
