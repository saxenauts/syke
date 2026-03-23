from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from syke.observe.descriptor import HarnessDescriptor
from syke.observe.structured_file import PARSER_REGISTRY, StructuredFileAdapter


def _write_jsonl(path: Path, lines: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _descriptor(
    root: Path,
    *,
    source: str = "structured-test",
    includes: list[str] | None = None,
    format_cluster: str = "jsonl",
    id_field: str | None = "sessionId",
    id_fallback: str | None = "$file.stem",
    content_parser: str = "extract_text_content",
    tool_parser: str | None = "extract_tool_blocks",
    metadata_fields: Sequence[Mapping[str, object]] | None = None,
    external_template: str | None = None,
    roots: Sequence[Mapping[str, object]] | None = None,
) -> HarnessDescriptor:
    discover_roots = roots or [
        {
            "path": str(root),
            "include": includes or ["**/*.jsonl"],
            "priority": 10,
        }
    ]

    payload: dict[str, object] = {
        "spec_version": 1,
        "source": source,
        "format_cluster": format_cluster,
        "status": "active",
        "discover": {"roots": discover_roots},
        "session": {
            "scope": "file",
            "id_field": id_field,
            "id_fallback": id_fallback,
            "start_time": {"first_timestamp": "timestamp"},
            "parent_session_id": {"field": "parentSessionId"},
        },
        "turn": {
            "match": {"field": "type", "values": ["user", "assistant", "human", "ai"]},
            "role_field": "type",
            "content_parser": content_parser,
            "tool_parser": tool_parser,
            "timestamp_field": "timestamp",
        },
        "metadata": {
            "fields": metadata_fields or [],
        },
    }

    if external_template is not None:
        payload["external_id"] = {"template": external_template}

    return HarnessDescriptor.model_validate(payload)


def _base_lines() -> list[dict[str, object]]:
    return [
        {
            "sessionId": "sess-1",
            "type": "user",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {"content": "hello"},
            "parentSessionId": "parent-1",
            "env": {"branch": "main"},
            "cwd": "",
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T10:01:00Z",
            "message": {"content": "world"},
            "cwd": "/tmp/work",
        },
    ]


def test_discover_finds_files_matching_include_patterns(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    _write_jsonl(root / "a.jsonl", _base_lines())
    _write_jsonl(root / "nested" / "b.jsonl", _base_lines())
    _write_json(root / "skip.json", {"sessionId": "x"})

    desc = _descriptor(root, includes=["**/*.jsonl"])
    adapter = StructuredFileAdapter(db, user_id, desc)

    found = adapter.discover()
    names = {p.name for p in found}
    assert names == {"a.jsonl", "b.jsonl"}


def test_discover_priority_deduplicates_by_stem(db, user_id, tmp_path):
    high = tmp_path / "high"
    low = tmp_path / "low"
    _write_jsonl(high / "shared.jsonl", _base_lines())
    _write_jsonl(low / "shared.jsonl", _base_lines())

    roots = [
        {"path": str(low), "include": ["*.jsonl"], "priority": 10},
        {"path": str(high), "include": ["*.jsonl"], "priority": 20},
    ]
    desc = _descriptor(tmp_path, roots=roots)
    adapter = StructuredFileAdapter(db, user_id, desc)

    found = adapter.discover()
    assert len(found) == 1
    assert found[0].parent == high


def test_discover_filters_by_last_sync(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    older = root / "old.jsonl"
    newer = root / "new.jsonl"
    _write_jsonl(older, _base_lines())
    _write_jsonl(newer, _base_lines())

    run_id = db.start_ingestion_run(user_id, "structured-last-sync")
    db.complete_ingestion_run(run_id, 0)

    now = time.time()
    os.utime(older, (now - 3600, now - 3600))
    os.utime(newer, (now + 5, now + 5))

    desc = _descriptor(root, source="structured-last-sync")
    adapter = StructuredFileAdapter(db, user_id, desc)
    found = adapter.discover()

    assert [p.name for p in found] == ["new.jsonl"]


def test_iter_sessions_jsonl_builds_observed_session(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    session_file = root / "sample.jsonl"
    _write_jsonl(session_file, _base_lines())
    desc = _descriptor(root)
    adapter = StructuredFileAdapter(db, user_id, desc)

    sessions = list(adapter.iter_sessions())
    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "sess-1"
    assert session.parent_session_id == "parent-1"
    assert session.start_time == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert [turn.role for turn in session.turns] == ["user", "assistant"]


def test_iter_sessions_json_array_support(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    session_file = root / "sample.json"
    _write_json(session_file, _base_lines())

    desc = _descriptor(root, format_cluster="json", includes=["*.json"])
    adapter = StructuredFileAdapter(db, user_id, desc)

    sessions = list(adapter.iter_sessions())
    assert len(sessions) == 1
    assert len(sessions[0].turns) == 2


def test_iter_sessions_applies_turn_match(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    lines = _base_lines() + [
        {
            "sessionId": "sess-1",
            "type": "progress",
            "timestamp": "2026-01-01T10:02:00Z",
            "message": {"content": "ignored"},
        }
    ]
    _write_jsonl(root / "sample.jsonl", lines)
    desc = _descriptor(root)
    adapter = StructuredFileAdapter(db, user_id, desc)

    sessions = list(adapter.iter_sessions())
    assert len(sessions[0].turns) == 2


def test_iter_sessions_normalizes_role(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    lines = [
        {
            "sessionId": "sess-role",
            "type": "human",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {"content": "hello"},
        },
        {
            "type": "ai",
            "timestamp": "2026-01-01T10:01:00Z",
            "message": {"content": "reply"},
        },
    ]
    _write_jsonl(root / "role.jsonl", lines)
    desc = _descriptor(root)
    adapter = StructuredFileAdapter(db, user_id, desc)

    sessions = list(adapter.iter_sessions())
    assert [turn.role for turn in sessions[0].turns] == ["user", "assistant"]


def test_content_parser_delegation(db, user_id, tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    _write_jsonl(root / "sample.jsonl", _base_lines())

    def parser(line: dict[str, object]) -> str:
        return f"custom:{line.get('type')}"

    monkeypatch.setitem(PARSER_REGISTRY, "custom_content_parser", parser)
    desc = _descriptor(root, content_parser="custom_content_parser")
    adapter = StructuredFileAdapter(db, user_id, desc)

    sessions = list(adapter.iter_sessions())
    assert [turn.content for turn in sessions[0].turns] == ["custom:user", "custom:assistant"]


def test_tool_parser_delegation(db, user_id, tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    _write_jsonl(root / "sample.jsonl", _base_lines())

    def parser(_: dict[str, object]) -> list[dict[str, object]]:
        return [{"block_type": "tool_use", "tool_name": "x", "tool_id": "1", "input": {}}]

    monkeypatch.setitem(PARSER_REGISTRY, "custom_tool_parser", parser)
    desc = _descriptor(root, tool_parser="custom_tool_parser")
    adapter = StructuredFileAdapter(db, user_id, desc)

    sessions = list(adapter.iter_sessions())
    assert all(turn.tool_calls for turn in sessions[0].turns)


def test_metadata_path_extraction_uses_first_non_none(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    _write_jsonl(root / "sample.jsonl", _base_lines())

    metadata_fields = [{"key": "branch", "path": "env.branch"}]
    desc = _descriptor(root, metadata_fields=metadata_fields)
    adapter = StructuredFileAdapter(db, user_id, desc)

    sessions = list(adapter.iter_sessions())
    assert sessions[0].metadata["branch"] == "main"


def test_metadata_first_extraction_skips_empty_values(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    _write_jsonl(root / "sample.jsonl", _base_lines())

    metadata_fields = [{"key": "cwd", "first": "cwd"}]
    desc = _descriptor(root, metadata_fields=metadata_fields)
    adapter = StructuredFileAdapter(db, user_id, desc)

    sessions = list(adapter.iter_sessions())
    assert sessions[0].metadata["cwd"] == "/tmp/work"


def test_metadata_parser_extraction(db, user_id, tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    _write_jsonl(root / "sample.jsonl", _base_lines())

    def parser(lines: list[dict[str, object]]) -> str:
        return f"count:{len(lines)}"

    monkeypatch.setitem(PARSER_REGISTRY, "custom_meta_parser", parser)
    metadata_fields = [{"key": "line_count", "parser": "custom_meta_parser"}]
    desc = _descriptor(root, metadata_fields=metadata_fields)
    adapter = StructuredFileAdapter(db, user_id, desc)

    sessions = list(adapter.iter_sessions())
    assert sessions[0].metadata["line_count"] == "count:2"


def test_session_id_fallback_file_stem(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    lines = _base_lines()
    for line in lines:
        line.pop("sessionId", None)
    _write_jsonl(root / "fallback-id.jsonl", lines)

    desc = _descriptor(root, id_field="sessionId", id_fallback="$file.stem")
    adapter = StructuredFileAdapter(db, user_id, desc)
    sessions = list(adapter.iter_sessions())

    assert sessions[0].session_id == "fallback-id"


def test_external_id_expansion_on_turn_metadata(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    _write_jsonl(root / "sample.jsonl", _base_lines())

    desc = _descriptor(root, external_template="{source}:{session_id}:turn:{sequence_index}")
    adapter = StructuredFileAdapter(db, user_id, desc)
    sessions = list(adapter.iter_sessions())

    external_ids = [turn.metadata.get("external_id") for turn in sessions[0].turns]
    assert external_ids == [
        "structured-test:sess-1:turn:0",
        "structured-test:sess-1:turn:1",
    ]


def test_iter_sessions_since_filters_files_by_mtime(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    older = root / "old.jsonl"
    newer = root / "new.jsonl"
    _write_jsonl(older, _base_lines())
    _write_jsonl(newer, _base_lines())

    now = time.time()
    os.utime(older, (now - 1000, now - 1000))
    os.utime(newer, (now + 5, now + 5))

    desc = _descriptor(root)
    adapter = StructuredFileAdapter(db, user_id, desc)
    sessions = list(adapter.iter_sessions(since=now))

    assert len(sessions) == 1
    assert sessions[0].source_path.name == "new.jsonl"


def test_empty_file_is_handled_gracefully(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    empty = root / "empty.jsonl"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_text("", encoding="utf-8")

    desc = _descriptor(root)
    adapter = StructuredFileAdapter(db, user_id, desc)
    sessions = list(adapter.iter_sessions())
    assert sessions == []


def test_malformed_jsonl_logs_warning_without_crash(db, user_id, tmp_path, caplog):
    root = tmp_path / "sessions"
    malformed = root / "bad.jsonl"
    malformed.parent.mkdir(parents=True, exist_ok=True)
    malformed.write_text("not-json\nstill-not-json\n", encoding="utf-8")

    desc = _descriptor(root)
    adapter = StructuredFileAdapter(db, user_id, desc)

    with caplog.at_level("WARNING"):
        sessions = list(adapter.iter_sessions())

    assert sessions == []
    assert any("failed JSON parse" in rec.message for rec in caplog.records)


def test_malformed_json_logs_warning_without_crash(db, user_id, tmp_path, caplog):
    root = tmp_path / "sessions"
    malformed = root / "bad.json"
    malformed.parent.mkdir(parents=True, exist_ok=True)
    malformed.write_text("{not valid}", encoding="utf-8")

    desc = _descriptor(root, format_cluster="json", includes=["*.json"])
    adapter = StructuredFileAdapter(db, user_id, desc)

    with caplog.at_level("WARNING"):
        sessions = list(adapter.iter_sessions())

    assert sessions == []
    assert any("Failed to parse JSON" in rec.message for rec in caplog.records)


def test_unknown_parser_logs_warning_and_skips_file(db, user_id, tmp_path, caplog):
    root = tmp_path / "sessions"
    _write_jsonl(root / "sample.jsonl", _base_lines())

    desc = _descriptor(root, content_parser="missing_parser")
    adapter = StructuredFileAdapter(db, user_id, desc)

    with caplog.at_level("WARNING"):
        sessions = list(adapter.iter_sessions())

    assert sessions == []
    assert any("Failed to parse session" in rec.message for rec in caplog.records)


def test_timestamp_falls_back_to_start_time_when_missing(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    lines = [
        {
            "sessionId": "sess-ts",
            "type": "user",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {"content": "hello"},
        },
        {
            "type": "assistant",
            "message": {"content": "no timestamp"},
        },
    ]
    _write_jsonl(root / "sample.jsonl", lines)
    desc = _descriptor(root)
    adapter = StructuredFileAdapter(db, user_id, desc)
    session = list(adapter.iter_sessions())[0]

    assert session.turns[1].timestamp == session.start_time
    assert session.turns[1].metadata.get("timestamp_inferred") is True


def test_non_matching_turns_result_in_no_sessions(db, user_id, tmp_path):
    root = tmp_path / "sessions"
    lines = [
        {
            "sessionId": "sess-empty",
            "type": "progress",
            "timestamp": "2026-01-01T10:00:00Z",
            "message": {"content": "status"},
        }
    ]
    _write_jsonl(root / "sample.jsonl", lines)
    desc = _descriptor(root)
    adapter = StructuredFileAdapter(db, user_id, desc)

    assert list(adapter.iter_sessions()) == []
