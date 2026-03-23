# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUntypedBaseClass=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportArgumentType=false

from __future__ import annotations

import logging
import os
import platform
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from watchdog.events import FileSystemEvent, FileSystemEventHandler  # type: ignore[reportMissingImports]

from syke.models import Event
from syke.observe.tailer import JsonlTailer
from syke.observe.writer import SenseWriter

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from syke.observe.trace import SykeObserver


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
            self.writer.enqueue(record)  # type: ignore[arg-type]  # raw dicts from tailer

        failures = tailer.get_failures()
        source = str(file_path)

        if failures:
            self._failure_counts[source] += len(failures)
            samples = self._failure_samples[source]
            samples.extend(failures)
            self._failure_samples[source] = samples[-50:]  # cap at 50
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


__all__ = ["SenseFileHandler"]
