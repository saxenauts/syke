"""Tests for the Claude Code adapter."""

from __future__ import annotations

import json
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


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(l) for l in lines))


# ---------------------------------------------------------------------------
# _make_title
# ---------------------------------------------------------------------------

class TestMakeTitle:
    def test_basic_text(self, adapter):
        assert adapter._make_title("Build a REST API") == "Build a REST API"

    def test_strips_greeting_hey(self, adapter):
        result = adapter._make_title("hey, can you help me fix this bug please")
        assert not result.lower().startswith("hey")

    def test_strips_greeting_please(self, adapter):
        result = adapter._make_title("please refactor this authentication module")
        assert not result.lower().startswith("please")

    def test_strips_can_you(self, adapter):
        result = adapter._make_title("can you add tests for the payment service")
        assert not result.lower().startswith("can you")

    def test_doesnt_strip_if_remainder_too_short(self, adapter):
        # "please fix" → "fix" is only 3 chars — should NOT strip
        result = adapter._make_title("please fix")
        assert result == "please fix"

    def test_prefers_summary_first_sentence(self, adapter):
        summary = "Refactored the auth module. Also cleaned up tests."
        result = adapter._make_title("Some long user message text here", summary=summary)
        assert result == "Refactored the auth module."

    def test_truncates_at_120_chars(self, adapter):
        long_text = "a" * 200
        result = adapter._make_title(long_text)
        assert len(result) <= 120

    def test_truncates_at_word_boundary(self, adapter):
        # Construct text where a space falls before position 120
        text = ("hello world " * 10) + "extra"
        result = adapter._make_title(text)
        assert not result.endswith(" ")
        assert len(result) <= 120

    def test_empty_text_returns_untitled(self, adapter):
        assert adapter._make_title("") == "Untitled session"

    def test_capitalizes_after_greeting_strip(self, adapter):
        result = adapter._make_title("can you implement oauth2 for the app")
        assert result[0].isupper()


# ---------------------------------------------------------------------------
# _strip_system_tags
# ---------------------------------------------------------------------------

class TestStripSystemTags:
    def test_strips_system_reminder(self, adapter):
        text = "before\n<system-reminder>\nsome content\n</system-reminder>\nafter"
        result = adapter._strip_system_tags(text)
        assert "some content" not in result
        assert "before" in result
        assert "after" in result

    def test_strips_extremely_important(self, adapter):
        text = "start\n<EXTREMELY_IMPORTANT>\nhidden\n</EXTREMELY_IMPORTANT>\nend"
        result = adapter._strip_system_tags(text)
        assert "hidden" not in result
        assert "start" in result
        assert "end" in result

    def test_content_before_tag_preserved(self, adapter):
        text = "keep this<system-reminder>\nremove\n</system-reminder>"
        result = adapter._strip_system_tags(text)
        assert "keep this" in result

    def test_no_tags_unchanged(self, adapter):
        text = "normal content without tags"
        assert adapter._strip_system_tags(text) == text


# ---------------------------------------------------------------------------
# _strip_agent_scaffolding
# ---------------------------------------------------------------------------

class TestStripAgentScaffolding:
    def test_strips_notepad_section(self, adapter):
        text = "User request\n## Notepad Location\n/some/path\n## Something else\nkeep this"
        result = adapter._strip_agent_scaffolding(text)
        assert "Notepad Location" not in result
        assert "/some/path" not in result
        assert "keep this" in result

    def test_keeps_non_scaffolding_content(self, adapter):
        text = "## My custom section\nsome content\n## Another section\nmore content"
        result = adapter._strip_agent_scaffolding(text)
        assert "My custom section" in result
        assert "some content" in result

    def test_no_scaffolding_unchanged(self, adapter):
        text = "Normal content here\nNo scaffolding"
        assert adapter._strip_agent_scaffolding(text) == text


# ---------------------------------------------------------------------------
# _parse_timestamp
# ---------------------------------------------------------------------------

class TestParseTimestamp:
    def test_iso_string(self, adapter):
        line = {"timestamp": "2024-01-23T10:30:00Z"}
        ts = adapter._parse_timestamp(line)
        assert ts is not None
        assert ts.year == 2024
        assert ts.month == 1

    def test_iso_string_with_offset(self, adapter):
        line = {"timestamp": "2024-01-23T10:30:00+00:00"}
        ts = adapter._parse_timestamp(line)
        assert ts is not None

    def test_epoch_millis(self, adapter):
        # 1706000000000 ms = Jan 23 2024 ish
        line = {"timestamp": 1706000000000}
        ts = adapter._parse_timestamp(line)
        assert ts is not None
        assert ts.tzinfo is not None

    def test_missing_timestamp(self, adapter):
        assert adapter._parse_timestamp({}) is None

    def test_invalid_string(self, adapter):
        assert adapter._parse_timestamp({"timestamp": "not-a-date"}) is None


# ---------------------------------------------------------------------------
# _extract_message_content
# ---------------------------------------------------------------------------

class TestExtractMessageContent:
    def test_project_store_string_content(self, adapter):
        line = {"message": {"content": "hello world"}}
        assert adapter._extract_message_content(line) == "hello world"

    def test_project_store_list_content(self, adapter):
        line = {"message": {"content": [{"type": "text", "text": "part one"}, {"type": "text", "text": "part two"}]}}
        result = adapter._extract_message_content(line)
        assert "part one" in result
        assert "part two" in result

    def test_no_message_key_returns_empty(self, adapter):
        # When there is no "message" key, msg defaults to {} and {}.get("content","")
        # returns "" (a string), so the method returns "" rather than falling through
        # to the top-level "content" key. This is the actual behaviour; transcript
        # sessions are parsed via direct msg.get("content") in _parse_transcript_session.
        line = {"content": "transcript message"}
        assert adapter._extract_message_content(line) == ""

    def test_empty_returns_empty_string(self, adapter):
        assert adapter._extract_message_content({}) == ""


# ---------------------------------------------------------------------------
# _decode_project_dir
# ---------------------------------------------------------------------------

class TestDecodeProjectDir:
    def test_known_path(self, adapter, tmp_path):
        # Create a real directory so DFS can resolve it
        project = tmp_path / "myproject"
        project.mkdir()
        # Encode as Claude Code would: replace / with -
        dirname = str(tmp_path / "myproject").lstrip("/").replace("/", "-")
        result = adapter._decode_project_dir(dirname)
        # Should decode to tmp_path/myproject
        assert "myproject" in result

    def test_fallback_for_missing_path(self, adapter):
        # Non-existent path falls back to naive replacement
        result = adapter._decode_project_dir("-nonexistent-path-here")
        assert isinstance(result, str)
        assert len(result) > 0


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
            {"type": "user", "timestamp": "2024-01-23T10:00:00Z",
             "message": {"content": "Implement a login system"}, "sessionId": "ses_abc123"},
            {"type": "assistant", "timestamp": "2024-01-23T10:05:00Z",
             "message": {"content": "Sure, I'll implement JWT-based login."}},
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
            {"type": "assistant", "timestamp": "2024-01-23T10:00:00Z",
             "message": {"content": "Some assistant text"}},
        ]
        fpath = self._make_session_file(tmp_path, lines)
        assert adapter._parse_project_session(fpath, "~/proj") is None

    def test_returns_none_for_too_short_content(self, adapter, tmp_path):
        lines = [
            {"type": "user", "timestamp": "2024-01-23T10:00:00Z",
             "message": {"content": "hi"}, "sessionId": "ses_x"},
        ]
        fpath = self._make_session_file(tmp_path, lines)
        assert adapter._parse_project_session(fpath, "~/proj") is None

    def test_includes_git_branch_in_metadata(self, adapter, tmp_path):
        lines = [
            {"type": "user", "timestamp": "2024-01-23T10:00:00Z",
             "message": {"content": "Fix the authentication bug in the API rate-limiting middleware"},
             "sessionId": "ses_x", "gitBranch": "feature/auth-fix"},
        ]
        fpath = self._make_session_file(tmp_path, lines)
        event = adapter._parse_project_session(fpath, "~/proj")
        assert event is not None
        assert event.metadata.get("git_branch") == "feature/auth-fix"

    def test_content_capped_at_50k(self, adapter, tmp_path):
        long_content = "x" * 60000
        lines = [
            {"type": "user", "timestamp": "2024-01-23T10:00:00Z",
             "message": {"content": long_content}, "sessionId": "ses_x"},
        ]
        fpath = self._make_session_file(tmp_path, lines)
        event = adapter._parse_project_session(fpath, "~/proj")
        assert event is not None
        assert len(event.content) <= 50000

    def test_summary_used_for_title(self, adapter, tmp_path):
        lines = [
            {"type": "user", "timestamp": "2024-01-23T10:00:00Z",
             "message": {"content": "Can you help me implement the payment checkout flow"}, "sessionId": "s"},
            {"type": "summary", "timestamp": "2024-01-23T10:01:00Z",
             "message": {"content": "Implemented OAuth2 authentication flow."}},
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
            {"type": "user", "timestamp": "2024-02-01T09:00:00Z",
             "content": "Write unit tests for the payment module"},
            {"type": "user", "timestamp": "2024-02-01T09:10:00Z",
             "content": "Focus on edge cases for refunds"},
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
        lines = [{"type": "tool_use", "timestamp": "2024-02-01T09:00:00Z", "content": "blah"}]
        fpath = self._make_transcript(tmp_path, lines)
        assert adapter._parse_transcript_session(fpath) is None

    def test_extracts_project_from_working_directory(self, adapter, tmp_path):
        lines = [
            {"type": "user", "timestamp": "2024-02-01T09:00:00Z",
             "content": "Working directory: /Users/me/myproject\nFix the login bug now"},
        ]
        fpath = self._make_transcript(tmp_path, lines)
        event = adapter._parse_transcript_session(fpath)
        assert event is not None
        assert event.metadata.get("project") == "/Users/me/myproject"

    def test_tracks_tool_calls(self, adapter, tmp_path):
        lines = [
            {"type": "user", "timestamp": "2024-02-01T09:00:00Z",
             "content": "Refactor the database layer to use the repository pattern thoroughly"},
            {"type": "tool_use", "timestamp": "2024-02-01T09:01:00Z",
             "tool_name": "Bash", "content": ""},
            {"type": "tool_use", "timestamp": "2024-02-01T09:02:00Z",
             "tool_name": "Read", "content": ""},
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
            {"type": "user", "timestamp": "2024-03-01T10:00:00Z",
             "message": {"content": "Build a GraphQL API for the product catalog"},
             "sessionId": "ses_001"},
            {"type": "assistant", "timestamp": "2024-03-01T10:10:00Z",
             "message": {"content": "I'll create a GraphQL schema with resolvers."}},
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
            {"type": "user", "timestamp": "2024-03-02T14:00:00Z",
             "content": "Optimize the database queries for the reports page"},
            {"type": "user", "timestamp": "2024-03-02T14:05:00Z",
             "content": "Focus on the slow aggregate queries first"},
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
            {"type": "user", "timestamp": "2024-03-01T10:00:00Z",
             "message": {"content": "Implement async task queue for background jobs using Redis"},
             "sessionId": "ses_shared"},
        ]
        _write_jsonl(project_dir / "ses_shared.jsonl", session_lines)

        transcript_lines = [
            {"type": "user", "timestamp": "2024-03-01T10:00:00Z",
             "content": "Implement async task queue for background jobs using Redis"},
        ]
        _write_jsonl(transcripts_dir / "ses_shared.jsonl", transcript_lines)

        monkeypatch.setenv("HOME", str(tmp_path))

        result = adapter.ingest()
        assert result.events_count == 1

    def test_no_claude_dir_returns_zero(self, adapter, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = adapter.ingest()
        assert result.events_count == 0
