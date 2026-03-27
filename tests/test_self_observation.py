from __future__ import annotations

from importlib import import_module
import json
from pathlib import Path
import sqlite3
from typing import cast
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
            return_value={"root": tmp_path, "refresh": {"refreshed": True, "reason": "refreshed", "duration_ms": 12, "dest_size_bytes": 1024}},
        ),
        patch("syke.llm.backends.pi_synthesis.validate_workspace", return_value={"valid": True, "issues": []}),
        patch("syke.llm.backends.pi_synthesis.get_pending_event_count", return_value=(4, "evt-1")),
        patch("syke.llm.backends.pi_synthesis._load_skill_prompt", return_value="synthesize"),
        patch("syke.runtime.get_pi_runtime", side_effect=RuntimeError("not started")),
        patch("syke.runtime.start_pi_runtime", return_value=fake_runtime),
        patch("syke.llm.backends.pi_synthesis._validate_cycle_output", return_value={"valid": True, "issues": [], "stats": {}}),
        patch("syke.llm.backends.pi_synthesis._sync_memex_to_db", return_value=True),
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
    assert complete_content["tool_name_counts"] == {"read": 1, "bash": 1}
    assert cast(int, complete_content["duration_ms"]) >= 0
    tool_use_rows = _rows_for(db, "synthesis.tool_use")
    assert len(tool_use_rows) == 2


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
    )
    fake_runtime = SimpleNamespace(
        is_alive=True,
        new_session=Mock(return_value={}),
        prompt=lambda prompt, timeout, on_event=None: fake_result,
        status=lambda: {"pid": 4321, "uptime_s": 9.5, "session_count": 3, "last_start_ms": 25},
    )
    memex_path = tmp_path / "memex.md"

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
        patch("syke.llm.backends.pi_ask.MEMEX_PATH", memex_path),
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
    fake_runtime.new_session.assert_called_once_with()

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
    assert complete_content["tool_name_counts"] == {"grep": 1, "read": 1}
    assert complete_content["ipc_fallback"] is True

    tool_use_rows = _rows_for(db, "ask.tool_use")
    assert len(tool_use_rows) == 2


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
    from syke.models import Event
    from syke.observe.runtime import SenseWriter
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
