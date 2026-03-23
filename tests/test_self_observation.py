from __future__ import annotations

from importlib import import_module
import json
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

from syke.daemon.daemon import SykeDaemon
from syke.db import SykeDB
from syke.memory.synthesis import synthesize
from syke.sync import run_sync

self_observe = import_module("syke.observe.trace")


def _rows_for(db: SykeDB, event_type: str) -> list[dict[str, object]]:
    rows = db.conn.execute(
        "SELECT * FROM events WHERE source = 'syke' AND event_type = ? ORDER BY timestamp ASC",
        (event_type,),
    ).fetchall()
    parsed: list[dict[str, object]] = []
    for row in rows:
        parsed.append(
            {
                **dict(row),
                "content": json.loads(row["content"]),
                "extras": json.loads(row["extras"]),
            }
        )
    return parsed


def test_syke_observer_records_event(db: SykeDB, user_id: str) -> None:
    observer = self_observe.SykeObserver(db, user_id)

    observer.record("health.check", {"status": "ok", "duration_ms": 12}, run_id="run-1")

    rows = _rows_for(db, "health.check")
    assert len(rows) == 1
    assert rows[0]["content"] == {"duration_ms": 12, "status": "ok"}
    assert rows[0]["extras"] == {"observer_depth": 0, "run_id": "run-1"}
    assert rows[0]["duration_ms"] == 12


def test_correct_source(db: SykeDB, user_id: str) -> None:
    self_observe.SykeObserver(db, user_id).record("health.check")

    row = db.conn.execute("SELECT source FROM events LIMIT 1").fetchone()
    assert row is not None
    assert row["source"] == "syke"


def test_unique_external_id(db: SykeDB, user_id: str) -> None:
    observer = self_observe.SykeObserver(db, user_id)

    observer.record("health.check")
    observer.record("health.check")

    rows = db.conn.execute(
        "SELECT external_id FROM events WHERE source = 'syke' ORDER BY timestamp ASC"
    ).fetchall()
    external_ids = [row["external_id"] for row in rows]
    assert len(external_ids) == 2
    assert len(set(external_ids)) == 2


def test_synthesis_emits_self_obs(db: SykeDB, user_id: str) -> None:
    expected = {
        "status": "ok",
        "cost_usd": 1.25,
        "num_turns": 2,
        "memex_updated": True,
        "events_count": 4,
    }

    with patch(
        "syke.memory.synthesis._run_synthesis_with_timeout",
        new=AsyncMock(return_value=expected),
    ):
        result = synthesize(db, user_id, force=True)

    assert result == expected
    assert len(_rows_for(db, "synthesis.start")) == 1
    complete = _rows_for(db, "synthesis.complete")
    assert len(complete) == 1
    complete_content = cast(dict[str, object], complete[0]["content"])
    assert complete_content["cost_usd"] == 1.25
    assert complete_content["events_count"] == 4
    assert cast(int, complete_content["duration_ms"]) >= 0


def test_ingestion_emits_self_obs(db: SykeDB, user_id: str) -> None:
    with (
        patch("syke.sync.sync_source", return_value=3),
        patch("syke.sync._run_memory_synthesis"),
        patch("syke.db.SykeDB.get_sources", return_value=["github"]),
        patch("syke.distribution.context_files.distribute_memex", return_value=None),
        patch("syke.memory.memex.get_memex_for_injection", return_value=""),
        patch("syke.distribution.harness.install_all", return_value={}),
    ):
        total_new, synced = run_sync(db, user_id)

    assert (total_new, synced) == (3, ["github"])
    assert len(_rows_for(db, "ingestion.start")) == 1
    complete = _rows_for(db, "ingestion.complete")
    assert len(complete) == 1
    complete_content = cast(dict[str, object], complete[0]["content"])
    assert complete_content["events_count"] == 3
    assert cast(int, complete_content["duration_ms"]) >= 0


def test_daemon_cycle_emits_self_obs(tmp_path: Path, user_id: str) -> None:
    db_path = tmp_path / "daemon.db"
    with SykeDB(db_path):
        pass

    daemon = SykeDaemon(user_id, interval=1)
    with (
        patch("syke.config.user_db_path", return_value=db_path),
        patch("syke.sync.run_sync", return_value=(2, ["github"])),
        patch("syke.version_check.check_update_available", return_value=(False, None)),
    ):
        daemon._sync_cycle()

    with SykeDB(db_path) as db:
        assert len(_rows_for(db, "daemon.cycle.start")) == 1
        complete = _rows_for(db, "daemon.cycle.complete")
        assert len(complete) == 1
        complete_content = cast(dict[str, object], complete[0]["content"])
        assert complete_content["events_count"] == 2
        assert cast(int, complete_content["duration_ms"]) >= 0


def test_watcher_emits_start_event(db: SykeDB, user_id: str, tmp_path: Path) -> None:
    from syke.observe.descriptor import (
        HarnessDescriptor,
        DiscoverConfig,
        DiscoverRoot,
        SessionConfig,
        TurnConfig,
        TurnMatchConfig,
    )
    from syke.observe.watcher import SenseWatcher
    from syke.observe.writer import SenseWriter

    observer = self_observe.SykeObserver(db, user_id)
    writer = SenseWriter(db, user_id)

    root = tmp_path / "test_root"
    root.mkdir()

    descriptor = HarnessDescriptor(
        spec_version=1,
        source="test-source",
        format_cluster="jsonl",
        discover=DiscoverConfig(
            roots=[DiscoverRoot(path=str(root))],
        ),
        session=SessionConfig(
            scope="file",
            id_field="session_id",
        ),
        turn=TurnConfig(
            role_field="role",
            content_parser="extract_text_content",
            timestamp_field="timestamp",
        ),
    )

    watcher = SenseWatcher([descriptor], writer, syke_observer=observer)
    watcher.start()

    rows = _rows_for(db, "sense.watcher.start")
    assert len(rows) == 1
    content = cast(dict[str, object], rows[0]["content"])
    assert content["paths"] == [str(root)]

    watcher.stop()


def test_writer_emits_batch_event(db: SykeDB, user_id: str) -> None:
    from syke.models import Event
    from syke.observe.writer import SenseWriter
    from datetime import UTC, datetime
    from uuid_extensions import uuid7

    observer = self_observe.SykeObserver(db, user_id)
    writer = SenseWriter(db, user_id, observer=observer)
    writer.start()

    event = Event(
        id=str(uuid7()),
        user_id=user_id,
        source="test",
        timestamp=datetime.now(UTC),
        event_type="test.event",
        title="Test Event",
        content="test content",
        metadata={},
        ingested_at=datetime.now(UTC),
        external_id=f"test:{uuid7()}",
    )

    writer.enqueue(event)
    writer.stop()

    rows = _rows_for(db, "sense.batch.flushed")
    assert len(rows) >= 1
    batch_event = rows[0]
    content = cast(dict[str, object], batch_event["content"])
    assert cast(int, content["count"]) >= 1
    assert cast(int, content["duration_ms"]) >= 0
