"""Tests for the Claude Code adapter."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from syke.db import SykeDB
from syke.ingestion.claude_code import ClaudeCodeAdapter


@pytest.fixture
def db(tmp_path):
    with SykeDB(tmp_path / "test.db") as database:
        yield database


@pytest.fixture
def adapter(db):
    return ClaudeCodeAdapter(db, "test_user")


def _write_jsonl(path: Path, lines: Sequence[object]) -> None:
    path.write_text("\n".join(json.dumps(l) for l in lines))


# ---------------------------------------------------------------------------
# _parse_project_session
# ---------------------------------------------------------------------------


class TestParseProjectSession:
    def _make_session_file(self, tmp_path, lines):
        fpath = tmp_path / "ses_abc123.jsonl"
        _write_jsonl(fpath, lines)
        return fpath

    def test_basic_session(self, adapter, tmp_path):
        lines = [
            {
                "type": "user",
                "timestamp": "2024-01-23T10:00:00Z",
                "message": {"content": "Implement a login system"},
                "sessionId": "ses_abc123",
            },
            {
                "type": "assistant",
                "timestamp": "2024-01-23T10:05:00Z",
                "message": {"content": "Sure, I'll implement JWT-based login."},
            },
        ]
        fpath = self._make_session_file(tmp_path, lines)
        event = adapter._parse_project_session(fpath, "~/myproject")
        assert event is not None
        assert "Implement a login system" in event.content
        assert event.metadata["store"] == "project"
        assert event.metadata["project"] == "~/myproject"

    def test_returns_none_for_empty_file(self, adapter, tmp_path):
        fpath = tmp_path / "empty.jsonl"
        fpath.write_text("")
        assert adapter._parse_project_session(fpath, "~/proj") is None

    def test_returns_none_for_no_user_messages(self, adapter, tmp_path):
        lines = [
            {
                "type": "assistant",
                "timestamp": "2024-01-23T10:00:00Z",
                "message": {"content": "Some assistant text"},
            },
        ]
        fpath = self._make_session_file(tmp_path, lines)
        assert adapter._parse_project_session(fpath, "~/proj") is None

    def test_returns_none_for_too_short_content(self, adapter, tmp_path):
        lines = [
            {
                "type": "user",
                "timestamp": "2024-01-23T10:00:00Z",
                "message": {"content": "hi"},
                "sessionId": "ses_x",
            },
        ]
        fpath = self._make_session_file(tmp_path, lines)
        assert adapter._parse_project_session(fpath, "~/proj") is None

    def test_includes_git_branch_in_metadata(self, adapter, tmp_path):
        lines = [
            {
                "type": "user",
                "timestamp": "2024-01-23T10:00:00Z",
                "message": {
                    "content": "Fix the authentication bug in the API rate-limiting middleware"
                },
                "sessionId": "ses_x",
                "gitBranch": "feature/auth-fix",
            },
        ]
        fpath = self._make_session_file(tmp_path, lines)
        event = adapter._parse_project_session(fpath, "~/proj")
        assert event is not None
        assert event.metadata.get("git_branch") == "feature/auth-fix"

    def test_content_capped_at_50k(self, adapter, tmp_path):
        long_content = "x" * 60000
        lines = [
            {
                "type": "user",
                "timestamp": "2024-01-23T10:00:00Z",
                "message": {"content": long_content},
                "sessionId": "ses_x",
            },
        ]
        fpath = self._make_session_file(tmp_path, lines)
        event = adapter._parse_project_session(fpath, "~/proj")
        assert event is not None
        assert len(event.content) <= 50000

    def test_summary_used_for_title(self, adapter, tmp_path):
        lines = [
            {
                "type": "user",
                "timestamp": "2024-01-23T10:00:00Z",
                "message": {
                    "content": "Can you help me implement the payment checkout flow"
                },
                "sessionId": "s",
            },
            {
                "type": "summary",
                "timestamp": "2024-01-23T10:01:00Z",
                "message": {"content": "Implemented OAuth2 authentication flow."},
            },
        ]
        fpath = self._make_session_file(tmp_path, lines)
        event = adapter._parse_project_session(fpath, "~/proj")
        assert event is not None
        assert "OAuth2" in event.title


# ---------------------------------------------------------------------------
# _parse_transcript_session
# ---------------------------------------------------------------------------


class TestParseTranscriptSession:
    def _make_transcript(self, tmp_path, lines):
        fpath = tmp_path / "ses_def456.jsonl"
        _write_jsonl(fpath, lines)
        return fpath

    def test_basic_transcript(self, adapter, tmp_path):
        lines = [
            {
                "type": "user",
                "timestamp": "2024-02-01T09:00:00Z",
                "content": "Write unit tests for the payment module",
            },
            {
                "type": "user",
                "timestamp": "2024-02-01T09:10:00Z",
                "content": "Focus on edge cases for refunds",
            },
        ]
        fpath = self._make_transcript(tmp_path, lines)
        event = adapter._parse_transcript_session(fpath)
        assert event is not None
        assert "payment module" in event.content
        assert event.metadata["store"] == "transcript"

    def test_returns_none_for_empty(self, adapter, tmp_path):
        fpath = tmp_path / "empty.jsonl"
        fpath.write_text("")
        assert adapter._parse_transcript_session(fpath) is None

    def test_returns_none_for_no_user_messages(self, adapter, tmp_path):
        lines = [
            {"type": "tool_use", "timestamp": "2024-02-01T09:00:00Z", "content": "blah"}
        ]
        fpath = self._make_transcript(tmp_path, lines)
        assert adapter._parse_transcript_session(fpath) is None

    def test_extracts_project_from_working_directory(self, adapter, tmp_path):
        lines = [
            {
                "type": "user",
                "timestamp": "2024-02-01T09:00:00Z",
                "content": "Working directory: /Users/me/myproject\nFix the login bug now",
            },
        ]
        fpath = self._make_transcript(tmp_path, lines)
        event = adapter._parse_transcript_session(fpath)
        assert event is not None
        assert event.metadata.get("project") == "/Users/me/myproject"

    def test_tracks_tool_calls(self, adapter, tmp_path):
        lines = [
            {
                "type": "user",
                "timestamp": "2024-02-01T09:00:00Z",
                "content": "Refactor the database layer to use the repository pattern thoroughly",
            },
            {
                "type": "tool_use",
                "timestamp": "2024-02-01T09:01:00Z",
                "tool_name": "Bash",
                "content": "",
            },
            {
                "type": "tool_use",
                "timestamp": "2024-02-01T09:02:00Z",
                "tool_name": "Read",
                "content": "",
            },
        ]
        fpath = self._make_transcript(tmp_path, lines)
        event = adapter._parse_transcript_session(fpath)
        assert event is not None
        assert event.metadata["tool_calls"] == 2


# ---------------------------------------------------------------------------
# ingest integration (project + transcript stores)
# ---------------------------------------------------------------------------


class TestIngest:
    def test_ingests_project_sessions(self, adapter, tmp_path, monkeypatch):
        # Set up fake ~/.claude/projects structure
        claude_dir = tmp_path / ".claude"
        project_dir = claude_dir / "projects" / "-Users-me-myproject"
        project_dir.mkdir(parents=True)

        session_lines = [
            {
                "type": "user",
                "timestamp": "2024-03-01T10:00:00Z",
                "message": {"content": "Build a GraphQL API for the product catalog"},
                "sessionId": "ses_001",
            },
            {
                "type": "assistant",
                "timestamp": "2024-03-01T10:10:00Z",
                "message": {"content": "I'll create a GraphQL schema with resolvers."},
            },
        ]
        _write_jsonl(project_dir / "ses_001.jsonl", session_lines)

        monkeypatch.setenv("HOME", str(tmp_path))

        result = adapter.ingest()
        assert result.events_count == 1
        assert result.source == "claude-code"

    def test_ingests_transcript_sessions(self, adapter, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        transcripts_dir = claude_dir / "transcripts"
        transcripts_dir.mkdir(parents=True)

        session_lines = [
            {
                "type": "user",
                "timestamp": "2024-03-02T14:00:00Z",
                "content": "Optimize the database queries for the reports page",
            },
            {
                "type": "user",
                "timestamp": "2024-03-02T14:05:00Z",
                "content": "Focus on the slow aggregate queries first",
            },
        ]
        _write_jsonl(transcripts_dir / "ses_002.jsonl", session_lines)

        monkeypatch.setenv("HOME", str(tmp_path))

        result = adapter.ingest()
        assert result.events_count == 1

    def test_deduplicates_project_and_transcript(self, adapter, tmp_path, monkeypatch):
        """Same session ID in both stores should only be ingested once."""
        claude_dir = tmp_path / ".claude"
        project_dir = claude_dir / "projects" / "-Users-me-proj"
        transcripts_dir = claude_dir / "transcripts"
        project_dir.mkdir(parents=True)
        transcripts_dir.mkdir(parents=True)

        session_lines = [
            {
                "type": "user",
                "timestamp": "2024-03-01T10:00:00Z",
                "message": {
                    "content": "Implement async task queue for background jobs using Redis"
                },
                "sessionId": "ses_shared",
            },
        ]
        _write_jsonl(project_dir / "ses_shared.jsonl", session_lines)

        transcript_lines = [
            {
                "type": "user",
                "timestamp": "2024-03-01T10:00:00Z",
                "content": "Implement async task queue for background jobs using Redis",
            },
        ]
        _write_jsonl(transcripts_dir / "ses_shared.jsonl", transcript_lines)

        monkeypatch.setenv("HOME", str(tmp_path))

        result = adapter.ingest()
        assert result.events_count == 1

    def test_no_claude_dir_returns_zero(self, adapter, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = adapter.ingest()
        assert result.events_count == 0
