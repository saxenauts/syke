from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

import pytest

from syke.ingestion.chatgpt import ChatGPTAdapter
from syke.ingestion.claude_code import ClaudeCodeAdapter
from syke.ingestion.codex import CodexAdapter
from syke.ingestion.gateway import IngestGateway
from syke.ingestion.github_ import GitHubAdapter
from syke.ingestion.gmail import (
    GmailAdapter,
)
from syke.ingestion.opencode import OpenCodeAdapter


def _write_jsonl(path: Path, lines: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def _make_message(
    msg_id: str = "abc123",
    subject: str = "Test Subject",
    from_: str = "sender@example.com",
    to: str = "receiver@example.com",
    body: str = "hello",
) -> dict[str, object]:
    return {
        "id": msg_id,
        "threadId": "thread-1",
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": from_},
                {"name": "To", "value": to},
            ],
            "body": {"data": body.encode("utf-8").hex()},
        },
    }


def _make_multipart_message(msg_id: str = "multi-1") -> dict[str, object]:
    return {
        "id": msg_id,
        "threadId": "thread-2",
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "Subject", "value": "Multipart"}],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": "706c61696e"},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": "3c703e68746d6c3c2f703e"},
                },
            ],
        },
    }


def _make_nested_multipart_message(msg_id: str = "nested-1") -> dict[str, object]:
    return {
        "id": msg_id,
        "threadId": "thread-3",
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "Subject", "value": "Nested"}],
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": "6e6573746564"},
                        }
                    ],
                }
            ],
        },
    }


def _call_first(obj: object, method_names: list[str], *args, **kwargs):
    for name in method_names:
        method = getattr(obj, name, None)
        if callable(method):
            return method(*args, **kwargs)
    raise AssertionError(f"No supported method found. Tried: {method_names}")


def _count_from_result(result: object) -> int:
    if isinstance(result, int):
        return result
    if isinstance(result, dict):
        for key in ("ingested", "inserted", "created", "new", "count"):
            value = result.get(key)
            if isinstance(value, int):
                return value
    # Handle IngestionResult dataclass (has events_count attr)
    count = getattr(result, "events_count", None)
    if isinstance(count, int):
        return count
    raise AssertionError(f"Could not derive count from result: {result!r}")


@pytest.fixture
def adapter_cc(db, user_id):
    return ClaudeCodeAdapter(db, user_id)


@pytest.fixture
def adapter_gmail(db, user_id):
    return GmailAdapter(db, user_id)


@pytest.fixture
def adapter_github(db, user_id):
    return GitHubAdapter(db, user_id, token="fake-token")


@pytest.fixture
def gateway(db, user_id):
    return IngestGateway(db, user_id)


def _run_cc(adapter_cc: ClaudeCodeAdapter, root: Path) -> int:
    with patch.dict(os.environ, {"HOME": str(root)}):
        result = adapter_cc.ingest()
    return _count_from_result(result)


def _run_gmail(adapter_gmail: GmailAdapter) -> int:
    result = _call_first(adapter_gmail, ["ingest", "sync", "run"])
    return _count_from_result(result)


def _run_github(adapter_github: GitHubAdapter, username: str = "testuser") -> int:
    result = _call_first(adapter_github, ["ingest", "sync", "run"], username=username)
    return _count_from_result(result)


def _run_chatgpt(adapter: ChatGPTAdapter, export_zip: Path) -> int:
    result = adapter.ingest(file_path=str(export_zip))
    return _count_from_result(result)


# Gmail test helper: _make_message is defined at module top


# --- Gateway ---


def test_push_event_returns_ok_for_valid_payload(gateway):
    result = gateway.push(source="test", event_type="note", title="Hello", content="Body.")
    assert result["status"] == "ok"


@pytest.mark.parametrize(
    ("kwargs", "expected_field"),
    [
        (
            {"source": "test", "event_type": "note", "title": "T", "content": ""},
            "content",
        ),
        (
            {"source": "", "event_type": "note", "title": "T", "content": "Body"},
            "source",
        ),
    ],
)
def test_push_event_rejects_missing_fields(gateway, kwargs, expected_field):
    result = gateway.push(**kwargs)
    assert result["status"] == "error"
    assert expected_field in result.get("error", "")


def test_push_event_dedup_external_id(gateway):
    first = gateway.push(
        source="test",
        event_type="note",
        title="A",
        content="Body",
        external_id="dup-1",
    )
    second = gateway.push(
        source="test",
        event_type="note",
        title="A",
        content="Body",
        external_id="dup-1",
    )
    assert first["status"] == "ok"
    assert second["status"] in {"ok", "duplicate"}
    if "event_id" in first and "event_id" in second:
        assert first["event_id"] == second["event_id"]


@pytest.mark.parametrize(
    "metadata",
    [
        "{bad json}",
    ],
)
def test_push_event_rejects_invalid_metadata(gateway, metadata):
    result = gateway.push(
        source="test",
        event_type="note",
        title="Hello",
        content="Body",
        metadata=metadata,
    )
    assert result["status"] == "error"


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        "2026-01-02T03:04:05Z",
    ],
)
def test_push_event_accepts_timestamp_variants(gateway, timestamp):
    kwargs = {
        "source": "test",
        "event_type": "note",
        "title": "Hello",
        "content": "Body",
    }
    if timestamp is not None:
        kwargs["timestamp"] = timestamp
    result = gateway.push(**kwargs)
    assert result["status"] == "ok"


def test_push_batch_returns_partial_errors_for_invalid_element(gateway):
    result = gateway.push_batch(
        [
            {
                "source": "test",
                "event_type": "note",
                "title": "ok",
                "content": "ok",
            },
            {"source": "test", "event_type": "note", "title": "missing-content"},
        ]
    )
    assert result["status"] in {"ok", "partial_error", "partial_errors"}
    assert result.get("errors")


# --- Claude Code ---


@pytest.mark.parametrize(
    "lines",
    [
        [],
        [
            {
                "type": "assistant",
                "timestamp": "2024-01-23T10:00:00Z",
                "message": {"content": "Only assistant"},
            },
        ],
    ],
)
def test_claude_returns_none_when_content_not_usable(adapter_cc, tmp_path, lines):
    session = tmp_path / ".claude" / "projects" / "proj-a" / "session.jsonl"
    _write_jsonl(session, lines)
    count = _run_cc(adapter_cc, tmp_path)
    assert count == 0


_CC_PROJECT_SESSION = [
    {
        "type": "user",
        "timestamp": "2024-01-23T10:00:00Z",
        "message": {
            "content": "Implement a login system with JWT-based authentication and refresh tokens"
        },
        "sessionId": "ses_abc123",
    },
    {
        "type": "assistant",
        "timestamp": "2024-01-23T10:05:00Z",
        "message": {"content": "Sure, I'll implement JWT-based login with proper token rotation."},
    },
]

_CC_TRANSCRIPT_SESSION = [
    {
        "type": "user",
        "timestamp": "2024-01-23T10:00:00Z",
        "content": "Implement a login system with JWT-based authentication and refresh tokens",
        "sessionId": "ses_abc123",
    },
    {
        "type": "assistant",
        "timestamp": "2024-01-23T10:05:00Z",
        "content": "Sure, I'll implement JWT-based login with proper token rotation.",
    },
]


def test_claude_ingests_project_session(adapter_cc, tmp_path):
    session = tmp_path / ".claude" / "projects" / "proj-a" / "ses_abc123.jsonl"
    _write_jsonl(session, _CC_PROJECT_SESSION)
    count = _run_cc(adapter_cc, tmp_path)
    assert count >= 1


def test_claude_ingests_transcript_session(adapter_cc, tmp_path):
    session = tmp_path / ".claude" / "transcripts" / "ses_abc123.jsonl"
    _write_jsonl(session, _CC_TRANSCRIPT_SESSION)
    count = _run_cc(adapter_cc, tmp_path)
    assert count >= 1


def test_claude_deduplicates_across_runs(adapter_cc, db, user_id, tmp_path):
    """Re-ingesting the same session should not duplicate the event in DB."""
    _write_jsonl(tmp_path / ".claude/projects/proj-a/ses_dedup.jsonl", _CC_PROJECT_SESSION)
    _run_cc(adapter_cc, tmp_path)
    first_count = db.count_events(user_id)
    _run_cc(adapter_cc, tmp_path)
    second_count = db.count_events(user_id)
    assert first_count >= 1
    # Event may be re-ingested (mtime-based), but DB dedup via external_id
    # prevents actual duplication in the events table.
    assert second_count == first_count


def test_claude_no_claude_dir_returns_zero(adapter_cc, tmp_path):
    count = _run_cc(adapter_cc, tmp_path)
    assert count == 0


# --- Gmail ---


@pytest.mark.parametrize(
    ("gog_ok", "oauth_ok", "expect_error"),
    [
        (True, True, False),
        (False, False, True),
    ],
)
def test_gmail_backend_selection(adapter_gmail, gog_ok, oauth_ok, expect_error):
    with (
        patch("syke.ingestion.gmail._gog_authenticated", return_value=gog_ok),
        patch("syke.ingestion.gmail._python_oauth_available", return_value=oauth_ok),
        patch("syke.ingestion.gmail._fetch_via_gog", return_value=[]),
        patch("syke.ingestion.gmail._get_python_service"),
        patch("syke.ingestion.gmail._fetch_via_python", return_value=[]),
    ):
        if expect_error:
            with pytest.raises(RuntimeError, match="No Gmail backend available"):
                adapter_gmail.ingest()
        else:
            result = adapter_gmail.ingest(account="test@gmail.com")
            assert result.events_count == 0


def test_gmail_dedup_across_runs(db, user_id):
    with (
        patch("syke.ingestion.gmail._gog_authenticated", return_value=True),
        patch("syke.ingestion.gmail._fetch_via_gog") as mock_fetch,
    ):
        mock_fetch.return_value = [
            _make_message(
                msg_id="dup1",
                subject="First Email",
                body="Hello this is the first email body with enough content",
            ),
            _make_message(
                msg_id="dup2",
                subject="Second Email",
                body="Hello this is the second email body with enough content",
            ),
        ]
        adapter = GmailAdapter(db, user_id)
        result1 = adapter.ingest(account="test@gmail.com")
        assert result1.events_count >= 1  # at least 1 ingested
        result2 = adapter.ingest(account="test@gmail.com")
        assert result2.events_count == 0
        assert db.count_events(user_id) == 2


# --- GitHub ---


def test_github_ingest_with_mocked_api(db, user_id):
    """GitHub ingest with fully mocked internals returns events."""
    from datetime import datetime

    from syke.models import Event

    adapter = GitHubAdapter(db, user_id, token="fake-token")
    profile_event = Event(
        user_id=user_id,
        source="github",
        event_type="github-profile",
        title="GitHub Profile: testuser",
        content="testuser - Builder - SF - 5 repos",
        timestamp=datetime(2020, 1, 1, tzinfo=UTC),
    )
    # Mock all fetch methods to return controlled data
    adapter._fetch_profile = lambda username: [profile_event]  # type: ignore[assignment]
    adapter._api_paginated = lambda url, max_pages=5: []  # type: ignore[assignment]  # repos
    adapter._make_repo_events = lambda repos_raw: []  # type: ignore[assignment]
    adapter._fetch_readmes = lambda username, repos_raw: []  # type: ignore[assignment]
    adapter._fetch_events = lambda username: []  # type: ignore[assignment]
    adapter._fetch_starred = lambda username: []  # type: ignore[assignment]
    result = adapter.ingest(username="testuser")
    assert result.events_count >= 1


# --- ChatGPT ---


def test_chatgpt_ingests_conversations_zip(db, user_id, tmp_path):
    export_zip = tmp_path / "export.zip"
    conversations = [
        {
            "id": "conv-1",
            "title": "Python help",
            "create_time": 1706000000.0,
            "update_time": 1706001000.0,
            "default_model_slug": "gpt-4",
            "mapping": {
                "node1": {
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["How do I sort a list in Python?"]},
                    }
                },
                "node2": {
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {"parts": ["You can use sorted() or list.sort()."]},
                    }
                },
            },
        }
    ]
    with zipfile.ZipFile(export_zip, "w") as zf:
        zf.writestr("conversations.json", json.dumps(conversations))
    adapter = ChatGPTAdapter(db, user_id)
    count = _run_chatgpt(adapter, export_zip)
    assert count >= 1


def test_chatgpt_missing_conversations_file_raises(db, user_id, tmp_path):
    export_zip = tmp_path / "empty.zip"
    with zipfile.ZipFile(export_zip, "w") as zf:
        zf.writestr("readme.txt", "no conversations file")
    adapter = ChatGPTAdapter(db, user_id)
    with pytest.raises(ValueError, match="No conversations.json"):
        _run_chatgpt(adapter, export_zip)


# --- Codex ---


_CODEX_SESSION_FILE = [
    {
        "type": "session_meta",
        "timestamp": "2026-02-03T10:01:10Z",
        "payload": {
            "cwd": "/Users/test/projects/myapp",
            "git": {"branch": "main"},
            "model_provider": "openai",
        },
    },
    {
        "type": "response_item",
        "timestamp": "2026-02-03T10:01:15Z",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Implement a login system with JWT-based authentication and refresh tokens",
                }
            ],
        },
    },
    {
        "type": "response_item",
        "timestamp": "2026-02-03T10:05:00Z",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "I will implement JWT login with proper token rotation and refresh logic.",
                }
            ],
        },
    },
]

_CODEX_HISTORY_ENTRIES = [
    {
        "session_id": "hist-sess-001",
        "ts": 1706000000,
        "text": "How do I sort a list in Python? I need to sort by multiple keys efficiently.",
    },
    {
        "session_id": "hist-sess-001",
        "ts": 1706001000,
        "text": "Can you show me an example with lambda and attrgetter for complex sorting?",
    },
]


@pytest.fixture
def adapter_codex(db, user_id):
    return CodexAdapter(db, user_id)


@pytest.fixture
def adapter_opencode(db, user_id):
    return OpenCodeAdapter(db, user_id)


def _run_codex(adapter_codex: CodexAdapter, root: Path) -> int:
    with patch.dict(os.environ, {"HOME": str(root)}):
        result = adapter_codex.ingest()
    return _count_from_result(result)


def _run_opencode(adapter_opencode: OpenCodeAdapter, root: Path) -> int:
    with patch.dict(os.environ, {"HOME": str(root)}):
        result = adapter_opencode.ingest()
    return _count_from_result(result)


def _write_opencode_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            parent_id TEXT,
            slug TEXT NOT NULL,
            directory TEXT NOT NULL,
            title TEXT NOT NULL,
            version TEXT NOT NULL,
            share_url TEXT,
            summary_additions INTEGER,
            summary_deletions INTEGER,
            summary_files INTEGER,
            summary_diffs TEXT,
            revert TEXT,
            permission TEXT,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            time_compacting INTEGER,
            time_archived INTEGER,
            workspace_id TEXT
        );

        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );

        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        """
    )

    conn.execute(
        """
        INSERT INTO session (
            id, project_id, parent_id, slug, directory, title, version,
            share_url, summary_additions, summary_deletions, summary_files,
            summary_diffs, revert, permission, time_created, time_updated,
            time_compacting, time_archived, workspace_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ses_test_001",
            "global",
            None,
            "",
            "/tmp/project",
            "Test OpenCode Session",
            "1.0.0",
            None,
            1,
            2,
            1,
            None,
            None,
            None,
            1_770_000_000_000,
            1_770_000_120_000,
            None,
            None,
            None,
        ),
    )

    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        (
            "msg_user_001",
            "ses_test_001",
            1_770_000_010_000,
            1_770_000_010_500,
            json.dumps(
                {
                    "role": "user",
                    "time": {"created": 1_770_000_010_000},
                    "variant": "openai/gpt-5.3-codex",
                }
            ),
        ),
    )

    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        (
            "msg_assistant_001",
            "ses_test_001",
            1_770_000_020_000,
            1_770_000_021_000,
            json.dumps(
                {
                    "role": "assistant",
                    "time": {"created": 1_770_000_020_000},
                    "modelID": "gpt-5.3-codex",
                    "providerID": "openai",
                    "tokens": {
                        "input": 11,
                        "output": 23,
                        "reasoning": 7,
                        "cache": {"read": 9, "write": 2},
                    },
                    "stopReason": "end-turn",
                }
            ),
        ),
    )

    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "prt_user_text",
            "msg_user_001",
            "ses_test_001",
            1_770_000_010_010,
            1_770_000_010_010,
            json.dumps({"type": "text", "text": "Help me add OpenCode ingestion"}),
        ),
    )

    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "prt_assistant_reasoning",
            "msg_assistant_001",
            "ses_test_001",
            1_770_000_020_010,
            1_770_000_020_010,
            json.dumps({"type": "reasoning", "text": "I will inspect the schema first."}),
        ),
    )

    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "prt_assistant_tool",
            "msg_assistant_001",
            "ses_test_001",
            1_770_000_020_020,
            1_770_000_020_020,
            json.dumps(
                {
                    "type": "tool",
                    "tool": "read",
                    "callID": "call_test_001",
                    "state": {
                        "status": "completed",
                        "input": {"filePath": "README.md"},
                        "output": "<file>README</file>",
                    },
                }
            ),
        ),
    )

    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "prt_assistant_text",
            "msg_assistant_001",
            "ses_test_001",
            1_770_000_020_030,
            1_770_000_020_030,
            json.dumps({"type": "text", "text": "Done. Added the adapter."}),
        ),
    )

    conn.commit()
    conn.close()


def test_codex_ingests_session_file(adapter_codex, tmp_path):
    session = (
        tmp_path
        / ".codex"
        / "sessions"
        / "2026"
        / "02"
        / "03"
        / "rollout-2026-02-03T10-01-10-019c24aa-5b5c-7163-8bff-9112bf5c34eb.jsonl"
    )
    _write_jsonl(session, _CODEX_SESSION_FILE)
    count = _run_codex(adapter_codex, tmp_path)
    assert count >= 1


def test_codex_ingests_history_fallback(adapter_codex, tmp_path):
    history = tmp_path / ".codex" / "history.jsonl"
    _write_jsonl(history, _CODEX_HISTORY_ENTRIES)
    count = _run_codex(adapter_codex, tmp_path)
    assert count >= 1


@pytest.mark.parametrize(
    ("lines", "expected_count"),
    [
        ([], 0),
        (
            [
                {
                    "type": "response_item",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Only assistant"}],
                    },
                }
            ],
            2,
        ),
    ],
)
def test_codex_returns_expected_count_for_edge_cases(
    adapter_codex, tmp_path, lines, expected_count
):
    session = (
        tmp_path
        / ".codex"
        / "sessions"
        / "2026"
        / "01"
        / "01"
        / "rollout-2026-01-01T00-00-00-019c24aa-5b5c-7163-8bff-9112bf5c34eb.jsonl"
    )
    _write_jsonl(session, lines)
    count = _run_codex(adapter_codex, tmp_path)
    assert count == expected_count


def test_codex_no_codex_dir_returns_zero(adapter_codex, tmp_path):
    count = _run_codex(adapter_codex, tmp_path)
    assert count == 0


def test_codex_dedup_across_runs(adapter_codex, db, user_id, tmp_path):
    """Re-ingesting the same session should not duplicate the event in DB."""
    _write_jsonl(
        tmp_path
        / ".codex/sessions/2026/02/03/rollout-2026-02-03T10-01-10-019c24aa-5b5c-7163-8bff-9112bf5c34eb.jsonl",
        _CODEX_SESSION_FILE,
    )
    _run_codex(adapter_codex, tmp_path)
    first_count = db.count_events(user_id)
    _run_codex(adapter_codex, tmp_path)
    second_count = db.count_events(user_id)
    assert first_count >= 1
    assert second_count == first_count


def test_opencode_ingests_sqlite_session(adapter_opencode, tmp_path):
    opencode_db = tmp_path / ".local" / "share" / "opencode" / "opencode.db"
    _write_opencode_db(opencode_db)

    count = _run_opencode(adapter_opencode, tmp_path)
    assert count >= 5


def test_opencode_no_db_returns_zero(adapter_opencode, tmp_path):
    count = _run_opencode(adapter_opencode, tmp_path)
    assert count == 0


def test_opencode_dedup_across_runs(adapter_opencode, db, user_id, tmp_path):
    opencode_db = tmp_path / ".local" / "share" / "opencode" / "opencode.db"
    _write_opencode_db(opencode_db)

    _run_opencode(adapter_opencode, tmp_path)
    first_count = db.count_events(user_id)

    _run_opencode(adapter_opencode, tmp_path)
    second_count = db.count_events(user_id)

    assert first_count >= 1
    assert second_count == first_count
