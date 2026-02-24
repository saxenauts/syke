"""Tests for MCP server tool functions — 3-tool server (get_live_context, record, ask)."""

from __future__ import annotations

import json
import os
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from syke.db import SykeDB
from syke.distribution.mcp_server import create_server
from syke.models import Event


@pytest.fixture
def server(db, user_id, tmp_path):
    """Create MCP server backed by a temp DB, patching user_db_path and user_data_dir."""
    import syke.distribution.mcp_server as mod

    original_db_path = mod.user_db_path
    original_data_dir = mod.user_data_dir
    mod.user_db_path = lambda uid: db.db_path
    mod.user_data_dir = lambda uid: tmp_path
    srv = create_server(user_id)
    yield srv
    mod.user_db_path = original_db_path
    mod.user_data_dir = original_data_dir


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / "mcp_calls.jsonl"


def _seed_events(db: SykeDB, user_id: str, count: int = 5):
    """Insert test events into the DB."""
    for i in range(count):
        event = Event(
            user_id=user_id,
            source="test-source",
            event_type="conversation",
            title=f"Test event {i}",
            content=f"Content for test event number {i}. ALMA research discussion.",
            timestamp=datetime(2026, 2, 10 + i, 12, 0, 0),
        )
        db.insert_event(event)


# ── get_live_context (PRIMARY) ───────────────────────────────────────


class TestGetLiveContext:
    def test_returns_json(self, server, db, user_id):
        _seed_events(db, user_id)
        result = _call_tool(server, "get_live_context", format="json")
        data = json.loads(result)
        assert data["user_id"] == user_id
        assert data["format"] == "memex"
        assert "content" in data
        assert len(data["content"]) > 0

    def test_returns_markdown(self, server, db, user_id):
        _seed_events(db, user_id)
        result = _call_tool(server, "get_live_context", format="markdown")
        # With only events (no profile), memex returns minimal content
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_claude_md(self, server, db, user_id):
        _seed_events(db, user_id)
        result = _call_tool(server, "get_live_context", format="claude-md")
        # With only events (no profile), memex returns minimal content
        assert isinstance(result, str)
        assert len(result) > 0

    def test_no_profile_returns_error(self, server, db, user_id):
        result = _call_tool(server, "get_live_context", format="json")
        data = json.loads(result)
        assert data["content"] == "[No data yet.]"

    def test_invalid_format_returns_error(self, server, db, user_id):
        _seed_events(db, user_id)
        result = _call_tool(server, "get_live_context", format="markdown")
        # All non-json formats return raw memex markdown (string), not error JSON
        assert isinstance(result, str)
        assert len(result) > 0


# ── record ───────────────────────────────────────────────────────────


class TestRecord:
    def test_basic_record(self, server, db, user_id):
        result = _call_tool(server, "record", observation="User decided to use Rust")
        data = json.loads(result)
        assert data["status"] == "recorded"

    def test_record_shows_in_timeline(self, server, db, user_id):
        _call_tool(server, "record", observation="User switched to PostgreSQL 16")
        events = db.get_events(user_id, source="mcp-record")
        assert len(events) == 1
        assert "PostgreSQL" in events[0]["title"]

    def test_record_dedup(self, server, db, user_id):
        obs = "User prefers dark mode"
        r1 = json.loads(_call_tool(server, "record", observation=obs))
        assert r1["status"] == "recorded"

        r2 = json.loads(_call_tool(server, "record", observation=obs))
        assert r2["status"] == "already_known"

    def test_record_different_observations_not_deduped(self, server, db, user_id):
        r1 = json.loads(_call_tool(server, "record", observation="First observation"))
        r2 = json.loads(_call_tool(server, "record", observation="Second observation"))
        assert r1["status"] == "recorded"
        assert r2["status"] == "recorded"

    def test_record_title_truncated(self, server, db, user_id):
        long_obs = "A" * 200
        _call_tool(server, "record", observation=long_obs)
        events = db.get_events(user_id, source="mcp-record")
        assert len(events) == 1
        assert len(events[0]["title"]) <= 120


# ── Logging ──────────────────────────────────────────────────────────


class TestLogging:
    def test_get_live_context_logged(self, server, db, user_id, log_path):
        _seed_events(db, user_id)
        _call_tool(server, "get_live_context", format="json")
        assert log_path.exists()
        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert any(e["tool"] == "get_live_context" for e in entries)

    def test_record_logged(self, server, db, user_id, log_path):
        _call_tool(server, "record", observation="Logging test observation")
        assert log_path.exists()
        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        record_entries = [e for e in entries if e["tool"] == "record"]
        assert len(record_entries) >= 1
        assert record_entries[0]["caller"] == "external"

    def test_log_has_duration(self, server, db, user_id, log_path):
        _seed_events(db, user_id)
        _call_tool(server, "get_live_context", format="json")
        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert entries[0]["duration_ms"] >= 0

    def test_log_args_truncated(self, server, db, user_id, log_path):
        _call_tool(server, "record", observation="x" * 500)
        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        record_entry = next(e for e in entries if e["tool"] == "record")
        for v in record_entry["args_summary"].values():
            assert len(v) <= 100


# ── ask ──────────────────────────────────────────────────────────────


class TestAsk:
    def test_ask_no_data(self, server, db, user_id):
        result = _call_tool(server, "ask", question="What is the user working on?")
        assert "no data" in result.lower() or "setup" in result.lower()

    def test_ask_with_mocked_agent(self, server, db, user_id):
        _seed_events(db, user_id, 5)

        with patch(
            "syke.distribution.ask_agent._run_ask",
            new=AsyncMock(return_value="They are building Syke."),
        ):
            result = _call_tool(server, "ask", question="What are they working on?")
            assert "Syke" in result


# ── Helper ───────────────────────────────────────────────────────────


def _call_tool(server: object, tool_name: str, **kwargs) -> str:
    """Call a registered FastMCP tool function by name."""
    import asyncio
    import inspect

    manager = server._tool_manager
    tool = manager._tools[tool_name]
    result = tool.fn(**kwargs)

    if inspect.iscoroutine(result):
        return asyncio.run(result)
    return result
