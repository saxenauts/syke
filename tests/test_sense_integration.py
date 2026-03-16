from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Thread
from typing import cast

from syke.db import SykeDB
from syke.ingestion.descriptor import HarnessDescriptor
from syke.models import Event
from syke.sense.watcher import SenseWatcher
from syke.sense.writer import SenseWriter

TEST_USER_ID = "sense-e2e-user"
TEST_SOURCE = "sense-e2e"


class _RecordAdapter:
    def __init__(self, writer: SenseWriter):
        self._writer: SenseWriter = writer
        self._session_sequence: dict[str, int] = {}

    def enqueue(self, event: object) -> None:
        if isinstance(event, Event):
            self._writer.enqueue(event)
            return
        if not isinstance(event, dict):
            return
        parsed = self._parse_record(cast(dict[str, object], cast(object, event)))
        if parsed is not None:
            self._writer.enqueue(parsed)

    def _parse_record(self, record: dict[str, object]) -> Event | None:
        session_id = record.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None

        sequence_index = self._session_sequence.get(session_id, 0)
        self._session_sequence[session_id] = sequence_index + 1

        role_raw = record.get("type")
        role = "assistant"
        if role_raw == "human":
            role = "user"
        elif role_raw == "assistant":
            role = "assistant"

        return Event(
            user_id="",
            source=TEST_SOURCE,
            timestamp=_parse_timestamp(record.get("timestamp")),
            event_type="turn",
            title=_extract_text(record)[:120] or session_id,
            content=_extract_text(record),
            external_id=f"{TEST_SOURCE}:{session_id}:{sequence_index}",
            session_id=session_id,
            sequence_index=sequence_index,
            role=role,
            source_event_type=role_raw if isinstance(role_raw, str) else None,
        )


def _parse_timestamp(raw: object) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(tz=UTC)


def _extract_text(record: dict[str, object]) -> str:
    message_raw = record.get("message")
    if not isinstance(message_raw, Mapping):
        return ""
    message = cast(Mapping[str, object], message_raw)

    content_raw = message.get("content")
    if not isinstance(content_raw, list):
        return ""
    content = cast(list[object], content_raw)

    chunks: list[str] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        block_map = cast(Mapping[str, object], block)
        if block_map.get("type") != "text":
            continue
        text = block_map.get("text")
        if isinstance(text, str) and text:
            chunks.append(text)
    return "\n".join(chunks)


def _make_descriptor(path: Path) -> HarnessDescriptor:
    return HarnessDescriptor.model_validate(
        {
            "spec_version": 1,
            "source": TEST_SOURCE,
            "format_cluster": "jsonl",
            "status": "stub",
            "discover": {"roots": [{"path": str(path)}]},
        }
    )


def _jsonl_line(session_id: str, text: str, ts: str) -> str:
    return json.dumps(
        {
            "type": "human",
            "session_id": session_id,
            "timestamp": ts,
            "message": {"content": [{"type": "text", "text": text}]},
        }
    )


def _append_line(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        _ = handle.write(f"{line}\n")
        handle.flush()
        os.fsync(handle.fileno())


def _await_count(db: SykeDB, session_id: str, expected: int, timeout_s: float = 5.0) -> int:
    deadline = time.monotonic() + timeout_s
    count = 0
    while time.monotonic() < deadline:
        count = cast(
            int,
            db.conn.execute(
                "SELECT COUNT(*) FROM events WHERE user_id = ? AND source = ? AND session_id = ?",
                (TEST_USER_ID, TEST_SOURCE, session_id),
            ).fetchone()[0],
        )
        if count >= expected:
            return count
        time.sleep(0.1)
    return count


def _start_pipeline(tmp_path: Path) -> tuple[SykeDB, SenseWriter, SenseWatcher]:
    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()

    db = SykeDB(tmp_path / "sense-integration.db")
    writer = SenseWriter(db, TEST_USER_ID, flush_interval_s=0.02, max_batch_size=100)
    writer.start()

    adapter = _RecordAdapter(writer)
    watcher = SenseWatcher(
        [_make_descriptor(watched_dir)], cast(SenseWriter, cast(object, adapter))
    )
    watcher.start()

    time.sleep(0.2)
    return db, writer, watcher


def test_e2e_write_line_to_event_in_5s(tmp_path: Path) -> None:
    db, writer, watcher = _start_pipeline(tmp_path)
    try:
        session_id = "single-session"
        session_file = tmp_path / "watched" / f"{session_id}.jsonl"

        _append_line(session_file, _jsonl_line(session_id, "prime", "2026-03-16T00:00:00Z"))
        _append_line(session_file, _jsonl_line(session_id, "hello", "2026-03-16T00:00:01Z"))

        first_count = _await_count(db, session_id, expected=1, timeout_s=10.0)
        assert first_count >= 1, "Event not captured within 10 seconds"

        baseline = cast(
            int,
            db.conn.execute(
                "SELECT COUNT(*) FROM events WHERE user_id = ? AND source = ? AND session_id = ?",
                (TEST_USER_ID, TEST_SOURCE, session_id),
            ).fetchone()[0],
        )

        for idx in range(10):
            _append_line(
                session_file,
                _jsonl_line(session_id, f"burst-{idx}", f"2026-03-16T00:00:{idx + 2:02d}Z"),
            )

        after_burst = _await_count(db, session_id, expected=baseline + 10, timeout_s=5.0)
        assert after_burst - baseline == 10
    finally:
        watcher.stop()
        writer.stop()
        db.close()


def test_e2e_concurrent_sessions(tmp_path: Path) -> None:
    db, writer, watcher = _start_pipeline(tmp_path)
    try:
        session_a = "session-a"
        session_b = "session-b"
        file_a = tmp_path / "watched" / f"{session_a}.jsonl"
        file_b = tmp_path / "watched" / f"{session_b}.jsonl"

        _append_line(file_a, _jsonl_line(session_a, "prime-a", "2026-03-16T00:10:00Z"))
        _append_line(file_b, _jsonl_line(session_b, "prime-b", "2026-03-16T00:10:00Z"))
        time.sleep(0.35)

        barrier = Barrier(2)

        def _write(path: Path, session_id: str, text: str, ts: str) -> None:
            _ = barrier.wait(timeout=2)
            _append_line(path, _jsonl_line(session_id, text, ts))
            time.sleep(0.05)
            _append_line(path, _jsonl_line(session_id, f"{text}-followup", "2026-03-16T00:10:02Z"))

        writer_a = Thread(
            target=_write,
            args=(file_a, session_a, "hello-a", "2026-03-16T00:10:01Z"),
            daemon=True,
        )
        writer_b = Thread(
            target=_write,
            args=(file_b, session_b, "hello-b", "2026-03-16T00:10:01Z"),
            daemon=True,
        )
        writer_a.start()
        writer_b.start()
        writer_a.join(timeout=2)
        writer_b.join(timeout=2)

        assert not writer_a.is_alive()
        assert not writer_b.is_alive()

        deadline = time.monotonic() + 5.0
        session_ids: set[str] = set()
        while time.monotonic() < deadline:
            rows = cast(
                list[tuple[str | None]],
                db.conn.execute(
                    "SELECT DISTINCT session_id FROM events WHERE user_id = ? AND source = ?",
                    (TEST_USER_ID, TEST_SOURCE),
                ).fetchall(),
            )
            session_ids = {session_id for (session_id,) in rows if session_id is not None}
            if session_a in session_ids and session_b in session_ids:
                break
            time.sleep(0.1)

        assert {session_a, session_b}.issubset(session_ids)
    finally:
        watcher.stop()
        writer.stop()
        db.close()
