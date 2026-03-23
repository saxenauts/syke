# pyright: reportMissingImports=false, reportUnknownVariableType=false

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from watchdog.observers import Observer  # type: ignore[reportMissingImports]

from syke.observe.descriptor import HarnessDescriptor
from syke.observe.handler import SenseFileHandler
from syke.observe.writer import SenseWriter

if TYPE_CHECKING:
    from syke.observe.trace import SykeObserver


class ObserverLike(Protocol):
    def schedule(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
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
            self._syke_observer.record(
                "sense.watcher.start",
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


__all__ = ["SenseWatcher"]
