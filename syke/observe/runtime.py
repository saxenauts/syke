# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUntypedBaseClass=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportArgumentType=false
"""Real-time observe pipeline: tailing, file watching, batched writing, SQLite polling.

These classes are always started/stopped together by the daemon.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import queue
import sqlite3
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from watchdog.events import FileSystemEvent, FileSystemEventHandler  # type: ignore[reportMissingImports]
from watchdog.observers import Observer  # type: ignore[reportMissingImports]

from syke.db import SykeDB
from syke.models import Event
from syke.observe.adapter import ObserveAdapter, ObservedSession
from syke.observe.content_filter import ContentFilter
from syke.observe.descriptor import HarnessDescriptor

if TYPE_CHECKING:
    from syke.observe.trace import SykeObserver

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JsonlTailer — tails a JSONL file, returns new records since last poll
# ---------------------------------------------------------------------------

READ_CHUNK_SIZE = 64 * 1024
JsonRecord = dict[str, object]


class JsonlTailer:
    def __init__(self, file_path: Path, *, suppress_history: bool = False):
        self.file_path: Path = file_path
        self._suppress_history: bool = suppress_history
        self._offset: int = 0
        self._inode: int | None = None
        self._buffer: bytes = b""
        self._failures: list[str] = []

    def poll(self) -> list[JsonRecord]:
        if not self.file_path.exists():
            return []

        stat = self.file_path.stat()
        inode = stat.st_ino

        if self._inode is None and self._suppress_history:
            self._inode = inode
            self._offset = stat.st_size
            self._buffer = b""
            return []

        if self._inode is None:
            self._inode = inode
        elif inode != self._inode:
            self._inode = inode
            self._offset = 0
            self._buffer = b""
        elif stat.st_size < self._offset:
            self._offset = 0
            self._buffer = b""

        records: list[JsonRecord] = []
        self._failures = []
        with self.file_path.open("rb") as handle:
            _ = handle.seek(self._offset)
            pending = self._buffer

            while True:
                chunk = handle.read(READ_CHUNK_SIZE)
                if not chunk:
                    break

                pending += chunk
                parts = pending.split(b"\n")
                complete_lines = parts[:-1]
                pending = parts[-1]

                for line in complete_lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        decoded = line.decode("utf-8")
                        parsed = cast(object, json.loads(decoded))
                        if isinstance(parsed, dict):
                            records.append(cast(JsonRecord, parsed))
                    except UnicodeDecodeError:
                        self._failures.append(line.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        try:
                            decoded = line.decode("utf-8")
                            self._failures.append(decoded)
                        except UnicodeDecodeError:
                            self._failures.append(line.decode("utf-8", errors="replace"))

            self._buffer = pending
            self._offset = handle.tell()

        return records

    def get_failures(self) -> list[str]:
        """Return list of raw lines that failed to parse since last poll."""
        return list(self._failures)


# ---------------------------------------------------------------------------
# SenseWriter — batched, threaded event writer with dedup + content filtering
# ---------------------------------------------------------------------------

_SENTINEL = object()


class SenseWriter:
    def __init__(
        self,
        db: SykeDB,
        user_id: str,
        *,
        flush_interval_s: float = 0.05,
        max_batch_size: int = 100,
        observer: SykeObserver | None = None,
    ):
        self.db: SykeDB = db
        self.user_id: str = user_id
        self.flush_interval_s: float = flush_interval_s
        self.max_batch_size: int = max_batch_size
        self._queue: queue.Queue[Event | object] = queue.Queue(maxsize=10_000)
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event = threading.Event()
        self._filter: ContentFilter = ContentFilter()
        self._flush_count: int = 0
        self._on_insert_callbacks: list[Callable[[list[Event]], None]] = []
        self._callbacks_lock: threading.Lock = threading.Lock()
        self._observer: SykeObserver | None = observer

    @property
    def flush_count(self) -> int:
        return self._flush_count

    def add_on_insert_callback(self, cb: Callable[[list[Event]], None]) -> None:
        """Register a callback to be invoked when events are inserted."""
        with self._callbacks_lock:
            self._on_insert_callbacks.append(cb)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="syke-sense-writer")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        self._queue.put(_SENTINEL)
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise RuntimeError("SenseWriter stop timed out before drain completed")
        self._thread = None

    def enqueue(self, event: Event) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            logger.warning("SenseWriter queue full, dropping event %s", event.external_id)

    def _run(self) -> None:
        writer_db = SykeDB(self.db.db_path)
        batch: list[Event] = []
        deadline = time.monotonic() + self.flush_interval_s
        try:
            while True:
                timeout = max(0.0, deadline - time.monotonic())
                try:
                    item = self._queue.get(timeout=timeout)
                    if isinstance(item, Event):
                        batch.append(item)
                except queue.Empty:
                    pass

                now = time.monotonic()
                should_flush = False
                if batch and len(batch) >= self.max_batch_size:
                    should_flush = True
                elif batch and now >= deadline:
                    should_flush = True
                elif batch and self._stop_event.is_set() and self._queue.empty():
                    should_flush = True

                if should_flush:
                    self._flush_batch(writer_db, batch)
                    batch = []
                    deadline = time.monotonic() + self.flush_interval_s

                if self._stop_event.is_set() and self._queue.empty() and not batch:
                    break
        finally:
            writer_db.close()

    def _flush_batch(self, writer_db: SykeDB, batch: list[Event]) -> None:
        start_time = time.monotonic()
        inserted_events: list[Event] = []
        with writer_db.transaction():
            for event in batch:
                filtered_content, _ = self._filter.process(event.content, event.title or "")
                if filtered_content is None:
                    continue
                event.content = filtered_content
                event.user_id = self.user_id
                try:
                    _ = writer_db.insert_event(event)
                    inserted_events.append(event)
                except sqlite3.IntegrityError:
                    continue
        self._flush_count += 1

        if self._observer and inserted_events:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            from syke.observe.trace import SENSE_BATCH_FLUSHED
            self._observer.record(
                SENSE_BATCH_FLUSHED,
                {
                    "count": len(inserted_events),
                    "duration_ms": duration_ms,
                },
            )

        with self._callbacks_lock:
            callbacks = list(self._on_insert_callbacks)
        for cb in callbacks:
            try:
                cb(inserted_events)
            except Exception:
                logger.warning("on_insert callback failed", exc_info=True)


# ---------------------------------------------------------------------------
# SenseFileHandler — watchdog handler that tails JSONL files on change
# ---------------------------------------------------------------------------

class SenseFileHandler(FileSystemEventHandler):
    def __init__(
        self,
        writer: SenseWriter,
        *,
        system_name: str | None = None,
        syke_observer: SykeObserver | None = None,
        heal_fn: Callable[[str, list[str]], None] | None = None,
        heal_threshold: int = 5,
    ):
        super().__init__()
        self.writer: SenseWriter = writer
        self._tailers: dict[Path, JsonlTailer] = {}
        self._last_sizes: dict[Path, int] = {}
        self._is_macos: bool = (system_name or platform.system()) == "Darwin"
        self._syke_observer: SykeObserver | None = syke_observer
        self._heal_fn: Callable[[str, list[str]], None] | None = heal_fn
        self._heal_threshold: int = heal_threshold
        self._failure_counts: dict[str, int] = defaultdict(int)
        self._failure_samples: dict[str, list[str]] = defaultdict(list)
        self._healed: set[str] = set()

    def on_modified(self, event: FileSystemEvent) -> None:
        if not self._is_macos:
            return
        file_path = self._event_path(event)
        if file_path is None or not self._should_watch(file_path):
            return
        if not self._size_changed(file_path):
            return
        self._process_file(file_path)

    def on_closed(self, event: FileSystemEvent) -> None:
        if self._is_macos:
            return
        file_path = self._event_path(event)
        if file_path is None or not self._should_watch(file_path):
            return
        self._process_file(file_path)

    def _event_path(self, event: FileSystemEvent) -> Path | None:
        if event.is_directory:
            return None
        return Path(event.src_path)  # type: ignore[arg-type]

    def _should_watch(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in {".jsonl", ".json"} and file_path.exists()

    def _size_changed(self, file_path: Path) -> bool:
        try:
            size = os.stat(file_path).st_size
        except FileNotFoundError:
            return False
        previous_size = self._last_sizes.get(file_path)
        self._last_sizes[file_path] = size
        return previous_size is None or previous_size != size

    def _process_file(self, file_path: Path) -> None:
        tailer = self._tailers.get(file_path)
        if tailer is None:
            tailer = self._create_tailer(file_path)
            self._tailers[file_path] = tailer
            if self._syke_observer:
                from syke.observe.trace import SENSE_FILE_DETECTED
                self._syke_observer.record(
                    SENSE_FILE_DETECTED,
                    {"path": str(file_path)},
                )

        records = tailer.poll()
        for record in records:
            self.writer.enqueue(record)  # type: ignore[arg-type]

        failures = tailer.get_failures()
        source = str(file_path)

        if failures:
            self._failure_counts[source] += len(failures)
            samples = self._failure_samples[source]
            samples.extend(failures)
            self._failure_samples[source] = samples[-50:]
            if (
                self._heal_fn
                and source not in self._healed
                and self._failure_counts[source] >= self._heal_threshold
            ):
                try:
                    self._heal_fn(source, self._failure_samples[source][:20])
                    self._healed.add(source)
                    self._failure_counts.pop(source, None)
                    self._failure_samples.pop(source, None)
                except Exception:
                    logger.warning("heal_fn failed for %s", source, exc_info=True)
        else:
            self._failure_counts.pop(source, None)
            self._failure_samples.pop(source, None)
            self._healed.discard(source)

    def _create_tailer(self, file_path: Path) -> JsonlTailer:
        if self._is_macos:
            return JsonlTailer(file_path, suppress_history=True)
        return JsonlTailer(file_path)


# ---------------------------------------------------------------------------
# SenseWatcher — watchdog Observer wrapper, discovers roots from descriptors
# ---------------------------------------------------------------------------

class ObserverLike(Protocol):
    def schedule(self, *args: object, **kwargs: object) -> object: ...  # type: ignore[override]
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def join(self) -> None: ...


class SenseWatcher:
    def __init__(
        self,
        descriptors: list[HarnessDescriptor],
        writer: SenseWriter,
        *,
        observer: ObserverLike | None = None,
        syke_observer: SykeObserver | None = None,
        heal_fn: Callable[[str, list[str]], None] | None = None,
    ):
        self.descriptors: list[HarnessDescriptor] = descriptors
        self.writer: SenseWriter = writer
        self._observer: ObserverLike = observer or Observer()  # type: ignore[assignment]
        self._handler: SenseFileHandler = SenseFileHandler(
            writer, syke_observer=syke_observer, heal_fn=heal_fn,
        )
        self._syke_observer: SykeObserver | None = syke_observer
        self._started: bool = False

    def start(self) -> None:
        if self._started:
            return
        roots = self._discover_roots()
        for root in roots:
            self._observer.schedule(self._handler, str(root), recursive=True)
        self._observer.start()
        self._started = True
        if self._syke_observer:
            from syke.observe.trace import SENSE_WATCHER_START
            self._syke_observer.record(
                SENSE_WATCHER_START,
                {"paths": [str(p) for p in roots]},
            )

    def stop(self) -> None:
        if not self._started:
            return
        self._observer.stop()
        self._observer.join()
        self._started = False

    def _discover_roots(self) -> list[Path]:
        roots: set[Path] = set()
        for descriptor in self.descriptors:
            if descriptor.format_cluster not in {"jsonl", "json"}:
                continue
            if descriptor.discover is None:
                continue
            for root in descriptor.discover.roots:
                path = Path(root.path).expanduser()
                if path.exists() and path.is_dir():
                    roots.add(path.resolve())
        return sorted(roots)


# ---------------------------------------------------------------------------
# SQLiteWatcher — polls SQLite databases for new sessions
# ---------------------------------------------------------------------------

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
            for event in self.adapter.session_to_events(session):
                self.writer.enqueue(event)
            session_seen = (session.end_time or session.start_time).timestamp()
            if session_seen > max_seen:
                max_seen = session_seen
        self._last_seen_by_db[self.db_path] = max_seen


__all__ = [
    "JsonlTailer",
    "ObserverLike",
    "SQLiteWatcher",
    "SenseFileHandler",
    "SenseWatcher",
    "SenseWriter",
]
