from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from syke.observe.observe import ObserveAdapter, ObservedSession
from syke.observe.writer import SenseWriter

logger = logging.getLogger(__name__)


class SQLiteWatcher:
    def __init__(
        self,
        db_path: Path,
        adapter: ObserveAdapter,
        writer: SenseWriter,
        *,
        poll_interval_s: float = 1.0,
        retry_backoffs_s: tuple[float, ...] = (0.1, 0.5, 2.0),
    ):
        self.db_path: Path = db_path
        self.adapter: ObserveAdapter = adapter
        self.writer: SenseWriter = writer
        self.poll_interval_s: float = poll_interval_s
        self.retry_backoffs_s: tuple[float, ...] = retry_backoffs_s

        self._last_seen_by_db: dict[Path, float] = {db_path: 0.0}
        self._last_mtime: float | None = None
        self._stop_event: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def last_seen(self) -> datetime:
        return datetime.fromtimestamp(self._last_seen_by_db.get(self.db_path, 0.0), tz=UTC)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="syke-sqlite-watcher")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise RuntimeError("SQLiteWatcher stop timed out")
        self._thread = None

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            mtime = self._db_mtime()
            if mtime is not None and mtime != self._last_mtime:
                self._last_mtime = mtime
                sessions = self._query_sessions_with_retry()
                self._process_sessions(sessions)
            _ = self._stop_event.wait(self.poll_interval_s)

    def _db_mtime(self) -> float | None:
        if not self.db_path.exists() or not self.db_path.is_file():
            return None
        return self.db_path.stat().st_mtime

    def _query_sessions_with_retry(self) -> list[ObservedSession]:
        since = self._last_seen_by_db.get(self.db_path, 0.0)
        last_error: sqlite3.OperationalError | None = None

        for attempt, delay in enumerate(self.retry_backoffs_s):
            try:
                return list(self.adapter.iter_sessions(since=since))
            except sqlite3.OperationalError as exc:
                last_error = exc
                if attempt == len(self.retry_backoffs_s) - 1:
                    break
                _ = self._stop_event.wait(delay)

        if last_error is not None:
            logger.warning(
                "SQLite watcher query failed for %s after retries: %s",
                self.db_path,
                last_error,
            )
        return []

    def _process_sessions(self, sessions: Iterable[ObservedSession]) -> None:
        max_seen = self._last_seen_by_db.get(self.db_path, 0.0)
        for session in sessions:
            for event in self._session_to_events(session):
                self.writer.enqueue(event)
            session_seen = (session.end_time or session.start_time).timestamp()
            if session_seen > max_seen:
                max_seen = session_seen
        self._last_seen_by_db[self.db_path] = max_seen

    def _session_to_events(self, session: ObservedSession) -> list[Event]:
        return self.adapter.session_to_events(session)
