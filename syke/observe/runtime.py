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
_WATCHER_STATE_LOCK = threading.Lock()


def _default_watcher_state_path(db: object) -> Path:
    db_path = Path(getattr(db, "db_path")).expanduser()
    if db_path.name:
        return db_path.with_name("observe_watchers.json")
    return db_path / "observe_watchers.json"


def _state_key(path: Path) -> str:
    return str(path.expanduser().resolve())


class _WatcherStateStore:
    """Best-effort durable state for warm daemon watchers."""

    def __init__(self, path: Path):
        self.path = path.expanduser()

    def load_jsonl(self, file_path: Path) -> dict[str, int | float | None]:
        payload = self._read()
        state = payload.get("jsonl", {}).get(_state_key(file_path), {})
        return self._normalize_jsonl_state(state)

    def load_all_jsonl(self) -> dict[str, dict[str, int | float | None]]:
        payload = self._read()
        raw_jsonl = payload.get("jsonl", {})
        if not isinstance(raw_jsonl, dict):
            return {}
        result: dict[str, dict[str, int | float | None]] = {}
        for key, state in raw_jsonl.items():
            if not isinstance(key, str):
                continue
            normalized_state = self._normalize_jsonl_state(state)
            if normalized_state:
                result[key] = normalized_state
        return result

    def save_jsonl(
        self,
        file_path: Path,
        *,
        offset: int,
        inode: int | None,
        mtime: float | None = None,
    ) -> None:
        def update(payload: dict[str, object]) -> None:
            jsonl = payload.setdefault("jsonl", {})
            if not isinstance(jsonl, dict):
                payload["jsonl"] = {}
                jsonl = payload["jsonl"]
            entry: dict[str, int | float] = {"offset": max(offset, 0)}
            if inode is not None and inode >= 0:
                entry["inode"] = inode
            if mtime is not None and mtime >= 0:
                entry["mtime"] = float(mtime)
            cast(dict[str, object], jsonl)[_state_key(file_path)] = entry

        self._update(update)

    def load_sqlite(self, db_path: Path) -> dict[str, float]:
        payload = self._read()
        state = payload.get("sqlite", {}).get(_state_key(db_path), {})
        if not isinstance(state, dict):
            return {}
        result: dict[str, float] = {}
        last_seen = state.get("last_seen")
        last_mtime = state.get("last_mtime")
        if isinstance(last_seen, (int, float)) and last_seen >= 0:
            result["last_seen"] = float(last_seen)
        if isinstance(last_mtime, (int, float)) and last_mtime >= 0:
            result["last_mtime"] = float(last_mtime)
        return result

    def save_sqlite(self, db_path: Path, *, last_seen: float, last_mtime: float | None) -> None:
        def update(payload: dict[str, object]) -> None:
            sqlite = payload.setdefault("sqlite", {})
            if not isinstance(sqlite, dict):
                payload["sqlite"] = {}
                sqlite = payload["sqlite"]
            entry: dict[str, float] = {"last_seen": max(float(last_seen), 0.0)}
            if last_mtime is not None and last_mtime >= 0:
                entry["last_mtime"] = float(last_mtime)
            cast(dict[str, object], sqlite)[_state_key(db_path)] = entry

        self._update(update)

    def load_dirty_sources(self) -> set[str]:
        payload = self._read()
        raw = payload.get("dirty_sources", [])
        if not isinstance(raw, list):
            return set()
        return {item for item in raw if isinstance(item, str) and item}

    def save_dirty_sources(self, sources: set[str]) -> None:
        def update(payload: dict[str, object]) -> None:
            payload["dirty_sources"] = sorted(sources)

        self._update(update)

    def _read(self) -> dict[str, object]:
        with _WATCHER_STATE_LOCK:
            if not self.path.exists():
                return {}
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
        return raw if isinstance(raw, dict) else {}

    def _update(self, mutator: Callable[[dict[str, object]], None]) -> None:
        with _WATCHER_STATE_LOCK:
            payload = self._read_unlocked()
            mutator(payload)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            tmp_path.replace(self.path)

    def _read_unlocked(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _normalize_jsonl_state(state: object) -> dict[str, int | float | None]:
        if not isinstance(state, dict):
            return {}
        offset = state.get("offset")
        inode = state.get("inode")
        mtime = state.get("mtime")
        result: dict[str, int | float | None] = {}
        if isinstance(offset, int) and offset >= 0:
            result["offset"] = offset
        if isinstance(inode, int) and inode >= 0:
            result["inode"] = inode
        if isinstance(mtime, (int, float)) and mtime >= 0:
            result["mtime"] = float(mtime)
        return result

# ---------------------------------------------------------------------------
# JsonlTailer — tails a JSONL file, returns new records since last poll
# ---------------------------------------------------------------------------

READ_CHUNK_SIZE = 64 * 1024
JsonRecord = dict[str, object]


class JsonlTailer:
    def __init__(
        self,
        file_path: Path,
        *,
        suppress_history: bool = False,
        initial_offset: int = 0,
        initial_inode: int | None = None,
    ):
        self.file_path: Path = file_path
        self._suppress_history: bool = suppress_history
        self._offset: int = max(initial_offset, 0)
        self._inode: int | None = initial_inode
        self._failures: list[str] = []

    def poll(self) -> list[JsonRecord]:
        if not self.file_path.exists():
            return []

        stat = self.file_path.stat()
        inode = stat.st_ino

        if self._inode is None and self._suppress_history and self._offset == 0:
            self._inode = inode
            self._offset = stat.st_size
            return []

        if self._inode is None:
            self._inode = inode
        elif inode != self._inode:
            self._inode = inode
            self._offset = 0
        elif stat.st_size < self._offset:
            self._offset = 0

        records: list[JsonRecord] = []
        self._failures = []
        with self.file_path.open("rb") as handle:
            _ = handle.seek(self._offset)
            pending = b""

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

            self._offset = max(handle.tell() - len(pending), 0)

        return records

    def get_failures(self) -> list[str]:
        """Return list of raw lines that failed to parse since last poll."""
        return list(self._failures)

    def state_snapshot(self) -> dict[str, int | None]:
        return {
            "offset": self._offset,
            "inode": self._inode,
        }


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
        max_queue_size: int = 10_000,
        observer: SykeObserver | None = None,
    ):
        self.db: SykeDB = db
        self.user_id: str = user_id
        self.flush_interval_s: float = flush_interval_s
        self.max_batch_size: int = max_batch_size
        self._queue: queue.Queue[Event | object] = queue.Queue(maxsize=max_queue_size)
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
        self._queue.put(event)

    def _run(self) -> None:
        from syke.db import SykeDB as WriterDB

        writer_db = WriterDB(self.db.db_path)
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
                elif not batch and now >= deadline:
                    # When idle, roll the deadline forward so queue.get() blocks again.
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
        state_path: Path | None = None,
        source_lookup: Callable[[Path], str | None] | None = None,
        on_source_dirty: Callable[[str, Path], None] | None = None,
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
        self._tailer_has_saved_state: dict[Path, bool] = {}
        self._tailer_saved_state: dict[Path, dict[str, int | float]] = {}
        self._pending_new_files: set[Path] = set()
        self._source_lookup = source_lookup
        self._on_source_dirty = on_source_dirty
        self._state_store: _WatcherStateStore | None = self._build_state_store(
            writer,
            state_path=state_path,
        )

    def on_created(self, event: FileSystemEvent) -> None:
        if not self._is_macos:
            return
        file_path = self._event_path(event)
        if file_path is None or not self._should_watch(file_path):
            return
        self._pending_new_files.add(file_path)
        if self._size_changed(file_path):
            self._process_file(file_path)

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

    def _process_file(self, file_path: Path, *, bootstrap: bool = False) -> None:
        tailer = self._tailers.get(file_path)
        if tailer is None:
            tailer = self._create_tailer(
                file_path,
                suppress_history=self._should_suppress_initial_history(
                    file_path,
                    bootstrap=bootstrap,
                ),
            )
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

        has_new_data = bool(records) or bool(failures)
        if bootstrap and not self._tailer_has_saved_state.get(file_path, False):
            has_new_data = True

        self._persist_tailer_state(file_path, tailer)
        self._mark_source_dirty(
            file_path,
            has_new_data=has_new_data,
        )
        self._pending_new_files.discard(file_path)

    def bootstrap_existing_files(self, file_paths: Iterable[Path]) -> None:
        persisted_state = self._load_persisted_jsonl_state()
        for file_path in file_paths:
            saved_state = persisted_state.get(_state_key(file_path), {})
            if self._should_bootstrap_on_startup(file_path, saved_state):
                self._process_file(file_path, bootstrap=True)
                continue
            self._prime_known_file(file_path)

    def _create_tailer(self, file_path: Path, *, suppress_history: bool | None = None) -> JsonlTailer:
        state: dict[str, int | float | None] = {}
        if self._state_store is not None:
            state = self._state_store.load_jsonl(file_path)
        self._tailer_has_saved_state[file_path] = bool(state)
        self._tailer_saved_state[file_path] = self._normalized_saved_tailer_state(state)
        if self._is_macos:
            return JsonlTailer(
                file_path,
                suppress_history=bool(suppress_history),
                initial_offset=cast(int, state.get("offset") or 0),
                initial_inode=cast(int | None, state.get("inode")),
            )
        return JsonlTailer(
            file_path,
            initial_offset=cast(int, state.get("offset") or 0),
            initial_inode=cast(int | None, state.get("inode")),
        )

    def _should_suppress_initial_history(self, file_path: Path, *, bootstrap: bool) -> bool:
        if not self._is_macos:
            return False
        if bootstrap:
            return True
        return file_path not in self._pending_new_files

    def _persist_tailer_state(self, file_path: Path, tailer: JsonlTailer) -> None:
        if self._state_store is None:
            return
        state = tailer.state_snapshot()
        normalized_state = self._normalized_saved_tailer_state(state)
        file_mtime = self._file_mtime(file_path)
        if file_mtime is not None:
            normalized_state["mtime"] = file_mtime
        if self._tailer_saved_state.get(file_path) == normalized_state:
            return
        self._state_store.save_jsonl(
            file_path,
            offset=cast(int, state.get("offset") or 0),
            inode=cast(int | None, state.get("inode")),
            mtime=file_mtime,
        )
        self._tailer_saved_state[file_path] = normalized_state

    def _mark_source_dirty(self, file_path: Path, *, has_new_data: bool) -> None:
        if not has_new_data:
            return
        if self._source_lookup is None or self._on_source_dirty is None:
            return
        source = self._source_lookup(file_path)
        if source:
            self._on_source_dirty(source, file_path)

    @staticmethod
    def _build_state_store(
        writer: SenseWriter,
        *,
        state_path: Path | None,
    ) -> _WatcherStateStore | None:
        if state_path is not None:
            return _WatcherStateStore(state_path)
        db = getattr(writer, "db", None)
        from syke.db import SykeDB as CurrentSykeDB

        if not isinstance(db, CurrentSykeDB):
            return None
        return _WatcherStateStore(_default_watcher_state_path(db))

    def _load_persisted_jsonl_state(self) -> dict[str, dict[str, int | float | None]]:
        if self._state_store is None:
            return {}
        return self._state_store.load_all_jsonl()

    def _should_bootstrap_on_startup(
        self,
        file_path: Path,
        state: dict[str, int | float | None],
    ) -> bool:
        try:
            stat = file_path.stat()
        except FileNotFoundError:
            return False

        if not state:
            return True

        offset = state.get("offset")
        if not isinstance(offset, int) or offset < 0:
            return True

        inode = state.get("inode")
        if isinstance(inode, int) and inode >= 0 and stat.st_ino != inode:
            return True

        if stat.st_size != offset:
            return True

        mtime = state.get("mtime")
        if isinstance(mtime, (int, float)) and float(stat.st_mtime) != float(mtime):
            return True

        return False

    def _prime_known_file(self, file_path: Path) -> None:
        try:
            self._last_sizes[file_path] = file_path.stat().st_size
        except FileNotFoundError:
            return

    @staticmethod
    def _normalized_saved_tailer_state(
        state: dict[str, int | float | None],
    ) -> dict[str, int | float]:
        return {
            key: value for key, value in state.items() if isinstance(value, (int, float))
        }

    @staticmethod
    def _file_mtime(file_path: Path) -> float | None:
        try:
            return float(file_path.stat().st_mtime)
        except FileNotFoundError:
            return None


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
        state_path: Path | None = None,
        on_source_dirty: Callable[[str, Path], None] | None = None,
    ):
        self.descriptors: list[HarnessDescriptor] = descriptors
        self.writer: SenseWriter = writer
        self._observer: ObserverLike = observer or Observer()  # type: ignore[assignment]
        self._roots_by_source: list[tuple[Path, str]] = self._collect_source_roots()
        self._handler: SenseFileHandler = SenseFileHandler(
            writer,
            syke_observer=syke_observer,
            heal_fn=heal_fn,
            state_path=state_path,
            source_lookup=self._source_for_path,
            on_source_dirty=on_source_dirty,
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
        self._handler.bootstrap_existing_files(self._iter_existing_files())
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
        for root, _source in self._roots_by_source:
            if root.is_dir():
                roots.add(root)
        return sorted(roots)

    def _collect_source_roots(self) -> list[tuple[Path, str]]:
        roots: list[tuple[Path, str]] = []
        for descriptor in self.descriptors:
            if descriptor.format_cluster not in {"jsonl", "json"}:
                continue
            if descriptor.discover is None:
                continue
            for root in descriptor.discover.roots:
                path = Path(root.path).expanduser()
                if path.exists():
                    roots.append((path.resolve(), descriptor.source))
        roots.sort(key=lambda item: len(str(item[0])), reverse=True)
        return roots

    def _iter_existing_files(self) -> list[Path]:
        files: set[Path] = set()
        for descriptor in self.descriptors:
            if descriptor.format_cluster not in {"jsonl", "json"}:
                continue
            if descriptor.discover is None:
                continue
            for root in descriptor.discover.roots:
                path = Path(root.path).expanduser()
                if path.is_file() and path.suffix.lower() in {".jsonl", ".json"}:
                    files.add(path.resolve())
                    continue
                if not path.is_dir():
                    continue
                patterns = root.include or ["**/*.jsonl", "**/*.json"]
                for pattern in patterns:
                    for match in path.glob(pattern):
                        if match.is_file() and match.suffix.lower() in {".jsonl", ".json"}:
                            files.add(match.resolve())
        return sorted(files)

    def _source_for_path(self, file_path: Path) -> str | None:
        candidate = file_path.expanduser()
        try:
            candidate = candidate.resolve()
        except OSError:
            return None
        for root, source in self._roots_by_source:
            if root.is_file():
                if candidate == root:
                    return source
                continue
            try:
                candidate.relative_to(root)
                return source
            except ValueError:
                continue
        return None


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
        state_path: Path | None = None,
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
        self._startup_poll_pending: bool = False
        self._persisted_state: dict[str, float] = {}
        self._state_store: _WatcherStateStore | None = self._build_state_store(
            writer,
            state_path=state_path,
        )

        if self._state_store is not None:
            state = self._state_store.load_sqlite(db_path)
            self._last_seen_by_db[self.db_path] = float(state.get("last_seen", 0.0))
            last_mtime = state.get("last_mtime")
            self._last_mtime = float(last_mtime) if isinstance(last_mtime, (int, float)) else None
            self._startup_poll_pending = bool(state)
            self._persisted_state = {
                key: float(value) for key, value in state.items() if isinstance(value, (int, float))
            }

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
        self._persist_state()
        self._thread = None

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            mtime = self._db_mtime()
            if self._startup_poll_pending or (mtime is not None and mtime != self._last_mtime):
                self._startup_poll_pending = False
                self._last_mtime = mtime
                sessions = self._query_sessions_with_retry()
                self._process_sessions(sessions)
                self._persist_state()
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

    def _persist_state(self) -> None:
        if self._state_store is None:
            return
        current_state = {"last_seen": self._last_seen_by_db.get(self.db_path, 0.0)}
        if self._last_mtime is not None:
            current_state["last_mtime"] = self._last_mtime
        if self._persisted_state == current_state:
            return
        self._state_store.save_sqlite(
            self.db_path,
            last_seen=self._last_seen_by_db.get(self.db_path, 0.0),
            last_mtime=self._last_mtime,
        )
        self._persisted_state = current_state

    @staticmethod
    def _build_state_store(
        writer: SenseWriter,
        *,
        state_path: Path | None,
    ) -> _WatcherStateStore | None:
        if state_path is not None:
            return _WatcherStateStore(state_path)
        db = getattr(writer, "db", None)
        if not isinstance(db, SykeDB):
            return None
        return _WatcherStateStore(_default_watcher_state_path(db))


__all__ = [
    "JsonlTailer",
    "ObserverLike",
    "SQLiteWatcher",
    "SenseFileHandler",
    "SenseWatcher",
    "SenseWriter",
]
