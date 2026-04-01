from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from syke.observe.runtime import JsonlTailer


def _append_jsonl(path: Path, records: Sequence[Mapping[str, object]]) -> None:
    with path.open("ab") as handle:
        for record in records:
            _ = handle.write(json.dumps(record).encode("utf-8") + b"\n")


def test_tailer_reads_new_lines(tmp_path: Path) -> None:
    fpath = tmp_path / "events.jsonl"
    tailer = JsonlTailer(fpath)

    records = [{"idx": i} for i in range(5)]
    _append_jsonl(fpath, records)

    assert tailer.poll() == records
    assert tailer.poll() == []


def test_tailer_handles_partial_write(tmp_path: Path) -> None:
    fpath = tmp_path / "events.jsonl"
    tailer = JsonlTailer(fpath)

    with fpath.open("ab") as handle:
        _ = handle.write(b'{"id": 1')

    assert tailer.poll() == []

    with fpath.open("ab") as handle:
        _ = handle.write(b"}\n")

    assert tailer.poll() == [{"id": 1}]


def test_tailer_detects_rotation(tmp_path: Path) -> None:
    fpath = tmp_path / "events.jsonl"
    tailer = JsonlTailer(fpath)

    _append_jsonl(fpath, [{"id": "old"}])
    assert tailer.poll() == [{"id": "old"}]

    rotated = tmp_path / "events.jsonl.1"
    _ = fpath.rename(rotated)
    _append_jsonl(fpath, [{"id": "new"}])

    assert tailer.poll() == [{"id": "new"}]


def test_tailer_detects_truncation(tmp_path: Path) -> None:
    fpath = tmp_path / "events.jsonl"
    tailer = JsonlTailer(fpath)

    first_batch = [{"idx": i} for i in range(100)]
    _append_jsonl(fpath, first_batch)
    assert tailer.poll() == first_batch

    second_batch = [{"idx": i} for i in range(5)]
    _ = fpath.write_bytes(b"")
    _append_jsonl(fpath, second_batch)

    assert tailer.poll() == second_batch
