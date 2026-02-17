"""Tests for MCP server tool functions — adapter integration + healing."""

from __future__ import annotations

import json
import os
from datetime import datetime
from unittest.mock import patch

import pytest

from syke.db import SykeDB
from syke.distribution.mcp_server import create_server
from syke.models import Event, UserProfile


@pytest.fixture
def server(db, user_id):
    """Create MCP server backed by a temp DB, patching user_db_path."""
    import syke.distribution.mcp_server as mod

    original = mod.user_db_path
    mod.user_db_path = lambda uid: db.db_path
    srv = create_server(user_id)
    yield srv
    mod.user_db_path = original


def _seed_profile(db: SykeDB, user_id: str) -> UserProfile:
    """Insert a minimal profile into the DB."""
    profile = UserProfile(
        user_id=user_id,
        identity_anchor="Test user is a builder of test infrastructure.",
        active_threads=[
            {
                "name": "Testing Syke",
                "description": "Writing MCP server tests.",
                "intensity": "high",
                "platforms": ["claude-code"],
                "recent_signals": ["Feb 14: writing tests"],
            }
        ],
        recent_detail="Working on test coverage for hackathon submission.",
        background_context="Long history of building test suites.",
        world_state="Currently building Syke v0.2 for Claude Code Hackathon. Main focus: ask() tool implementation.",
        voice_patterns={
            "tone": "Precise and methodical",
            "vocabulary_notes": ["test", "assert", "fixture"],
            "communication_style": "Direct, expects coverage.",
            "examples": ["Make sure it passes."],
        },
        sources=["claude-code", "chatgpt"],
        events_count=100,
        model="claude-opus-4-6",
        cost_usd=0.50,
    )
    db.save_profile(profile)
    return profile


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


# ── get_profile ──────────────────────────────────────────────────────

class TestGetProfile:
    def test_returns_json_profile(self, server, db, user_id):
        _seed_profile(db, user_id)
        result = _call_tool(server, "get_profile", format="json")
        data = json.loads(result)
        assert data["user_id"] == user_id
        assert "identity_anchor" in data

    def test_returns_markdown_profile(self, server, db, user_id):
        _seed_profile(db, user_id)
        result = _call_tool(server, "get_profile", format="markdown")
        assert "# " in result or "builder" in result

    def test_returns_claude_md(self, server, db, user_id):
        _seed_profile(db, user_id)
        result = _call_tool(server, "get_profile", format="claude-md")
        assert "About" in result or user_id in result

    def test_no_profile_returns_error(self, server, db, user_id):
        result = _call_tool(server, "get_profile", format="json")
        data = json.loads(result)
        assert "error" in data

    def test_invalid_format_returns_error(self, server, db, user_id):
        """Invalid format returns error JSON, doesn't crash."""
        _seed_profile(db, user_id)
        result = _call_tool(server, "get_profile", format="invalid-format")
        data = json.loads(result)
        assert "error" in data
        assert "unknown format" in data["error"].lower() or "invalid" in data["error"].lower()


# ── query_timeline ───────────────────────────────────────────────────

class TestQueryTimeline:
    def test_returns_events(self, server, db, user_id):
        _seed_events(db, user_id, 3)
        result = _call_tool(server, "query_timeline", limit=10)
        events = json.loads(result)
        assert len(events) == 3

    def test_always_summarizes(self, server, db, user_id):
        _seed_events(db, user_id, 3)
        result = _call_tool(server, "query_timeline", limit=10)
        events = json.loads(result)
        assert len(events) == 3
        for e in events:
            assert "content" not in e
            assert "content_length" in e
            assert e["content_length"] > 0

    def test_filter_by_source(self, server, db, user_id):
        _seed_events(db, user_id, 3)
        # No events for this source
        result = _call_tool(server, "query_timeline", source="github")
        events = json.loads(result)
        assert len(events) == 0

    def test_filter_by_since(self, server, db, user_id):
        _seed_events(db, user_id, 5)
        result = _call_tool(server, "query_timeline", since="2026-02-13")
        events = json.loads(result)
        assert len(events) >= 2  # events on 13th and 14th

    def test_limit_respected(self, server, db, user_id):
        _seed_events(db, user_id, 10)
        result = _call_tool(server, "query_timeline", limit=3)
        events = json.loads(result)
        assert len(events) == 3

    def test_empty_timeline(self, server, db, user_id):
        result = _call_tool(server, "query_timeline")
        events = json.loads(result)
        assert events == []

    def test_summary_false_returns_full_content(self, server, db, user_id):
        """With summary=False, full event content should be returned."""
        _seed_events(db, user_id, 2)
        result = _call_tool(server, "query_timeline", limit=10, summary=False)
        events = json.loads(result)
        assert len(events) == 2
        for e in events:
            assert "content" in e
            assert "ALMA research discussion" in e["content"]
            assert "content_length" not in e


# ── get_manifest ─────────────────────────────────────────────────────

class TestGetManifest:
    def test_returns_status(self, server, db, user_id):
        _seed_events(db, user_id, 5)
        _seed_profile(db, user_id)
        result = _call_tool(server, "get_manifest")
        status = json.loads(result)
        assert isinstance(status, dict)

    def test_empty_db(self, server, db, user_id):
        result = _call_tool(server, "get_manifest")
        status = json.loads(result)
        assert isinstance(status, dict)

    def test_freshness_fields(self, server, db, user_id):
        _seed_events(db, user_id, 3)
        _seed_profile(db, user_id)
        result = _call_tool(server, "get_manifest")
        status = json.loads(result)
        assert "profile_age_hours" in status
        assert "profile_fresh" in status
        assert "events_since_profile" in status

    def test_profile_costs_included(self, server, db, user_id):
        """Manifest includes profile cost stats when profiles exist."""
        _seed_profile(db, user_id)
        result = _call_tool(server, "get_manifest")
        status = json.loads(result)
        assert "profile_costs" in status
        costs = status["profile_costs"]
        assert "run_count" in costs
        assert costs["run_count"] >= 1
        assert "total_cost_usd" in costs
        assert "avg_cost_usd" in costs
        assert "last_run_cost_usd" in costs

    def test_no_profile_costs_without_profiles(self, server, db, user_id):
        """Manifest omits profile_costs when no profiles exist."""
        _seed_events(db, user_id, 3)
        result = _call_tool(server, "get_manifest")
        status = json.loads(result)
        assert "profile_costs" not in status


# ── get_event ────────────────────────────────────────────────────────

class TestGetEvent:
    def test_returns_full_content(self, server, db, user_id):
        _seed_events(db, user_id, 3)
        # Get an event ID via timeline
        timeline = json.loads(_call_tool(server, "query_timeline", limit=1))
        event_id = timeline[0]["id"]

        result = _call_tool(server, "get_event", event_id=event_id)
        event = json.loads(result)
        assert event["id"] == event_id
        assert "content" in event
        assert len(event["content"]) > 0
        assert "ALMA research discussion" in event["content"]

    def test_not_found(self, server, db, user_id):
        result = _call_tool(server, "get_event", event_id="nonexistent-id")
        data = json.loads(result)
        assert "error" in data

    def test_returns_all_fields(self, server, db, user_id):
        """get_event returns all event fields including metadata."""
        _seed_events(db, user_id, 1)
        timeline = json.loads(_call_tool(server, "query_timeline", limit=1))
        event_id = timeline[0]["id"]

        result = _call_tool(server, "get_event", event_id=event_id)
        event = json.loads(result)
        assert "source" in event
        assert "timestamp" in event
        assert "event_type" in event
        assert "title" in event
        assert "content" in event


# ── search_events ────────────────────────────────────────────────────

class TestSearchEvents:
    def test_finds_matching_events(self, server, db, user_id):
        _seed_events(db, user_id, 5)
        result = _call_tool(server, "search_events", query="ALMA")
        events = json.loads(result)
        assert len(events) >= 1

    def test_no_results(self, server, db, user_id):
        _seed_events(db, user_id, 3)
        result = _call_tool(server, "search_events", query="nonexistent_xyz")
        events = json.loads(result)
        assert len(events) == 0

    def test_always_summarizes(self, server, db, user_id):
        _seed_events(db, user_id, 5)
        result = _call_tool(server, "search_events", query="ALMA")
        events = json.loads(result)
        assert len(events) >= 1
        for e in events:
            assert "content" not in e
            assert "content_length" in e

    def test_limit_respected(self, server, db, user_id):
        _seed_events(db, user_id, 10)
        result = _call_tool(server, "search_events", query="test", limit=2)
        events = json.loads(result)
        assert len(events) <= 2

    def test_summary_false_returns_full_content(self, server, db, user_id):
        """With summary=False, full event content should be returned."""
        _seed_events(db, user_id, 5)
        result = _call_tool(server, "search_events", query="ALMA", summary=False)
        events = json.loads(result)
        assert len(events) >= 1
        for e in events:
            assert "content" in e
            assert "ALMA research discussion" in e["content"]
            assert "content_length" not in e


# ── push_event ───────────────────────────────────────────────────────

class TestPushEvent:
    def test_push_and_query(self, server, db, user_id):
        result = _call_tool(
            server,
            "push_event",
            source="claude-code",
            event_type="observation",
            title="User started new project",
            content="Working on visualization website.",
        )
        data = json.loads(result)
        assert data["status"] == "ok"

        # Verify it shows up in timeline
        events_result = _call_tool(server, "query_timeline", source="claude-code")
        events = json.loads(events_result)
        assert len(events) == 1
        assert events[0]["title"] == "User started new project"

    def test_push_with_external_id_dedup(self, server, db, user_id):
        kwargs = dict(
            source="test",
            event_type="observation",
            title="Same event",
            content="Dedup test.",
            external_id="dedup-123",
        )
        result1 = json.loads(_call_tool(server, "push_event", **kwargs))
        assert result1["status"] == "ok"

        result2 = json.loads(_call_tool(server, "push_event", **kwargs))
        assert result2["status"] == "duplicate"

    def test_push_invalid_metadata(self, server, db, user_id):
        result = _call_tool(
            server,
            "push_event",
            source="test",
            event_type="note",
            title="Bad metadata",
            content="Has broken JSON.",
            metadata="not-valid-json{",
        )
        data = json.loads(result)
        assert data["status"] == "error"
        assert "metadata" in data["error"].lower() or "json" in data["error"].lower()

    def test_push_list_metadata_returns_error(self, server, db, user_id):
        """push_event with list metadata JSON returns error, doesn't crash."""
        result = _call_tool(
            server,
            "push_event",
            source="test",
            event_type="note",
            title="List meta",
            content="Has list metadata.",
            metadata="[1, 2, 3]",
        )
        data = json.loads(result)
        assert data["status"] == "error"
        assert "object" in data["error"].lower() or "dict" in data["error"].lower()


# ── push_events (batch) ──────────────────────────────────────────────

class TestPushEvents:
    def test_batch_push(self, server, db, user_id):
        events = [
            {"source": "test", "event_type": "note", "title": f"Batch {i}", "content": f"Content {i}"}
            for i in range(3)
        ]
        result = _call_tool(server, "push_events", events_json=json.dumps(events))
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["inserted"] == 3

    def test_batch_invalid_json(self, server, db, user_id):
        result = _call_tool(server, "push_events", events_json="not json")
        data = json.loads(result)
        assert data["status"] == "error"

    def test_batch_not_array(self, server, db, user_id):
        result = _call_tool(server, "push_events", events_json='{"not": "array"}')
        data = json.loads(result)
        assert data["status"] == "error"
        assert "array" in data["error"].lower()

    def test_batch_with_string_metadata(self, server, db, user_id):
        """push_events with string metadata in event dicts — should parse and insert (Bug 1 fix)."""
        events = [
            {
                "source": "test",
                "event_type": "observation",
                "title": "String meta batch",
                "content": "Event with JSON string metadata via batch.",
                "metadata": '{"session": "abc123"}',
            }
        ]
        result = _call_tool(server, "push_events", events_json=json.dumps(events))
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["inserted"] == 1


# ── ask ──────────────────────────────────────────────────────────────

class TestAsk:
    def test_ask_no_data(self, server, db, user_id):
        """Ask on empty DB returns no-data message without API call."""
        result = _call_tool(server, "ask", question="What is the user working on?")
        assert "no data" in result.lower() or "setup" in result.lower()

    @pytest.mark.skipif(
        not os.getenv("ANTHROPIC_API_KEY"),
        reason="ask() tool requires ANTHROPIC_API_KEY for agentic exploration"
    )
    def test_ask_with_mocked_agent(self, server, db, user_id):
        """Ask with seeded data returns mocked answer."""
        _seed_events(db, user_id, 5)
        _seed_profile(db, user_id)

        with patch("syke.distribution.ask_agent.ask", return_value="They are building Syke."):
            result = _call_tool(server, "ask", question="What are they working on?")
            assert "Syke" in result


# ── Healing / Recovery ───────────────────────────────────────────────

class TestHealing:
    def test_profile_after_push_cycle(self, server, db, user_id):
        """Push events, seed profile, verify both tools work in sequence."""
        # Push first
        _call_tool(
            server, "push_event",
            source="claude-code", event_type="observation",
            title="Working on Syke", content="Building MCP tests.",
        )
        # Then seed profile
        _seed_profile(db, user_id)
        # Both tools should work
        profile = json.loads(_call_tool(server, "get_profile", format="json"))
        assert profile["user_id"] == user_id

        events = json.loads(_call_tool(server, "query_timeline"))
        assert len(events) >= 1

    def test_search_on_empty_db(self, server, db, user_id):
        """Search on empty DB should return empty, not crash."""
        result = _call_tool(server, "search_events", query="anything")
        events = json.loads(result)
        assert events == []

    def test_manifest_on_empty_db(self, server, db, user_id):
        """Manifest on fresh DB should return a valid response."""
        result = _call_tool(server, "get_manifest")
        status = json.loads(result)
        assert isinstance(status, dict)


# ── Helper ───────────────────────────────────────────────────────────

def _call_tool(server: object, tool_name: str, **kwargs) -> str:
    """Call a registered FastMCP tool function by name."""
    import asyncio
    import inspect

    # FastMCP stores tools in _tool_manager; access the callable directly
    manager = server._tool_manager
    tool = manager._tools[tool_name]
    # Call the underlying function
    result = tool.fn(**kwargs)

    # If result is a coroutine, run it
    if inspect.iscoroutine(result):
        return asyncio.run(result)
    return result
