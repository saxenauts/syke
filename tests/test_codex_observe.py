from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest.mock import patch

import pytest

from syke.ingestion.codex import CodexAdapter


def _write_jsonl(path: Path, lines: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def _run_codex(adapter: CodexAdapter, root: Path) -> int:
    with patch.dict(os.environ, {"HOME": str(root)}):
        result = adapter.ingest()
    return result.events_count


@pytest.fixture
def adapter_codex(db, user_id):
    return CodexAdapter(db, user_id)


def test_codex_observe_per_turn_with_tools_reasoning_and_metadata(
    adapter_codex, db, user_id, tmp_path
):
    session = (
        tmp_path
        / ".codex"
        / "sessions"
        / "2026"
        / "02"
        / "16"
        / "rollout-2026-02-16T06-02-33-019c24aa-5b5c-7163-8bff-9112bf5c34eb.jsonl"
    )
    _write_jsonl(
        session,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-02-16T06:02:33.558Z",
                "payload": {
                    "cwd": "/Users/test/work/repo",
                    "git": {"branch": "feature/codex-observe"},
                    "model_provider": "openai",
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-02-16T06:02:34.000Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "build parser"}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-02-16T06:02:35.000Z",
                "payload": {
                    "type": "reasoning",
                    "text": "Need to inspect response_item sequence first",
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-02-16T06:02:36.000Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "I will parse it now."}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-02-16T06:02:37.000Z",
                "payload": {
                    "type": "function_call",
                    "name": "read_file",
                    "call_id": "call_1",
                    "arguments": {"path": "syke/ingestion/codex.py"},
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-02-16T06:02:38.000Z",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "done",
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-02-16T06:02:39.000Z",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "internal scaffolding"}],
                },
            },
        ],
    )

    count = _run_codex(adapter_codex, tmp_path)

    assert count == 5

    rows = db.conn.execute(
        "SELECT event_type, role, content, metadata, tool_name, tool_correlation_id "
        "FROM events WHERE user_id = ? AND source = ? ORDER BY sequence_index ASC, timestamp ASC",
        (user_id, "codex"),
    ).fetchall()

    turn_rows = [row for row in rows if row["event_type"] == "turn"]
    assert len(turn_rows) == 2
    assert turn_rows[0]["role"] == "user"
    assert turn_rows[0]["content"] == "build parser"
    assert turn_rows[1]["role"] == "assistant"
    assert turn_rows[1]["content"].startswith(
        "[thinking]\nNeed to inspect response_item sequence first"
    )
    assert "I will parse it now." in turn_rows[1]["content"]

    tool_call = next(row for row in rows if row["event_type"] == "tool_call")
    assert tool_call["tool_name"] == "read_file"
    assert tool_call["tool_correlation_id"] == "call_1"
    assert '"path": "syke/ingestion/codex.py"' in tool_call["content"]

    tool_result = next(row for row in rows if row["event_type"] == "tool_result")
    assert tool_result["tool_correlation_id"] == "call_1"
    assert tool_result["content"] == "done"

    session_row = db.conn.execute(
        "SELECT metadata FROM events WHERE user_id = ? AND source = ? AND event_type = ?",
        (user_id, "codex", "session.start"),
    ).fetchone()
    assert session_row is not None
    metadata = json.loads(session_row["metadata"] or "{}")
    assert metadata["cwd"] == "/Users/test/work/repo"
    assert metadata["git_branch"] == "feature/codex-observe"
    assert metadata["model_provider"] == "openai"


def test_codex_observe_history_fallback_groups_sessions(adapter_codex, db, user_id, tmp_path):
    rich_session = (
        tmp_path
        / ".codex"
        / "sessions"
        / "2026"
        / "02"
        / "16"
        / "rollout-2026-02-16T06-02-33-aaaa1111-bbbb-2222-cccc-3333dddd4444.jsonl"
    )
    _write_jsonl(
        rich_session,
        [
            {
                "type": "response_item",
                "timestamp": "2026-02-16T06:02:34.000Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "rich session user turn"}],
                },
            }
        ],
    )

    history = tmp_path / ".codex" / "history.jsonl"
    _write_jsonl(
        history,
        [
            {
                "session_id": "aaaa1111-bbbb-2222-cccc-3333dddd4444",
                "ts": 1700000000,
                "text": "skip me",
            },
            {
                "session_id": "hist-2",
                "ts": 1700000001,
                "text": "History turn one with enough content to survive content filtering.",
            },
            {
                "session_id": "hist-2",
                "ts": 1700000002,
                "text": "History turn two with enough content to survive content filtering.",
            },
        ],
    )

    count = _run_codex(adapter_codex, tmp_path)
    assert count == 3

    hist_turns = db.conn.execute(
        "SELECT COUNT(*) FROM events WHERE user_id = ? AND source = ? "
        "AND session_id = ? AND event_type = ?",
        (user_id, "codex", "hist-2", "turn"),
    ).fetchone()[0]
    assert hist_turns == 1

    skipped_from_history = db.conn.execute(
        "SELECT COUNT(*) FROM events WHERE user_id = ? AND source = ? AND session_id = ?",
        (user_id, "codex", "aaaa1111-bbbb-2222-cccc-3333dddd4444"),
    ).fetchone()[0]
    assert skipped_from_history == 1


def test_codex_observe_dedup_reingest_is_zero(adapter_codex, db, user_id, tmp_path):
    session = (
        tmp_path
        / ".codex"
        / "sessions"
        / "2026"
        / "02"
        / "16"
        / "rollout-2026-02-16T06-02-33-deadbeef-1111-2222-3333-444455556666.jsonl"
    )
    _write_jsonl(
        session,
        [
            {
                "type": "response_item",
                "timestamp": "2026-02-16T06:02:34.000Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "first"}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-02-16T06:02:35.000Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "second"}],
                },
            },
        ],
    )

    first = _run_codex(adapter_codex, tmp_path)
    second = _run_codex(adapter_codex, tmp_path)

    assert first == 2
    assert second == 0
    assert db.count_events(user_id, source="codex") == 2


def test_codex_observe_empty_session_emits_only_envelope(adapter_codex, db, user_id, tmp_path):
    session = (
        tmp_path
        / ".codex"
        / "sessions"
        / "2026"
        / "02"
        / "16"
        / "rollout-2026-02-16T06-02-33-feedface-1111-2222-3333-444455556666.jsonl"
    )
    _write_jsonl(
        session,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-02-16T06:02:33.558Z",
                "payload": {"cwd": "/Users/test/empty"},
            }
        ],
    )

    count = _run_codex(adapter_codex, tmp_path)
    assert count == 1

    turn_count = db.conn.execute(
        "SELECT COUNT(*) FROM events WHERE user_id = ? AND source = ? AND event_type = ?",
        (user_id, "codex", "turn"),
    ).fetchone()[0]
    assert turn_count == 0
