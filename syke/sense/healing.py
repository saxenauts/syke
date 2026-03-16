"""Healing loop — deterministic health scoring and adapter regeneration.

Health score per source = weighted sum of:
  - column_completeness: % of canonical columns filled (weight 0.3)
  - event_granularity: distinct event_types count (weight 0.2)
  - error_rate: parse_errors / total_lines (weight 0.25)
  - schema_drift: ratio of extras keys matching canonical names (weight 0.1)
  - freshness: seconds since last successful parse (weight 0.15)

Healing triggers when score stays below threshold for sustained_minutes.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from syke.sense.self_observe import SykeObserver

logger = logging.getLogger(__name__)

CANONICAL_COLUMNS = frozenset(
    {
        "session_id",
        "role",
        "content",
        "event_type",
        "timestamp",
        "tool_name",
        "model",
        "input_tokens",
        "output_tokens",
        "parent_session_id",
    }
)


@dataclass
class SourceHealth:
    total_lines: int = 0
    parsed_lines: int = 0
    error_count: int = 0
    columns_seen: set[str] = field(default_factory=set)
    event_types_seen: set[str] = field(default_factory=set)
    extras_keys_seen: set[str] = field(default_factory=set)
    last_success_time: float = 0.0
    below_threshold_since: float = 0.0

    @property
    def column_completeness(self) -> float:
        if not CANONICAL_COLUMNS:
            return 1.0
        return len(self.columns_seen & CANONICAL_COLUMNS) / len(CANONICAL_COLUMNS)

    @property
    def event_granularity(self) -> float:
        n = len(self.event_types_seen)
        if n >= 4:
            return 1.0
        if n >= 2:
            return 0.6
        if n == 1:
            return 0.2
        return 0.0

    @property
    def error_rate(self) -> float:
        if self.total_lines == 0:
            return 0.0
        return self.error_count / self.total_lines

    @property
    def schema_drift(self) -> float:
        if not self.extras_keys_seen:
            return 0.0
        drifted = self.extras_keys_seen & CANONICAL_COLUMNS
        return len(drifted) / max(len(self.extras_keys_seen), 1)

    @property
    def freshness(self) -> float:
        if self.last_success_time <= 0:
            return 0.0
        age = time.time() - self.last_success_time
        if age < 60:
            return 1.0
        if age < 3600:
            return 0.7
        if age < 86400:
            return 0.3
        return 0.0

    def score(self) -> float:
        return (
            0.30 * self.column_completeness
            + 0.20 * self.event_granularity
            + 0.25 * max(0.0, 1.0 - self.error_rate * 20)
            + 0.10 * (1.0 - self.schema_drift)
            + 0.15 * self.freshness
        )


class HealingLoop:
    def __init__(
        self,
        observer: SykeObserver | None = None,
        on_threshold: Callable[[str, list[str]], None] | None = None,
        threshold: float = 0.3,
        sustained_minutes: float = 2.0,
        max_samples: int = 20,
    ):
        self._health: dict[str, SourceHealth] = {}
        self._samples: dict[str, list[str]] = {}
        self._observer = observer
        self._on_threshold = on_threshold
        self._threshold = threshold
        self._sustained_minutes = sustained_minutes
        self._max_samples = max_samples
        self._lock = threading.Lock()
        self._triggered: set[str] = set()

    def record_failure(self, source: str, raw_line: str) -> None:
        with self._lock:
            h = self._health.setdefault(source, SourceHealth())
            h.total_lines += 1
            h.error_count += 1
            samples = self._samples.setdefault(source, [])
            if len(samples) < self._max_samples:
                samples.append(raw_line)

        self._check_health(source)

    def record_success(self, source: str, parsed: dict | None = None) -> None:
        with self._lock:
            h = self._health.setdefault(source, SourceHealth())
            h.total_lines += 1
            h.parsed_lines += 1
            h.last_success_time = time.time()
            h.below_threshold_since = 0.0
            self._triggered.discard(source)

            if parsed and isinstance(parsed, dict):
                for key, val in parsed.items():
                    if val is not None:
                        h.columns_seen.add(key)
                et = parsed.get("event_type")
                if isinstance(et, str):
                    h.event_types_seen.add(et)
                extras = parsed.get("extras")
                if isinstance(extras, dict):
                    h.extras_keys_seen.update(extras.keys())

            self._samples.pop(source, None)

    def get_failure_count(self, source: str) -> int:
        with self._lock:
            h = self._health.get(source)
            return h.error_count if h else 0

    def get_samples(self, source: str) -> list[str]:
        with self._lock:
            return list(self._samples.get(source, []))

    def get_health(self, source: str) -> SourceHealth | None:
        with self._lock:
            return self._health.get(source)

    def get_score(self, source: str) -> float:
        with self._lock:
            h = self._health.get(source)
            return h.score() if h else 1.0

    def _check_health(self, source: str) -> None:
        with self._lock:
            h = self._health.get(source)
            if h is None:
                return
            current_score = h.score()
            now = time.time()

            if current_score >= self._threshold:
                h.below_threshold_since = 0.0
                return

            if h.below_threshold_since <= 0:
                h.below_threshold_since = now
                return

            elapsed_min = (now - h.below_threshold_since) / 60.0
            if elapsed_min < self._sustained_minutes:
                return

            if source in self._triggered:
                return

            self._triggered.add(source)
            samples = list(self._samples.get(source, []))

        if self._observer:
            self._observer.record(
                "healing.triggered",
                {
                    "source": source,
                    "health_score": current_score,
                    "error_count": h.error_count,
                    "total_lines": h.total_lines,
                    "sustained_minutes": elapsed_min,
                },
            )
        if self._on_threshold:
            try:
                self._on_threshold(source, samples)
            except Exception:
                logger.warning("on_threshold callback failed for %s", source, exc_info=True)
