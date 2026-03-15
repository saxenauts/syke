from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from syke.db import SykeDB
from syke.ingestion.claude_code import ClaudeCodeAdapter
from syke.ingestion.codex import CodexAdapter
from syke.ingestion.descriptor import (
    SUPPORTED_FORMAT_CLUSTERS,
    HarnessDescriptor,
    load_all_descriptors,
    load_descriptor,
    validate_descriptor,
)
from syke.ingestion.observe import ObserveAdapter
from syke.ingestion.registry import HarnessRegistry
from syke.ingestion.structured_file import StructuredFileAdapter
from syke.models import IngestionResult

REPO_ROOT = Path(__file__).resolve().parents[1]
DESCRIPTORS_DIR = REPO_ROOT / "syke" / "ingestion" / "descriptors"


def _write_jsonl(path: Path, lines: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(data), encoding="utf-8")


def _load_descriptor_with_roots(
    name: str,
    root: Path,
    *,
    include: list[str],
    drop_external_id: bool = False,
) -> HarnessDescriptor:
    raw = load_descriptor(DESCRIPTORS_DIR / name).model_dump(mode="python")
    raw["discover"] = {
        "roots": [
            {
                "path": str(root),
                "include": include,
                "priority": 50,
            }
        ]
    }
    if drop_external_id:
        raw.pop("external_id", None)
    return HarnessDescriptor.model_validate(raw)


def _structured_jsonl_descriptor(root: Path) -> HarnessDescriptor:
    return HarnessDescriptor.model_validate(
        {
            "spec_version": 1,
            "source": "structured-jsonl",
            "format_cluster": "jsonl",
            "status": "active",
            "discover": {
                "roots": [
                    {
                        "path": str(root),
                        "include": ["*.jsonl"],
                        "priority": 10,
                    }
                ]
            },
            "session": {
                "scope": "file",
                "id_field": "sessionId",
                "id_fallback": "$file.stem",
                "start_time": {"first_timestamp": "timestamp"},
                "parent_session_id": {"field": "parentSessionId"},
            },
            "turn": {
                "match": {"field": "type", "values": ["user", "assistant"]},
                "role_field": "type",
                "content_parser": "extract_text_content",
                "tool_parser": "extract_tool_blocks",
                "timestamp_field": "timestamp",
            },
            "metadata": {
                "fields": [
                    {"key": "branch", "path": "context.branch"},
                    {"key": "cwd", "first": "cwd"},
                ]
            },
            "external_id": {"template": "{source}:{session_id}:turn:{sequence_index}"},
        }
    )


def _codex_session_path(root: Path, suffix: str) -> Path:
    return (
        root
        / ".codex"
        / "sessions"
        / "2026"
        / "03"
        / "14"
        / f"rollout-2026-03-14T12-00-00-{suffix}.jsonl"
    )


def _ingest(adapter: ObserveAdapter) -> IngestionResult:
    return ObserveAdapter.ingest(adapter)  # pyright: ignore[reportUnknownMemberType]


def test_registry_loads_all_descriptors() -> None:
    registry = HarnessRegistry()

    assert len(registry.list_harnesses()) == 7


def test_registry_active_harnesses() -> None:
    registry = HarnessRegistry()
    active_sources = {descriptor.source for descriptor in registry.active_harnesses()}

    assert {"claude-code", "codex", "github", "gmail", "hermes", "opencode", "pi"} <= active_sources


def test_registry_format_clusters() -> None:
    registry = HarnessRegistry()
    clusters_in_registry = {descriptor.format_cluster for descriptor in registry.list_harnesses()}

    for cluster in clusters_in_registry:
        assert cluster in SUPPORTED_FORMAT_CLUSTERS
        assert registry.by_format_cluster(cluster), cluster


def test_registry_get_adapter_claude_code(db: SykeDB, user_id: str) -> None:
    registry = HarnessRegistry()

    adapter = registry.get_adapter("claude-code", db, user_id)

    assert isinstance(adapter, ClaudeCodeAdapter)


def test_registry_health_summary() -> None:
    registry = HarnessRegistry()
    summary = registry.health_summary()

    assert len(summary) == 7
    assert summary["claude-code"] == "active"
    assert summary["github"] == "active"


def test_structured_adapter_from_descriptor(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    session_file = tmp_path / ".claude" / "projects" / "demo-project" / "session-a.jsonl"
    _write_jsonl(
        session_file,
        [
            {
                "sessionId": "session-a",
                "type": "user",
                "timestamp": "2026-03-14T12:00:00Z",
                "message": {"content": "hello"},
            }
        ],
    )

    descriptor = load_descriptor(DESCRIPTORS_DIR / "claude-code.toml")
    adapter = StructuredFileAdapter(db, user_id, descriptor)

    discovered = adapter.discover()

    assert discovered == [session_file]


def test_structured_adapter_jsonl_roundtrip(db: SykeDB, user_id: str, tmp_path: Path) -> None:
    session_file = tmp_path / "structured" / "session.jsonl"
    _write_jsonl(
        session_file,
        [
            {
                "sessionId": "structured-session",
                "parentSessionId": "parent-session",
                "type": "user",
                "timestamp": "2026-03-14T12:00:00Z",
                "cwd": "",
                "context": {"branch": "main"},
                "message": {"content": [{"type": "text", "text": "Plan the adapter."}]},
            },
            {
                "type": "assistant",
                "timestamp": "2026-03-14T12:01:00Z",
                "cwd": "/tmp/demo",
                "message": {
                    "content": [
                        {"type": "thinking", "text": "Inspect the descriptor first."},
                        {"type": "text", "text": "I inspected it."},
                        {
                            "type": "tool_use",
                            "name": "read_file",
                            "id": "tool-1",
                            "input": {"path": "syke/ingestion/descriptor.py"},
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": [{"text": "descriptor loaded"}],
                        },
                    ]
                },
            },
        ],
    )

    adapter = StructuredFileAdapter(db, user_id, _structured_jsonl_descriptor(session_file.parent))

    sessions = list(adapter.iter_sessions())

    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "structured-session"
    assert session.parent_session_id == "parent-session"
    assert session.metadata["branch"] == "main"
    assert session.metadata["cwd"] == "/tmp/demo"
    assert [turn.role for turn in session.turns] == ["user", "assistant"]
    assert session.turns[1].content.startswith("[thinking]\nInspect the descriptor first.")
    assert session.turns[1].tool_calls == [
        {
            "block_type": "tool_use",
            "tool_name": "read_file",
            "tool_id": "tool-1",
            "input": {"path": "syke/ingestion/descriptor.py"},
        },
        {
            "block_type": "tool_result",
            "tool_use_id": "tool-1",
            "content": "descriptor loaded",
            "is_error": False,
        },
    ]
    assert [turn.metadata["external_id"] for turn in session.turns] == [
        "structured-jsonl:structured-session:turn:0",
        "structured-jsonl:structured-session:turn:1",
    ]


def test_structured_adapter_json_roundtrip(db: SykeDB, user_id: str, tmp_path: Path) -> None:
    session_file = tmp_path / "chatgpt-export" / "conversations.json"
    _write_json(
        session_file,
        [
            {
                "id": "chatgpt-session-1",
                "create_time": "2026-03-14T12:00:00Z",
                "title": "Adapter protocol",
                "default_model_slug": "gpt-5",
                "update_time": "2026-03-14T12:01:00Z",
                "message": {
                    "author": {"role": "user"},
                    "create_time": "2026-03-14T12:00:00Z",
                    "content": "How do these adapters compose?",
                },
            },
            {
                "id": "chatgpt-session-1",
                "message": {
                    "author": {"role": "assistant"},
                    "create_time": "2026-03-14T12:01:00Z",
                    "content": [{"type": "text", "text": "They compose through observe."}],
                },
            },
        ],
    )

    descriptor = HarnessDescriptor.model_validate(
        {
            "spec_version": 1,
            "source": "chatgpt",
            "format_cluster": "json",
            "status": "active",
            "discover": {
                "roots": [
                    {
                        "path": str(session_file.parent),
                        "include": ["*.json"],
                        "priority": 50,
                    }
                ]
            },
            "session": {
                "scope": "file",
                "id_field": "id",
                "id_fallback": "$file.stem",
                "start_time": {"first_timestamp": "create_time"},
            },
            "turn": {
                "match": {"field": "message.author.role", "values": ["user", "assistant"]},
                "role_field": "message.author.role",
                "content_parser": "extract_text_content",
                "timestamp_field": "message.create_time",
            },
            "metadata": {
                "fields": [
                    {"key": "title", "first": "title"},
                    {"key": "model", "first": "default_model_slug"},
                ]
            },
        }
    )
    adapter = StructuredFileAdapter(db, user_id, descriptor)

    sessions = list(adapter.iter_sessions())

    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "chatgpt-session-1"
    assert session.metadata["title"] == "Adapter protocol"
    assert session.metadata["model"] == "gpt-5"
    assert [turn.role for turn in session.turns] == ["user", "assistant"]
    assert [turn.content for turn in session.turns] == [
        "How do these adapters compose?",
        "They compose through observe.",
    ]


def test_codex_adapter_produces_observe_sessions(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    session_file = _codex_session_path(tmp_path, "11111111-2222-3333-4444-555555555555")
    _write_jsonl(
        session_file,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-03-14T12:00:00Z",
                "payload": {
                    "cwd": str(Path.home() / "work" / "repo"),
                    "git": {"branch": "main"},
                    "model_provider": "openai",
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-14T12:00:01Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Inspect this rollout."}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-14T12:00:02Z",
                "payload": {
                    "type": "reasoning",
                    "text": "I should inspect the response items first.",
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-14T12:00:03Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Inspection complete."}],
                },
            },
        ],
    )

    adapter = CodexAdapter(db, user_id)
    sessions = list(adapter.iter_sessions())

    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "11111111-2222-3333-4444-555555555555"
    assert session.metadata["cwd"] == str(Path.home() / "work" / "repo")
    assert session.metadata["git_branch"] == "main"
    assert session.metadata["model_provider"] == "openai"
    assert [turn.role for turn in session.turns] == ["user", "assistant"]
    assert session.turns[1].content.startswith(
        "[thinking]\nI should inspect the response items first."
    )


def test_codex_adapter_tool_calls(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    session_file = _codex_session_path(tmp_path, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    _write_jsonl(
        session_file,
        [
            {
                "type": "response_item",
                "timestamp": "2026-03-14T12:00:01Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Call the tool."}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-14T12:00:02Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Running it now."}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-14T12:00:03Z",
                "payload": {
                    "type": "function_call",
                    "name": "read_file",
                    "call_id": "call-1",
                    "arguments": '{"path": "README.md"}',
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-14T12:00:04Z",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": {"status": "ok"},
                },
            },
        ],
    )

    adapter = CodexAdapter(db, user_id)
    session = list(adapter.iter_sessions())[0]

    assert session.turns[1].tool_calls == [
        {
            "block_type": "tool_use",
            "tool_name": "read_file",
            "tool_id": "call-1",
            "input": {"path": "README.md"},
        },
        {
            "block_type": "tool_result",
            "tool_use_id": "call-1",
            "content": '{"status": "ok"}',
            "is_error": False,
        },
    ]


def test_health_check_active_with_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    session_file = tmp_path / ".claude" / "projects" / "demo-project" / "session-a.jsonl"
    _write_jsonl(
        session_file,
        [
            {
                "type": "user",
                "timestamp": "2026-03-14T12:00:00Z",
                "message": {"content": "hello"},
            }
        ],
    )

    health = HarnessRegistry().check_health("claude-code")

    assert health.status == "healthy"
    assert health.files_found == 1
    assert health.details["latest_file"] == str(session_file)


def test_health_check_missing_descriptor_returns_not_installed() -> None:
    health = HarnessRegistry().check_health("cursor")

    assert health.status == "not_installed"
    assert health.files_found == 0


def test_all_descriptors_validate() -> None:
    descriptors = load_all_descriptors(DESCRIPTORS_DIR)
    warnings = {
        descriptor.source: validate_descriptor(descriptor)
        for descriptor in descriptors
        if validate_descriptor(descriptor)
    }

    assert len(descriptors) == 7
    assert warnings == {}


def test_external_id_determinism(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structured_file = tmp_path / "structured" / "stable.jsonl"
    _write_jsonl(
        structured_file,
        [
            {
                "sessionId": "stable-session",
                "type": "user",
                "timestamp": "2026-03-14T12:00:00Z",
                "message": {"content": "hello"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-03-14T12:00:01Z",
                "message": {"content": "world"},
            },
        ],
    )
    descriptor = _structured_jsonl_descriptor(structured_file.parent)

    first_session = list(StructuredFileAdapter(db, user_id, descriptor).iter_sessions())[0]
    second_session = list(StructuredFileAdapter(db, user_id, descriptor).iter_sessions())[0]

    assert [turn.metadata["external_id"] for turn in first_session.turns] == [
        turn.metadata["external_id"] for turn in second_session.turns
    ]

    monkeypatch.setenv("HOME", str(tmp_path))
    codex_file = _codex_session_path(tmp_path, "99999999-aaaa-bbbb-cccc-dddddddddddd")
    _write_jsonl(
        codex_file,
        [
            {
                "type": "response_item",
                "timestamp": "2026-03-14T12:00:00Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Stable user prompt with enough content to survive filtering.",
                        }
                    ],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-14T12:00:01Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Stable assistant response with enough content to survive filtering.",
                        }
                    ],
                },
            },
        ],
    )

    first_result = _ingest(CodexAdapter(db, user_id))
    first_external_id_rows = cast(
        list[tuple[str]],
        db.conn.execute(
            "SELECT external_id FROM events WHERE user_id = ? AND source = ? ORDER BY external_id ASC",
            (user_id, "codex"),
        ).fetchall(),
    )
    first_external_ids = [row[0] for row in first_external_id_rows]

    second_result = _ingest(CodexAdapter(db, user_id))
    second_external_id_rows = cast(
        list[tuple[str]],
        db.conn.execute(
            "SELECT external_id FROM events WHERE user_id = ? AND source = ? ORDER BY external_id ASC",
            (user_id, "codex"),
        ).fetchall(),
    )
    second_external_ids = [row[0] for row in second_external_id_rows]

    assert first_result.events_count >= 2
    assert second_result.events_count == 0
    assert first_external_ids == second_external_ids
    assert "codex:99999999-aaaa-bbbb-cccc-dddddddddddd:start" in first_external_ids
    assert any(
        external_id.startswith("codex:99999999-aaaa-bbbb-cccc-dddddddddddd:turn:")
        for external_id in first_external_ids
    )
