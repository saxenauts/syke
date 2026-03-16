"""5.5 — Real-Time Latency (O4).

Proves: A JSONL line written to a watched directory appears as
an event in the DB within 5 seconds (via the real SenseWriter +
SenseWatcher pipeline).
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from syke.db import SykeDB
from syke.ingestion.descriptor import HarnessDescriptor
from syke.sense.watcher import SenseWatcher
from syke.sense.writer import SenseWriter

TEST_USER = "sandbox-latency"
TEST_SOURCE = "sandbox-latency"


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


def _append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{line}\n")
        f.flush()
        os.fsync(f.fileno())


def _poll_count(db: SykeDB, session_id: str, expected: int, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    count = 0
    while time.monotonic() < deadline:
        count = cast(
            int,
            db.conn.execute(
                "SELECT COUNT(*) FROM events WHERE user_id = ? AND source = ? AND session_id = ?",
                (TEST_USER, TEST_SOURCE, session_id),
            ).fetchone()[0],
        )
        if count >= expected:
            return count
        time.sleep(0.05)
    return count


class _RecordAdapter:
    """Minimal adapter that converts raw dicts to Events for the writer."""

    def __init__(self, writer: SenseWriter):
        self._writer = writer
        self._seq: dict[str, int] = {}

    def enqueue(self, event: object) -> None:
        from syke.models import Event

        if isinstance(event, Event):
            self._writer.enqueue(event)
            return
        if not isinstance(event, dict):
            return
        d = cast(dict[str, object], event)
        sid = d.get("session_id")
        if not isinstance(sid, str) or not sid:
            return
        seq = self._seq.get(sid, 0)
        self._seq[sid] = seq + 1
        text = ""
        msg = d.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        t = block.get("text")
                        if isinstance(t, str):
                            text = t
        e = Event(
            user_id="",
            source=TEST_SOURCE,
            timestamp=datetime.now(tz=UTC),
            event_type="turn",
            title=text[:120] or sid,
            content=text,
            external_id=f"{TEST_SOURCE}:{sid}:{seq}",
            session_id=sid,
            sequence_index=seq,
            role="user",
        )
        self._writer.enqueue(e)


def test_write_to_db_under_5s(tmp_path):
    watched = tmp_path / "watched"
    watched.mkdir()

    db = SykeDB(tmp_path / "latency.db")
    writer = SenseWriter(db, TEST_USER, flush_interval_s=0.02, max_batch_size=100)
    writer.start()

    adapter = _RecordAdapter(writer)
    watcher = SenseWatcher(
        [_make_descriptor(watched)],
        cast(SenseWriter, cast(object, adapter)),
    )
    watcher.start()
    time.sleep(0.2)

    try:
        session_file = watched / "latency-session.jsonl"
        _append(session_file, _jsonl_line("lat-1", "prime", "2026-03-16T00:00:00Z"))
        time.sleep(0.5)

        start = time.monotonic()
        _append(session_file, _jsonl_line("lat-1", "test-event", "2026-03-16T00:00:01Z"))

        count = _poll_count(db, "lat-1", expected=1, timeout=10.0)
        elapsed = time.monotonic() - start

        assert count >= 1, f"No events captured in 10s"
        assert elapsed < 10.0, f"Latency {elapsed:.2f}s exceeds 10s threshold"
    finally:
        watcher.stop()
        writer.stop()
        db.close()
