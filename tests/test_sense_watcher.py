from __future__ import annotations

# pyright: reportUnknownMemberType=false

import json
from pathlib import Path
from typing import cast

from syke.observe.descriptor import HarnessDescriptor
from syke.observe.handler import SenseFileHandler
from syke.observe.watcher import SenseWatcher
from syke.observe.writer import SenseWriter


class _Event:
    def __init__(self, src_path: str) -> None:
        self.src_path: str = src_path
        self.is_directory: bool = False


class _FileClosedEvent(_Event):
    pass


class _FileModifiedEvent(_Event):
    pass


class _WriterStub:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def enqueue(self, event: object) -> None:
        if isinstance(event, dict):
            self.events.append(cast(dict[str, object], cast(object, event)))


def _writer(writer: _WriterStub) -> SenseWriter:
    return cast(SenseWriter, cast(object, writer))


class _ObserverStub:
    def __init__(self) -> None:
        self.scheduled: list[tuple[object, str, bool]] = []
        self.started: bool = False
        self.stopped: bool = False
        self.joined: bool = False

    def schedule(self, handler: object, path: str, recursive: bool) -> None:
        self.scheduled.append((handler, path, recursive))

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self) -> None:
        self.joined = True


def _append_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("ab") as handle:
        for record in records:
            _ = handle.write(json.dumps(record).encode("utf-8") + b"\n")


def _make_descriptor(path: Path) -> HarnessDescriptor:
    return HarnessDescriptor.model_validate(
        {
            "spec_version": 1,
            "source": "sense-test",
            "format_cluster": "jsonl",
            "status": "stub",
            "discover": {"roots": [{"path": str(path)}]},
        }
    )


def test_watcher_detects_new_file(tmp_path: Path) -> None:
    writer = _WriterStub()
    handler = SenseFileHandler(_writer(writer), system_name="Linux")
    fpath = tmp_path / "events.jsonl"

    _append_jsonl(fpath, [{"id": "first"}])
    handler.on_closed(_FileClosedEvent(str(fpath)))

    assert writer.events == [{"id": "first"}]


def test_watcher_detects_append(tmp_path: Path) -> None:
    writer = _WriterStub()
    handler = SenseFileHandler(_writer(writer), system_name="Linux")
    fpath = tmp_path / "events.jsonl"

    _append_jsonl(fpath, [{"id": "first"}])
    handler.on_closed(_FileClosedEvent(str(fpath)))

    _append_jsonl(fpath, [{"id": "second"}])
    handler.on_closed(_FileClosedEvent(str(fpath)))

    assert writer.events == [{"id": "first"}, {"id": "second"}]


def test_watcher_platform_correct(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    fpath = root / "events.jsonl"
    _append_jsonl(fpath, [{"id": "first"}])

    linux_writer = _WriterStub()
    linux_handler = SenseFileHandler(_writer(linux_writer), system_name="Linux")
    linux_handler.on_modified(_FileModifiedEvent(str(fpath)))
    linux_handler.on_closed(_FileClosedEvent(str(fpath)))
    assert linux_writer.events == [{"id": "first"}]

    darwin_writer = _WriterStub()
    darwin_handler = SenseFileHandler(_writer(darwin_writer), system_name="Darwin")
    darwin_handler.on_closed(_FileClosedEvent(str(fpath)))
    darwin_handler.on_modified(_FileModifiedEvent(str(fpath)))
    darwin_handler.on_modified(_FileModifiedEvent(str(fpath)))
    assert darwin_writer.events == []

    _append_jsonl(fpath, [{"id": "second"}])
    darwin_handler.on_modified(_FileModifiedEvent(str(fpath)))
    assert darwin_writer.events == [{"id": "second"}]

    observer = _ObserverStub()
    watcher = SenseWatcher([_make_descriptor(root)], _writer(linux_writer), observer=observer)
    watcher.start()

    assert observer.started is True
    assert observer.scheduled
    assert observer.scheduled[0][1] == str(root.resolve())
    assert observer.scheduled[0][2] is True

    watcher.stop()
    assert observer.stopped is True
    assert observer.joined is True
