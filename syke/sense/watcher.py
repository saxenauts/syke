# pyright: reportMissingImports=false, reportUnknownVariableType=false

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from watchdog.observers import Observer  # type: ignore[reportMissingImports]

from syke.ingestion.descriptor import HarnessDescriptor
from syke.sense.handler import SenseFileHandler
from syke.sense.writer import SenseWriter


class ObserverLike(Protocol):
    def schedule(self, handler: object, path: str, recursive: bool) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def join(self) -> None:
        pass


class SenseWatcher:
    def __init__(
        self,
        descriptors: list[HarnessDescriptor],
        writer: SenseWriter,
        *,
        observer: ObserverLike | None = None,
    ):
        self.descriptors: list[HarnessDescriptor] = descriptors
        self.writer: SenseWriter = writer
        self._observer: ObserverLike = observer or Observer()
        self._handler: SenseFileHandler = SenseFileHandler(writer)
        self._started: bool = False

    def start(self) -> None:
        if self._started:
            return
        for root in self._discover_roots():
            self._observer.schedule(self._handler, str(root), recursive=True)
        self._observer.start()
        self._started = True

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


__all__ = ["SenseWatcher"]
