"""Tests for DynamicAdapter — loading parse_line from disk and ingesting."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from syke.db import SykeDB
from syke.observe.dynamic_adapter import DynamicAdapter, _load_parse_line

ADAPTER_CODE = """\
import json

def parse_line(line):
    data = json.loads(line)
    return {
        "timestamp": data.get("timestamp"),
        "session_id": data.get("sessionId"),
        "role": data.get("type"),
        "content": data.get("text", ""),
        "event_type": "turn",
    }
"""


def _write_adapter(tmp_path: Path) -> Path:
    adapter_dir = tmp_path / "test-source"
    adapter_dir.mkdir()
    (adapter_dir / "adapter.py").write_text(ADAPTER_CODE)
    return adapter_dir


def _write_jsonl(path: Path, session_id: str, turns: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    ts = datetime(2026, 3, 16, 12, 0, 0, tzinfo=UTC)
    for i, turn in enumerate(turns):
        record = {
            "type": turn["role"],
            "sessionId": session_id,
            "timestamp": ts.isoformat(),
            "text": turn.get("text", f"turn {i}"),
        }
        lines.append(json.dumps(record))
    path.write_text("\n".join(lines) + "\n")
    return path


def test_load_parse_line(tmp_path):
    adapter_dir = _write_adapter(tmp_path)
    module = _load_parse_line(adapter_dir / "adapter.py")
    assert hasattr(module, "parse_line")
    result = module.parse_line(
        '{"type": "user", "text": "hi", "timestamp": "2026-01-01T00:00:00Z"}'
    )
    assert result["role"] == "user"
    assert result["content"] == "hi"


def test_load_parse_line_missing():
    with pytest.raises((ImportError, FileNotFoundError)):
        _load_parse_line(Path("/nonexistent/adapter.py"))


def test_load_parse_line_no_function(tmp_path):
    bad = tmp_path / "bad" / "adapter.py"
    bad.parent.mkdir()
    bad.write_text("x = 1\n")
    with pytest.raises(ImportError, match="no parse_line"):
        _load_parse_line(bad)


def test_discover_finds_files(tmp_path):
    adapter_dir = _write_adapter(tmp_path)
    data_dir = tmp_path / "data"
    _write_jsonl(data_dir / "sessions" / "s1.jsonl", "s1", [{"role": "user"}])
    _write_jsonl(data_dir / "sessions" / "s2.jsonl", "s2", [{"role": "user"}])

    with SykeDB(tmp_path / "test.db") as db:
        adapter = DynamicAdapter(
            db=db,
            user_id="test",
            source_name="test-source",
            adapter_dir=adapter_dir,
            discover_roots=[data_dir],
        )
        found = adapter.discover()
    assert len(found) == 2


def test_iter_sessions(tmp_path):
    adapter_dir = _write_adapter(tmp_path)
    data_dir = tmp_path / "data"
    _write_jsonl(
        data_dir / "s1.jsonl",
        "session-1",
        [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "hi back"},
        ],
    )

    with SykeDB(tmp_path / "test.db") as db:
        adapter = DynamicAdapter(
            db=db,
            user_id="test",
            source_name="test-source",
            adapter_dir=adapter_dir,
            discover_roots=[data_dir],
        )
        sessions = list(adapter.iter_sessions())

    assert len(sessions) == 1
    assert sessions[0].session_id == "session-1"
    assert len(sessions[0].turns) == 2
    assert sessions[0].turns[0].role == "user"
    assert sessions[0].turns[0].content == "hello"


def test_iter_sessions_can_scope_to_explicit_paths(tmp_path):
    adapter_dir = _write_adapter(tmp_path)
    data_dir = tmp_path / "data"
    changed = _write_jsonl(
        data_dir / "changed.jsonl",
        "session-changed",
        [{"role": "user", "text": "changed"}],
    )
    _write_jsonl(
        data_dir / "unchanged.jsonl",
        "session-unchanged",
        [{"role": "user", "text": "unchanged"}],
    )

    with SykeDB(tmp_path / "test.db") as db:
        adapter = DynamicAdapter(
            db=db,
            user_id="test",
            source_name="test-source",
            adapter_dir=adapter_dir,
            discover_roots=[data_dir],
        )
        sessions = list(adapter.iter_sessions(paths=[changed]))

    assert len(sessions) == 1
    assert sessions[0].session_id == "session-changed"


def test_explicit_paths_bypass_mtime_filter(tmp_path):
    adapter_dir = _write_adapter(tmp_path)
    data_dir = tmp_path / "data"
    changed = _write_jsonl(
        data_dir / "older.jsonl",
        "session-older",
        [{"role": "user", "text": "still include me"}],
    )

    with SykeDB(tmp_path / "test.db") as db:
        adapter = DynamicAdapter(
            db=db,
            user_id="test",
            source_name="test-source",
            adapter_dir=adapter_dir,
            discover_roots=[data_dir],
        )
        future_since = datetime(2030, 1, 1, tzinfo=UTC).timestamp()
        sessions = list(adapter.iter_sessions(since=future_since, paths=[changed]))

    assert len(sessions) == 1
    assert sessions[0].session_id == "session-older"


def test_full_ingest_cycle(tmp_path):
    adapter_dir = _write_adapter(tmp_path)
    data_dir = tmp_path / "data"
    _write_jsonl(
        data_dir / "sess.jsonl",
        "s1",
        [
            {"role": "user", "text": "what is 2+2"},
            {"role": "assistant", "text": "4"},
            {"role": "user", "text": "thanks"},
        ],
    )

    with SykeDB(tmp_path / "test.db") as db:
        adapter = DynamicAdapter(
            db=db,
            user_id="test",
            source_name="test-source",
            adapter_dir=adapter_dir,
            discover_roots=[data_dir],
        )
        result = adapter.ingest()

    assert result.events_count >= 3


def test_parse_ts_iso():
    ts = DynamicAdapter._parse_ts("2026-03-16T12:00:00+00:00")
    assert ts.year == 2026
    assert ts.month == 3


def test_parse_ts_epoch():
    ts = DynamicAdapter._parse_ts(1710590400.0)
    assert ts.year >= 2024


def test_parse_ts_none():
    ts = DynamicAdapter._parse_ts(None)
    assert isinstance(ts, datetime)


def test_malformed_lines_skipped(tmp_path):
    adapter_dir = _write_adapter(tmp_path)
    data_dir = tmp_path / "data"
    fpath = data_dir / "mixed.jsonl"
    fpath.parent.mkdir(parents=True)
    fpath.write_text(
        '{"type":"user","sessionId":"s1","timestamp":"2026-01-01T00:00:00Z","text":"ok"}\n'
        "not json at all\n"
        '{"type":"assistant","sessionId":"s1","timestamp":"2026-01-01T00:00:01Z","text":"yep"}\n'
    )

    with SykeDB(tmp_path / "test.db") as db:
        adapter = DynamicAdapter(
            db=db,
            user_id="test",
            source_name="test-source",
            adapter_dir=adapter_dir,
            discover_roots=[data_dir],
        )
        sessions = list(adapter.iter_sessions())

    assert len(sessions) == 1
    assert len(sessions[0].turns) == 2
