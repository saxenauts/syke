from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, override

from syke.db import SykeDB
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn
from syke.observe.runtime import SenseWriter, SQLiteWatcher


class _SQLiteSessionAdapter(ObserveAdapter):
    source: str = "opencode"

    def __init__(
        self,
        db: SykeDB,
        user_id: str,
        source_db_path: Path,
        *,
        failures_before_success: int = 0,
    ):
        super().__init__(db, user_id)
        self.source_db_path: Path = source_db_path
        self.failures_before_success: int = failures_before_success
        self.query_calls: list[float] = []

    @override
    def discover(self) -> list[Path]:
        return [self.source_db_path] if self.source_db_path.exists() else []

    @override
    def iter_sessions(
        self,
        since: float = 0,
        paths: Iterable[Path] | None = None,
    ) -> Iterable[ObservedSession]:
        _ = paths
        self.query_calls.append(since)

        if self.failures_before_success > 0:
            self.failures_before_success -= 1
            raise sqlite3.OperationalError("database is locked")

        conn = sqlite3.connect(f"file:{self.source_db_path}?mode=ro", uri=True)
        try:
            if since > 0:
                rows_raw = conn.execute(
                    """
                    SELECT id, time_updated, prompt
                    FROM session
                    WHERE time_updated >= ?
                    ORDER BY time_updated, id
                    """,
                    (int(since * 1000),),
                ).fetchall()
            else:
                rows_raw = conn.execute(
                    """
                    SELECT id, time_updated, prompt
                    FROM session
                    ORDER BY time_updated, id
                    """
                ).fetchall()

            rows = cast(list[tuple[str, int, str]], rows_raw)
            sessions: list[ObservedSession] = []
            for session_id, updated_ms, prompt in rows:
                ts = datetime.fromtimestamp(updated_ms / 1000, tz=UTC)
                start_ts = datetime.fromtimestamp((updated_ms - 1000) / 1000, tz=UTC)
                sessions.append(
                    ObservedSession(
                        session_id=session_id,
                        source_path=self.source_db_path,
                        start_time=start_ts,
                        end_time=ts,
                        turns=[
                            ObservedTurn(
                                role="user",
                                content=prompt,
                                timestamp=ts,
                            )
                        ],
                    )
                )
            return sessions
        finally:
            conn.close()


def _create_source_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        _ = conn.execute(
            """
            CREATE TABLE session (
                id TEXT PRIMARY KEY,
                time_updated INTEGER NOT NULL,
                prompt TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_session(path: Path, session_id: str, time_updated_ms: int, prompt: str) -> None:
    conn = sqlite3.connect(path)
    try:
        _ = conn.execute(
            "INSERT INTO session (id, time_updated, prompt) VALUES (?, ?, ?)",
            (session_id, time_updated_ms, prompt),
        )
        conn.commit()
    finally:
        conn.close()


def _wait_until(condition: Callable[[], bool], timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("Condition was not met before timeout")


def test_sqlite_watcher_detects_change(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    source_db = tmp_path / "opencode.db"
    _create_source_db(source_db)

    writer = SenseWriter(db, user_id, flush_interval_s=0.01)
    adapter = _SQLiteSessionAdapter(db, user_id, source_db)
    watcher = SQLiteWatcher(source_db, adapter, writer, poll_interval_s=0.05)

    writer.start()
    watcher.start()
    try:
        _insert_session(source_db, "ses-1", 1_700_000_000_000, "first prompt")
        _wait_until(lambda: db.count_events(user_id, "opencode") == 2)
        assert db.count_events(user_id, "opencode") == 2
    finally:
        watcher.stop()
        writer.stop()


def test_sqlite_watcher_incremental(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    source_db = tmp_path / "opencode.db"
    _create_source_db(source_db)
    _insert_session(source_db, "ses-1", 1_700_000_000_000, "first prompt")

    writer = SenseWriter(db, user_id, flush_interval_s=0.01)
    adapter = _SQLiteSessionAdapter(db, user_id, source_db)
    watcher = SQLiteWatcher(source_db, adapter, writer, poll_interval_s=0.05)

    writer.start()
    watcher.start()
    try:
        _wait_until(lambda: db.count_events(user_id, "opencode") == 2)

        _insert_session(source_db, "ses-2", 1_700_000_001_000, "second prompt")
        _wait_until(lambda: db.count_events(user_id, "opencode") == 4)

        assert db.count_events(user_id, "opencode") == 4
        assert any(since > 0 for since in adapter.query_calls)
    finally:
        watcher.stop()
        writer.stop()


def test_sqlite_watcher_retry_on_busy(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    source_db = tmp_path / "opencode.db"
    _create_source_db(source_db)
    _insert_session(source_db, "ses-1", 1_700_000_000_000, "first prompt")

    writer = SenseWriter(db, user_id, flush_interval_s=0.01)
    adapter = _SQLiteSessionAdapter(db, user_id, source_db, failures_before_success=2)
    watcher = SQLiteWatcher(source_db, adapter, writer, poll_interval_s=0.05)

    writer.start()
    watcher.start()
    try:
        _wait_until(lambda: db.count_events(user_id, "opencode") == 2)
        assert len(adapter.query_calls) >= 3
        assert db.count_events(user_id, "opencode") == 2
    finally:
        watcher.stop()
        writer.stop()


def test_sqlite_watcher_restores_state_after_restart(
    tmp_path: Path, db: SykeDB, user_id: str
) -> None:
    source_db = tmp_path / "opencode.db"
    state_path = tmp_path / "watcher-state.json"
    _create_source_db(source_db)
    _insert_session(source_db, "ses-1", 1_700_000_000_000, "first prompt")

    writer = SenseWriter(db, user_id, flush_interval_s=0.01)
    adapter = _SQLiteSessionAdapter(db, user_id, source_db)
    watcher = SQLiteWatcher(
        source_db,
        adapter,
        writer,
        poll_interval_s=0.05,
        state_path=state_path,
    )

    writer.start()
    watcher.start()
    try:
        _wait_until(lambda: db.count_events(user_id, "opencode") == 2)
    finally:
        watcher.stop()
        writer.stop()

    writer2 = SenseWriter(db, user_id, flush_interval_s=0.01)
    adapter2 = _SQLiteSessionAdapter(db, user_id, source_db)
    watcher2 = SQLiteWatcher(
        source_db,
        adapter2,
        writer2,
        poll_interval_s=0.05,
        state_path=state_path,
    )

    writer2.start()
    watcher2.start()
    try:
        _wait_until(lambda: bool(adapter2.query_calls))
        assert db.count_events(user_id, "opencode") == 2
        assert adapter2.query_calls[0] > 0

        _insert_session(source_db, "ses-2", 1_700_000_001_000, "second prompt")
        _wait_until(lambda: db.count_events(user_id, "opencode") == 4)

        assert db.count_events(user_id, "opencode") == 4
    finally:
        watcher2.stop()
        writer2.stop()


def test_sqlite_watcher_does_not_drop_burst_when_writer_queue_is_small(
    tmp_path: Path, db: SykeDB, user_id: str
) -> None:
    source_db = tmp_path / "opencode.db"
    _create_source_db(source_db)
    for i in range(20):
        _insert_session(
            source_db,
            f"ses-{i}",
            1_700_000_000_000 + (i * 1000),
            f"prompt-{i}",
        )

    writer = SenseWriter(
        db,
        user_id,
        flush_interval_s=10.0,
        max_batch_size=1,
        max_queue_size=1,
    )
    adapter = _SQLiteSessionAdapter(db, user_id, source_db)
    watcher = SQLiteWatcher(source_db, adapter, writer, poll_interval_s=0.05)

    writer.start()
    watcher.start()
    try:
        _wait_until(lambda: db.count_events(user_id, "opencode") == 40)
        assert db.count_events(user_id, "opencode") == 40
    finally:
        watcher.stop()
        writer.stop()


def test_sqlite_watcher_stop_completes_when_backpressure_blocks_producer(
    tmp_path: Path, db: SykeDB, user_id: str
) -> None:
    source_db = tmp_path / "opencode.db"
    _create_source_db(source_db)
    for i in range(3):
        _insert_session(
            source_db,
            f"ses-{i}",
            1_700_000_000_000 + (i * 1000),
            f"prompt-{i}",
        )

    writer = SenseWriter(
        db,
        user_id,
        flush_interval_s=10.0,
        max_batch_size=1,
        max_queue_size=1,
    )
    flush_started = threading.Event()
    release_flush = threading.Event()
    original_flush = writer._flush_batch

    def slow_flush(writer_db: SykeDB, batch: list[object]) -> None:
        flush_started.set()
        assert release_flush.wait(timeout=2.0)
        original_flush(writer_db, cast(list, batch))

    writer._flush_batch = slow_flush  # type: ignore[method-assign]
    adapter = _SQLiteSessionAdapter(db, user_id, source_db)
    watcher = SQLiteWatcher(source_db, adapter, writer, poll_interval_s=0.05)

    writer.start()
    watcher.start()
    stop_errors: list[Exception] = []
    stop_done = threading.Event()

    def stop_watcher() -> None:
        try:
            watcher.stop(timeout=2.0)
        except Exception as exc:  # pragma: no cover - assertion below captures failures
            stop_errors.append(exc)
        finally:
            stop_done.set()

    stop_thread = threading.Thread(target=stop_watcher, name="sqlite-watcher-stop-test")
    try:
        _wait_until(flush_started.is_set)
        stop_thread.start()
        time.sleep(0.1)
        assert not stop_done.is_set()

        release_flush.set()
        stop_thread.join(timeout=3.0)

        assert stop_done.is_set()
        assert stop_errors == []
    finally:
        release_flush.set()
        if stop_thread.is_alive():
            stop_thread.join(timeout=3.0)
        if watcher._thread is not None:
            watcher.stop(timeout=2.0)
        writer.stop(timeout=2.0)
