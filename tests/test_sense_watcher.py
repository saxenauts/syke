from __future__ import annotations

# pyright: reportUnknownMemberType=false

import json
from pathlib import Path
from typing import cast

from syke.observe.descriptor import HarnessDescriptor
from syke.observe.runtime import SenseFileHandler, SenseWatcher, SenseWriter


class _Event:
    def __init__(self, src_path: str) -> None:
        self.src_path: str = src_path
        self.is_directory: bool = False


class _FileClosedEvent(_Event):
    pass


class _FileModifiedEvent(_Event):
    pass


class _FileCreatedEvent(_Event):
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


def _wrap_process_calls(watcher: SenseWatcher) -> list[tuple[Path, bool]]:
    calls: list[tuple[Path, bool]] = []
    original = watcher._handler._process_file

    def wrapped(file_path: Path, *, bootstrap: bool = False) -> None:
        calls.append((file_path, bootstrap))
        original(file_path, bootstrap=bootstrap)

    watcher._handler._process_file = wrapped  # type: ignore[method-assign]
    return calls


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


def test_watcher_reads_first_write_for_new_file_on_darwin(tmp_path: Path) -> None:
    writer = _WriterStub()
    handler = SenseFileHandler(_writer(writer), system_name="Darwin")
    fpath = tmp_path / "events.jsonl"

    fpath.touch()
    handler.on_created(_FileCreatedEvent(str(fpath)))

    _append_jsonl(fpath, [{"id": "first"}])
    handler.on_modified(_FileModifiedEvent(str(fpath)))

    assert writer.events == [{"id": "first"}]


def test_watcher_reads_existing_contents_when_new_file_first_seen_on_modified_after_bootstrap(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    fpath = root / "events.jsonl"
    writer = _WriterStub()

    watcher = SenseWatcher(
        [_make_descriptor(root)],
        _writer(writer),
        observer=_ObserverStub(),
    )
    watcher.start()

    _append_jsonl(fpath, [{"id": "first"}])
    watcher._handler.on_modified(_FileModifiedEvent(str(fpath)))
    watcher.stop()

    assert writer.events == [{"id": "first"}]


def test_watcher_restores_offset_after_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "watcher-state.json"
    fpath = tmp_path / "events.jsonl"

    first_writer = _WriterStub()
    first_handler = SenseFileHandler(
        _writer(first_writer),
        system_name="Linux",
        state_path=state_path,
    )
    _append_jsonl(fpath, [{"id": "first"}])
    first_handler.on_closed(_FileClosedEvent(str(fpath)))
    assert first_writer.events == [{"id": "first"}]

    second_writer = _WriterStub()
    second_handler = SenseFileHandler(
        _writer(second_writer),
        system_name="Linux",
        state_path=state_path,
    )
    _append_jsonl(fpath, [{"id": "second"}])
    second_handler.on_closed(_FileClosedEvent(str(fpath)))

    assert second_writer.events == [{"id": "second"}]


def test_watcher_marks_unseen_file_dirty_on_startup(tmp_path: Path) -> None:
    state_path = tmp_path / "watcher-state.json"
    root = tmp_path / "root"
    root.mkdir()
    fpath = root / "events.jsonl"
    dirty_sources: list[tuple[str, Path]] = []
    writer = _WriterStub()

    _append_jsonl(fpath, [{"id": "first"}])

    observer = _ObserverStub()
    watcher = SenseWatcher(
        [_make_descriptor(root)],
        _writer(writer),
        observer=observer,
        state_path=state_path,
        on_source_dirty=lambda source, path: dirty_sources.append((source, path)),
    )
    process_calls = _wrap_process_calls(watcher)
    watcher.start()
    watcher.stop()

    assert process_calls == [(fpath.resolve(), True)]
    assert writer.events == []
    assert dirty_sources == [("sense-test", fpath.resolve())]


def test_watcher_startup_skips_known_unchanged_file(tmp_path: Path) -> None:
    state_path = tmp_path / "watcher-state.json"
    root = tmp_path / "root"
    root.mkdir()
    fpath = root / "events.jsonl"

    _append_jsonl(fpath, [{"id": "first"}])

    initial_watcher = SenseWatcher(
        [_make_descriptor(root)],
        _writer(_WriterStub()),
        observer=_ObserverStub(),
        state_path=state_path,
    )
    initial_watcher.start()
    initial_watcher.stop()

    second_writer = _WriterStub()
    dirty_sources: list[tuple[str, Path]] = []
    second_watcher = SenseWatcher(
        [_make_descriptor(root)],
        _writer(second_writer),
        observer=_ObserverStub(),
        state_path=state_path,
        on_source_dirty=lambda source, path: dirty_sources.append((source, path)),
    )
    process_calls = _wrap_process_calls(second_watcher)

    second_watcher.start()
    second_watcher.stop()

    assert process_calls == []
    assert second_writer.events == []
    assert dirty_sources == []


def test_watcher_startup_bootstraps_nested_files(tmp_path: Path) -> None:
    state_path = tmp_path / "watcher-state.json"
    root = tmp_path / "root"
    nested = root / "nested" / "session"
    nested.mkdir(parents=True)
    fpath = nested / "events.jsonl"
    dirty_sources: list[tuple[str, Path]] = []
    writer = _WriterStub()

    _append_jsonl(fpath, [{"id": "first"}])

    watcher = SenseWatcher(
        [_make_descriptor(root)],
        _writer(writer),
        observer=_ObserverStub(),
        state_path=state_path,
        on_source_dirty=lambda source, path: dirty_sources.append((source, path)),
    )
    process_calls = _wrap_process_calls(watcher)

    watcher.start()
    watcher.stop()

    assert process_calls == [(fpath.resolve(), True)]
    assert writer.events == []
    assert dirty_sources == [("sense-test", fpath.resolve())]


def test_watcher_startup_marks_known_grown_file_dirty(tmp_path: Path) -> None:
    state_path = tmp_path / "watcher-state.json"
    root = tmp_path / "root"
    root.mkdir()
    fpath = root / "events.jsonl"

    _append_jsonl(fpath, [{"id": "first"}])

    initial_watcher = SenseWatcher(
        [_make_descriptor(root)],
        _writer(_WriterStub()),
        observer=_ObserverStub(),
        state_path=state_path,
    )
    initial_watcher.start()
    initial_watcher.stop()

    _append_jsonl(fpath, [{"id": "second"}])

    second_writer = _WriterStub()
    dirty_sources: list[tuple[str, Path]] = []
    second_watcher = SenseWatcher(
        [_make_descriptor(root)],
        _writer(second_writer),
        observer=_ObserverStub(),
        state_path=state_path,
        on_source_dirty=lambda source, path: dirty_sources.append((source, path)),
    )
    process_calls = _wrap_process_calls(second_watcher)

    second_watcher.start()
    second_watcher.stop()

    assert process_calls == [(fpath.resolve(), True)]
    assert second_writer.events == [{"id": "second"}]
    assert dirty_sources == [("sense-test", fpath.resolve())]


def test_watcher_startup_marks_inode_changed_file_dirty(tmp_path: Path) -> None:
    state_path = tmp_path / "watcher-state.json"
    root = tmp_path / "root"
    root.mkdir()
    fpath = root / "events.jsonl"

    _append_jsonl(fpath, [{"id": "first"}])

    initial_watcher = SenseWatcher(
        [_make_descriptor(root)],
        _writer(_WriterStub()),
        observer=_ObserverStub(),
        state_path=state_path,
    )
    initial_watcher.start()
    initial_watcher.stop()

    original_inode = fpath.stat().st_ino
    fpath.unlink()
    _append_jsonl(fpath, [{"id": "replacement"}])
    assert fpath.stat().st_ino != original_inode

    second_writer = _WriterStub()
    dirty_sources: list[tuple[str, Path]] = []
    second_watcher = SenseWatcher(
        [_make_descriptor(root)],
        _writer(second_writer),
        observer=_ObserverStub(),
        state_path=state_path,
        on_source_dirty=lambda source, path: dirty_sources.append((source, path)),
    )
    process_calls = _wrap_process_calls(second_watcher)

    second_watcher.start()
    second_watcher.stop()

    assert process_calls == [(fpath.resolve(), True)]
    assert second_writer.events == [{"id": "replacement"}]
    assert dirty_sources == [("sense-test", fpath.resolve())]


def test_watcher_does_not_mark_known_file_dirty_on_darwin_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "watcher-state.json"
    fpath = tmp_path / "events.jsonl"
    dirty_sources: list[tuple[str, Path]] = []

    _append_jsonl(fpath, [{"id": "first"}])

    initial_handler = SenseFileHandler(
        _writer(_WriterStub()),
        system_name="Darwin",
        state_path=state_path,
        source_lookup=lambda _path: "sense-test",
        on_source_dirty=lambda _source, _path: None,
    )
    initial_handler.on_modified(_FileModifiedEvent(str(fpath)))

    handler = SenseFileHandler(
        _writer(_WriterStub()),
        system_name="Darwin",
        state_path=state_path,
        source_lookup=lambda _path: "sense-test",
        on_source_dirty=lambda source, path: dirty_sources.append((source, path)),
    )
    handler.on_modified(_FileModifiedEvent(str(fpath)))

    assert dirty_sources == []

    _append_jsonl(fpath, [{"id": "second"}])
    handler.on_modified(_FileModifiedEvent(str(fpath)))

    assert dirty_sources == [("sense-test", fpath.resolve())]


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
