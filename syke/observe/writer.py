from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from typing import TYPE_CHECKING, Callable

from syke.db import SykeDB
from syke.observe.content_filter import ContentFilter
from syke.models import Event

if TYPE_CHECKING:
    from syke.observe.trace import SykeObserver

logger = logging.getLogger(__name__)

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
        """Register a callback to be invoked when events are inserted.

        The callback will be called with a list of inserted events after each flush.
        Callbacks are invoked in a non-blocking manner; exceptions are logged but do not
        affect the writer thread.
        """
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

        # Emit self-observation event if observer is available
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

        # Invoke callbacks with inserted events (non-blocking, exception-safe)
        with self._callbacks_lock:
            callbacks = list(self._on_insert_callbacks)
        for cb in callbacks:
            try:
                cb(inserted_events)
            except Exception:
                logger.warning("on_insert callback failed", exc_info=True)
