# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUntypedBaseClass=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING, cast

from watchdog.events import FileSystemEvent, FileSystemEventHandler  # type: ignore[reportMissingImports]

from syke.models import Event
from syke.sense.tailer import JsonlTailer
from syke.sense.writer import SenseWriter

if TYPE_CHECKING:
    from syke.sense.self_observe import SykeObserver


class SenseFileHandler(FileSystemEventHandler):
    def __init__(
        self,
        writer: SenseWriter,
        *,
        system_name: str | None = None,
        syke_observer: SykeObserver | None = None,
    ):
        super().__init__()
        self.writer: SenseWriter = writer
        self._tailers: dict[Path, JsonlTailer] = {}
        self._last_sizes: dict[Path, int] = {}
        self._is_macos: bool = (system_name or platform.system()) == "Darwin"
        self._syke_observer: SykeObserver | None = syke_observer

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
        return Path(event.src_path)

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
        is_new_file = tailer is None
        if tailer is None:
            tailer = self._create_tailer(file_path)
            self._tailers[file_path] = tailer
            if self._syke_observer:
                self._syke_observer.record(
                    "sense.file.detected",
                    {"path": str(file_path)},
                )

        for record in tailer.poll():
            self.writer.enqueue(cast(Event, cast(object, record)))

    def _create_tailer(self, file_path: Path) -> JsonlTailer:
        if self._is_macos:
            return JsonlTailer(file_path, suppress_history=True)
        return JsonlTailer(file_path)


__all__ = ["SenseFileHandler"]
