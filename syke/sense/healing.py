"""Healing loop — detect consecutive parse failures and trigger adapter regeneration."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from syke.sense.self_observe import SykeObserver

logger = logging.getLogger(__name__)


class HealingLoop:
    def __init__(
        self,
        observer: SykeObserver | None = None,
        on_threshold: Callable[[str, list[str]], None] | None = None,
        threshold: int = 10,
        max_samples: int = 20,
    ):
        self._failures: dict[str, int] = {}
        self._samples: dict[str, list[str]] = {}
        self._observer = observer
        self._on_threshold = on_threshold
        self._threshold = threshold
        self._max_samples = max_samples
        self._lock = threading.Lock()

    def record_failure(self, source: str, raw_line: str) -> None:
        with self._lock:
            self._failures[source] = self._failures.get(source, 0) + 1
            samples = self._samples.setdefault(source, [])
            if len(samples) < self._max_samples:
                samples.append(raw_line)
            count = self._failures[source]

        if count >= self._threshold:
            if self._observer:
                self._observer.record(
                    "healing.triggered",
                    {
                        "source": source,
                        "consecutive_failures": count,
                        "samples_count": len(samples),
                    },
                )
            if self._on_threshold:
                try:
                    self._on_threshold(source, list(samples))
                except Exception:
                    logger.warning("on_threshold callback failed for %s", source, exc_info=True)

    def record_success(self, source: str) -> None:
        with self._lock:
            self._failures.pop(source, None)
            self._samples.pop(source, None)

    def get_failure_count(self, source: str) -> int:
        with self._lock:
            return self._failures.get(source, 0)

    def get_samples(self, source: str) -> list[str]:
        with self._lock:
            return list(self._samples.get(source, []))
