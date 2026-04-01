from __future__ import annotations

import json
import sqlite3
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, Mock, patch

from syke.config import ASK_TIMEOUT
from syke.daemon.daemon import SykeDaemon
from syke.db import SykeDB
from syke.llm.backends.pi_ask import pi_ask
from syke.llm.backends.pi_synthesis import pi_synthesize
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


def test_syke_observer_with_non_pathlike_db_path_uses_fallback_db(user_id: str) -> None:
    db = MagicMock()
    db.db_path = MagicMock()

    observer = self_observe.SykeObserver(db, user_id)
    observer.record("health.check")

    db.insert_event.assert_called_once()


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


def test_synthesis_emits_self_obs(db: SykeDB, user_id: str, tmp_path: Path) -> None:
    events_db = tmp_path / "events.db"
    conn = sqlite3.connect(events_db)
    conn.execute("CREATE TABLE events (id TEXT, user_id TEXT)")
    conn.execute("INSERT INTO events (id, user_id) VALUES (?, ?)", ("evt-4", user_id))
    conn.commit()
    conn.close()

    fake_result = SimpleNamespace(
        ok=True,
        error=None,
        duration_ms=123,
        cost_usd=1.25,
        input_tokens=11,
        output_tokens=7,
        cache_read_tokens=0,
        cache_write_tokens=0,
        provider="azure-openai-responses",
        response_model="gpt-5.4-mini",
        response_id="resp_123",
        stop_reason="stop",
        tool_calls=[{"tool": "read"}, {"tool": "bash"}],
        events=[
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "inspect"},
                        {"type": "toolCall", "name": "bash", "arguments": {"command": "pwd"}},
                        {"type": "text", "text": "done"},
                    ],
                },
            }
        ],
    )
    prompt_mock = Mock(return_value=fake_result)
    fake_runtime = SimpleNamespace(
        new_session=Mock(return_value={}),
        prompt=prompt_mock,
    )

    with (
        patch(
            "syke.llm.backends.pi_synthesis.prepare_workspace",
            return_value={
                "root": tmp_path,
                "refresh": {
                    "refreshed": True,
                    "reason": "refreshed",
                    "duration_ms": 12,
                    "dest_size_bytes": 1024,
                },
            },
        ),
        patch(
            "syke.llm.backends.pi_synthesis.validate_workspace",
            return_value={"valid": True, "issues": []},
        ),
        patch("syke.llm.backends.pi_synthesis.get_pending_event_count", return_value=(4, "evt-1")),
        patch("syke.llm.backends.pi_synthesis._load_skill_prompt", return_value="synthesize"),
        patch("syke.runtime.get_pi_runtime", side_effect=RuntimeError("not started")),
        patch("syke.runtime.start_pi_runtime", return_value=fake_runtime),
        patch(
            "syke.llm.backends.pi_synthesis._validate_cycle_output",
            return_value={"valid": True, "issues": [], "stats": {}},
        ),
        patch(
            "syke.llm.backends.pi_synthesis._sync_memex_to_db",
            return_value={
                "ok": True,
                "updated": True,
                "source": "artifact",
                "artifact_written": False,
            },
        ),
        patch("syke.llm.backends.pi_synthesis.EVENTS_DB", events_db),
    ):
        result = pi_synthesize(db, user_id, force=True)

    assert result["status"] == "completed"
    assert result["backend"] == "pi"
    assert result["cost_usd"] == 1.25
    assert result["events_processed"] == 4
    assert result["num_turns"] == 1
    assert result["transcript"] == [
        {
            "role": "assistant",
            "blocks": [
                {"type": "thinking", "text": "inspect"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "pwd"}},
                {"type": "text", "text": "done"},
            ],
        }
    ]
    prompt_mock.assert_called_once()
    prompt_args, prompt_kwargs = prompt_mock.call_args
    assert prompt_args == ("synthesize",)
    assert prompt_kwargs["new_session"] is True
    assert len(_rows_for(db, "synthesis.start")) == 1
    complete = _rows_for(db, "synthesis.complete")
    assert len(complete) == 1
    complete_content = cast(dict[str, object], complete[0]["content"])
    assert complete_content["cost_usd"] == 1.25
    assert complete_content["events_processed"] == 4
    assert complete_content["tool_calls"] == 2
    assert complete_content["num_turns"] == 1
    assert complete_content["tool_name_counts"] == {"read": 1, "bash": 1}
    assert cast(int, complete_content["duration_ms"]) >= 0
    tool_use_rows = _rows_for(db, "synthesis.tool_use")
    assert len(tool_use_rows) == 2


def test_synthesis_lock_skip_emits_self_obs(db: SykeDB, user_id: str) -> None:
    module = import_module("syke.llm.backends.pi_synthesis")

    with patch.object(
        module,
        "_acquire_synthesis_lock",
        side_effect=module.SynthesisLockUnavailable("busy"),
    ):
        result = pi_synthesize(db, user_id, force=True)

    assert result["status"] == "skipped"
    assert result["reason"] == "locked"
    skipped = _rows_for(db, "synthesis.skipped")
    assert len(skipped) == 1
    skipped_content = cast(dict[str, object], skipped[0]["content"])
    assert skipped_content["reason"] == "locked"


def test_ask_emits_self_obs(db: SykeDB, user_id: str, tmp_path: Path) -> None:
    fake_result = SimpleNamespace(
        ok=True,
        error=None,
        output="Syke is local-first memory.",
        duration_ms=123,
        cost_usd=0.5,
        input_tokens=11,
        output_tokens=7,
        cache_read_tokens=1,
        cache_write_tokens=2,
        provider="azure-openai-responses",
        response_model="gpt-5.4-mini",
        response_id="resp_ask_123",
        stop_reason="stop",
        tool_calls=[{"name": "grep", "input": {"pattern": "memex"}}, {"name": "read"}],
        num_turns=2,
    )
    prompt_mock = Mock(return_value=fake_result)
    fake_runtime = SimpleNamespace(
        is_alive=True,
        new_session=Mock(return_value={}),
        prompt=prompt_mock,
        status=lambda: {"pid": 4321, "uptime_s": 9.5, "session_count": 3, "last_start_ms": 25},
    )

    with (
        patch(
            "syke.llm.backends.pi_ask.prepare_workspace",
            return_value={
                "root": tmp_path,
                "refresh": {
                    "refreshed": True,
                    "reason": "refreshed",
                    "duration_ms": 12,
                    "dest_size_bytes": 1024,
                },
            },
        ),
        patch("syke.llm.backends.pi_ask.get_pi_runtime", return_value=fake_runtime),
        patch("syke.llm.backends.pi_ask.start_pi_runtime", return_value=fake_runtime),
        patch("syke.memory.memex.get_memex_for_injection", return_value="memex context"),
        patch.object(SykeDB, "count_events", return_value=1),
    ):
        answer, metadata = pi_ask(
            db,
            user_id,
            "What is Syke?",
            transport_details={
                "ipc_fallback": True,
                "ipc_error": "socket missing",
                "ipc_attempt_ms": 7,
            },
        )

    assert answer == "Syke is local-first memory."
    assert metadata["backend"] == "pi"
    assert metadata["transport"] == "direct"
    assert metadata["ipc_fallback"] is True
    prompt_mock.assert_called_once()
    prompt_args, prompt_kwargs = prompt_mock.call_args
    assert prompt_args == ("What is Syke?",)
    assert prompt_kwargs["timeout"] == float(ASK_TIMEOUT)
    assert prompt_kwargs["new_session"] is True

    start_rows = _rows_for(db, "ask.start")
    assert len(start_rows) == 1
    start_content = cast(dict[str, object], start_rows[0]["content"])
    assert start_content["transport"] == "direct"
    assert start_content["ipc_fallback"] is True

    complete_rows = _rows_for(db, "ask.complete")
    assert len(complete_rows) == 1
    complete_content = cast(dict[str, object], complete_rows[0]["content"])
    assert complete_content["status"] == "completed"
    assert complete_content["response_id"] == "resp_ask_123"
    assert complete_content["num_turns"] == 2
    assert complete_content["tool_name_counts"] == {"grep": 1, "read": 1}
    assert complete_content["ipc_fallback"] is True

    tool_use_rows = _rows_for(db, "ask.tool_use")
    assert len(tool_use_rows) == 2


def test_ingestion_emits_self_obs(db: SykeDB, user_id: str) -> None:
    with (
        patch("syke.sync.sync_source", return_value=3),
        patch("syke.sync._run_memory_synthesis"),
        patch("syke.db.SykeDB.get_sources", return_value=["github"]),
        patch("syke.distribution.refresh_distribution"),
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
    with SykeDB(db_path) as db:
        db.initialize()
        daemon = SykeDaemon(user_id, interval=1)
        with (
            patch.object(daemon, "_health_check", return_value={"healthy": True}),
            patch.object(daemon, "_heal"),
            patch.object(daemon, "_reconcile", return_value=(2, ["github"])),
            patch.object(
                daemon,
                "_synthesize",
                return_value={"status": "completed", "memex_updated": True},
            ),
            patch.object(daemon, "_distribute"),
        ):
            daemon._daemon_cycle(db)

        assert len(_rows_for(db, "daemon.cycle.start")) == 1
        health = _rows_for(db, "health.check")
        assert len(health) == 1
        assert cast(dict[str, object], health[0]["content"])["healthy"] is True
        complete = _rows_for(db, "daemon.cycle.complete")
        assert len(complete) == 1
        complete_content = cast(dict[str, object], complete[0]["content"])
        assert complete_content["status"] == "completed"
        assert complete_content["events_count"] == 2
        assert complete_content["synthesis_status"] == "completed"
        assert complete_content["memex_updated"] is True
        assert cast(int, complete_content["duration_ms"]) >= 0


def test_watcher_emits_start_event(db: SykeDB, user_id: str, tmp_path: Path) -> None:
    from syke.observe.descriptor import (
        DiscoverConfig,
        DiscoverRoot,
        HarnessDescriptor,
        SessionConfig,
        TurnConfig,
    )
    from syke.observe.runtime import SenseWatcher, SenseWriter

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
    from datetime import UTC, datetime

    from uuid_extensions import uuid7

    from syke.models import Event
    from syke.observe.runtime import SenseWriter

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


def test_memex_fallback_uses_canonical_db_language(db: SykeDB, user_id: str) -> None:
    from datetime import UTC, datetime

    from syke.memory.memex import get_memex_for_injection
    from syke.models import Event, Memory

    db.insert_event(
        Event(
            id="evt-1",
            user_id=user_id,
            source="github",
            timestamp=datetime.now(UTC),
            event_type="commit",
            title="Commit",
            content="Initial commit",
            external_id="github:evt-1",
            ingested_at=datetime.now(UTC),
        )
    )
    db.insert_memory(Memory(id="mem-1", user_id=user_id, content="Project note"))

    result = get_memex_for_injection(db, user_id)
    assert "canonical database" in result
    assert "memory.db" not in result
    assert "events.db" not in result


def test_synthesis_health_prefers_cycle_records(db: SykeDB, user_id: str, tmp_path: Path) -> None:
    from syke.health import synthesis_health

    cycle_id = db.insert_cycle_record(user_id=user_id, cursor_start="evt-0", model="pi")
    db.complete_cycle_record(
        cycle_id=cycle_id,
        status="completed",
        events_processed=4,
        memories_created=2,
        memories_updated=1,
        links_created=3,
        memex_updated=1,
        cost_usd=1.25,
        duration_ms=2400,
    )

    db.log_memory_op(
        user_id,
        "synthesize",
        metadata={
            "events_processed": 99,
            "created": 99,
            "superseded": 99,
            "linked": 99,
            "cost_usd": 9.9,
        },
    )
    (tmp_path / "metrics.jsonl").write_text(
        json.dumps({"operation": "ask", "cost_usd": 99.0}) + "\n",
        encoding="utf-8",
    )

    health = synthesis_health(db, user_id, metrics_dir=tmp_path)
    assert health["events_processed"] == 4
    assert health["created"] == 2
    assert health["superseded"] == 1
    assert health["linked"] == 3
    assert health["memex_updated"] is True
    assert health["cost_usd"] == 1.25
    assert health["total_cost_usd"] == 1.25
    assert health["recent_runs"] == 1
    assert health["last_status"] == "completed"


def test_runtime_health_uses_cycle_records_without_metrics(
    db: SykeDB, user_id: str, tmp_path: Path
) -> None:
    from syke.health import runtime_health

    cycle_id = db.insert_cycle_record(user_id=user_id, cursor_start="evt-2", model="pi")
    db.complete_cycle_record(
        cycle_id=cycle_id,
        status="failed",
        events_processed=5,
        cost_usd=0.3,
        duration_ms=1200,
    )

    health = runtime_health(db, user_id, metrics_dir=tmp_path)
    assert health["recent_runs"] == 0
    assert health["synthesis_runs"] == 1
    assert health["cycle_failed_runs"] == 1
    assert health["cycle_events_processed"] == 5
    assert health["cycle_total_cost_usd"] == 0.3
    assert health["last_operation"] == "synthesis_cycle"
    assert health["assessment"] == "degraded"


def test_metrics_summary_includes_cycle_rollups(tmp_path: Path, user_id: str) -> None:
    from syke.metrics import MetricsTracker

    db_path = tmp_path / "syke.db"
    with SykeDB(db_path) as db:
        cycle_id = db.insert_cycle_record(user_id=user_id, cursor_start="evt-3", model="pi")
        db.complete_cycle_record(
            cycle_id=cycle_id,
            status="completed",
            events_processed=3,
            cost_usd=0.42,
            duration_ms=999,
        )

    with (
        patch("syke.metrics.user_data_dir", return_value=tmp_path),
        patch("syke.config.user_syke_db_path", return_value=db_path),
    ):
        summary = MetricsTracker(user_id).get_summary()

    assert summary["synthesis_cycles_total"] == 1
    assert summary["synthesis_cycles_completed"] == 1
    assert summary["synthesis_cycles_events_processed"] == 3
    assert summary["synthesis_cycles_cost_usd"] == 0.42
    assert summary["last_run"] is not None
    last_run = cast(dict[str, object], summary["last_run"])
    assert last_run["operation"] == "synthesis_cycle"


def test_daemon_metrics_summary_includes_cycle_rollups(tmp_path: Path, user_id: str) -> None:
    from syke.daemon.metrics import MetricsTracker

    db_path = tmp_path / "daemon-syke.db"
    with SykeDB(db_path) as db:
        cycle_id = db.insert_cycle_record(user_id=user_id, cursor_start="evt-4", model="pi")
        db.complete_cycle_record(
            cycle_id=cycle_id,
            status="completed",
            events_processed=2,
            cost_usd=0.2,
            duration_ms=700,
        )

    with (
        patch("syke.daemon.metrics.user_data_dir", return_value=tmp_path),
        patch("syke.config.user_syke_db_path", return_value=db_path),
    ):
        summary = MetricsTracker(user_id).get_summary()

    assert summary["synthesis_cycles_total"] == 1
    assert summary["synthesis_cycles_completed"] == 1
    assert summary["synthesis_cycles_events_processed"] == 2
    assert summary["synthesis_cycles_cost_usd"] == 0.2
    assert summary["last_run"] is not None
