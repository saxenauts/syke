"""Tests for the agentic perception engine.

Layer 1: Tool unit tests (no LLM, no Agent SDK) — call tool functions directly.
Layer 2: AgenticPerceiver tests (mocked Agent SDK) — verify the orchestration.
Layer 3: Delta merge tests — verify incremental profile merging.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from syke.db import SykeDB
from syke.models import Event, UserProfile
from syke.perception.agentic_perceiver import (
    merge_delta_into_profile,
)
from syke.perception.tools import create_perception_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _populate_multi_platform(db: SykeDB, user_id: str) -> None:
    """Populate the DB with multi-platform events for testing."""
    now = datetime.now(UTC)
    events = [
        Event(
            user_id=user_id, source="github",
            timestamp=now - timedelta(days=1),
            event_type="commit", title="Fix auth bug",
            content="Fixed authentication issue in login flow. Updated JWT validation.",
        ),
        Event(
            user_id=user_id, source="github",
            timestamp=now - timedelta(days=3),
            event_type="commit", title="Add perception tools",
            content="Added MCP tools for agentic perception. Wraps SykeDB queries.",
        ),
        Event(
            user_id=user_id, source="claude-code",
            timestamp=now - timedelta(days=1),
            event_type="conversation", title="Debugging session",
            content="Working on perception engine. Discussed architecture for agent loop.",
        ),
        Event(
            user_id=user_id, source="claude-code",
            timestamp=now - timedelta(days=5),
            event_type="conversation", title="Planning Syke hackathon",
            content="Planning the Claude Code Hackathon submission. Syke personal context daemon.",
        ),
        Event(
            user_id=user_id, source="chatgpt",
            timestamp=now - timedelta(weeks=3),
            event_type="conversation", title="Philosophy of consciousness",
            content="Discussion about consciousness and perception. Exploring qualia.",
        ),
        Event(
            user_id=user_id, source="chatgpt",
            timestamp=now - timedelta(days=2),
            event_type="conversation", title="Agent architecture",
            content="Exploring agent architecture patterns. ALMA paper on meta-learning memory.",
        ),
    ]
    db.insert_events(events)


def _run_async(coro):
    """Helper to run async tool functions in tests."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Layer 1: Tool unit tests (no LLM, no Agent SDK)
# ---------------------------------------------------------------------------


class TestGetSourceOverview:
    def test_returns_correct_counts(self, db, user_id):
        _populate_multi_platform(db, user_id)
        tools = create_perception_tools(db, user_id)
        overview_tool = tools[0]  # get_source_overview

        result = _run_async(overview_tool.handler({}))
        data = json.loads(result["content"][0]["text"])

        assert data["total_events"] == 6
        assert "github" in data["sources"]
        assert "claude-code" in data["sources"]
        assert "chatgpt" in data["sources"]
        assert data["sources"]["github"]["count"] == 2
        assert data["sources"]["claude-code"]["count"] == 2
        assert data["sources"]["chatgpt"]["count"] == 2

    def test_empty_db(self, db, user_id):
        tools = create_perception_tools(db, user_id)
        overview_tool = tools[0]
        result = _run_async(overview_tool.handler({}))
        data = json.loads(result["content"][0]["text"])
        assert data["total_events"] == 0
        assert data["sources"] == {}


class TestBrowseTimeline:
    def test_returns_events_with_previews(self, db, user_id):
        _populate_multi_platform(db, user_id)
        tools = create_perception_tools(db, user_id)
        browse_tool = tools[1]  # browse_timeline

        result = _run_async(browse_tool.handler({"limit": 10}))
        data = json.loads(result["content"][0]["text"])

        assert data["count"] > 0
        assert data["count"] <= 10
        for ev in data["events"]:
            assert "timestamp" in ev
            assert "source" in ev
            assert "content_preview" in ev
            assert len(ev["content_preview"]) <= 800

    def test_source_filter(self, db, user_id):
        _populate_multi_platform(db, user_id)
        tools = create_perception_tools(db, user_id)
        browse_tool = tools[1]

        result = _run_async(browse_tool.handler({"source": "github"}))
        data = json.loads(result["content"][0]["text"])

        assert data["count"] == 2
        for ev in data["events"]:
            assert ev["source"] == "github"

    def test_date_filter(self, db, user_id):
        _populate_multi_platform(db, user_id)
        tools = create_perception_tools(db, user_id)
        browse_tool = tools[1]

        since = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        result = _run_async(browse_tool.handler({"since": since}))
        data = json.loads(result["content"][0]["text"])

        # Should include only events from last 2 days
        assert data["count"] >= 1
        assert data["count"] <= 6  # Loose bound


class TestSearchFootprint:
    def test_finds_matching_events(self, db, user_id):
        _populate_multi_platform(db, user_id)
        tools = create_perception_tools(db, user_id)
        search_tool = tools[2]  # search_footprint

        result = _run_async(search_tool.handler({"query": "perception"}))
        data = json.loads(result["content"][0]["text"])

        assert data["query"] == "perception"
        assert data["count"] >= 1
        # Check that matching events actually contain the term
        for ev in data["events"]:
            assert (
                "perception" in ev["title"].lower()
                or "perception" in ev["content_preview"].lower()
            )

    def test_no_results(self, db, user_id):
        _populate_multi_platform(db, user_id)
        tools = create_perception_tools(db, user_id)
        search_tool = tools[2]

        result = _run_async(search_tool.handler({"query": "nonexistenttermxyz"}))
        data = json.loads(result["content"][0]["text"])
        assert data["count"] == 0


class TestCrossReference:
    def test_groups_by_source(self, db, user_id):
        _populate_multi_platform(db, user_id)
        tools = create_perception_tools(db, user_id)
        xref_tool = tools[3]  # cross_reference

        result = _run_async(xref_tool.handler({"topic": "perception"}))
        data = json.loads(result["content"][0]["text"])

        assert data["topic"] == "perception"
        assert data["total_matches"] >= 1
        # Results should be grouped by source
        assert isinstance(data["by_source"], dict)
        # Each event in a source group should have standard format fields
        for source, events in data["by_source"].items():
            for ev in events:
                assert "timestamp" in ev
                assert "source" in ev
                assert "content_preview" in ev
                assert ev["source"] == source

    def test_no_matches(self, db, user_id):
        _populate_multi_platform(db, user_id)
        tools = create_perception_tools(db, user_id)
        xref_tool = tools[3]

        result = _run_async(xref_tool.handler({"topic": "nonexistenttermxyz"}))
        data = json.loads(result["content"][0]["text"])
        assert data["total_matches"] == 0
        assert data["by_source"] == {}


class TestReadPreviousProfile:
    def test_no_profile(self, db, user_id):
        tools = create_perception_tools(db, user_id)
        profile_tool = tools[4]  # read_previous_profile

        result = _run_async(profile_tool.handler({}))
        data = json.loads(result["content"][0]["text"])
        assert data["exists"] is False

    def test_with_existing_profile(self, db, user_id):
        # Save a profile first
        profile = UserProfile(
            user_id=user_id,
            identity_anchor="Test user who codes a lot",
            active_threads=[],
            recent_detail="Working on hackathon",
            background_context="Long history of coding",
            sources=["github"],
            events_count=10,
        )
        db.save_profile(profile)

        tools = create_perception_tools(db, user_id)
        profile_tool = tools[4]

        result = _run_async(profile_tool.handler({}))
        data = json.loads(result["content"][0]["text"])
        assert data["exists"] is True
        assert data["profile"]["identity_anchor"] == "Test user who codes a lot"


class TestSubmitProfile:
    def test_valid_submission(self, db, user_id):
        tools = create_perception_tools(db, user_id)
        submit_tool = tools[5]  # submit_profile

        profile_data = {
            "identity_anchor": "A builder at heart",
            "active_threads": [
                {"name": "Syke", "description": "Building personal context daemon"}
            ],
            "recent_detail": "Hackathon mode",
            "background_context": "Years of building tools",
        }
        result = _run_async(submit_tool.handler(profile_data))
        data = json.loads(result["content"][0]["text"])

        assert data["status"] == "submitted"
        assert data["profile"]["identity_anchor"] == "A builder at heart"

    def test_missing_required_fields(self, db, user_id):
        tools = create_perception_tools(db, user_id)
        submit_tool = tools[5]

        # Missing identity_anchor
        profile_data = {
            "active_threads": [],
            "recent_detail": "test",
            "background_context": "test",
        }
        result = _run_async(submit_tool.handler(profile_data))
        data = json.loads(result["content"][0]["text"])

        assert data["status"] == "error"
        assert "identity_anchor" in data["message"]

    def test_all_fields_missing(self, db, user_id):
        tools = create_perception_tools(db, user_id)
        submit_tool = tools[5]

        result = _run_async(submit_tool.handler({}))
        data = json.loads(result["content"][0]["text"])

        assert data["status"] == "error"
        assert "identity_anchor" in data["message"]
        assert "active_threads" in data["message"]


# ---------------------------------------------------------------------------
# Layer 2: AgenticPerceiver tests (mocked Agent SDK)
# ---------------------------------------------------------------------------


class TestAgenticPerceiver:
    """Test the AgenticPerceiver orchestration with mocked Agent SDK."""

    def _make_submit_result_block(self, profile_data: dict):
        """Create a mock ToolResultBlock with submitted profile."""
        from claude_agent_sdk import ToolResultBlock
        result_json = json.dumps({"status": "submitted", "profile": profile_data})
        return ToolResultBlock(
            tool_use_id="test-id",
            content=[{"type": "text", "text": result_json}],
        )

    def _make_tool_use_block(self, name: str, input_data: dict):
        """Create a mock ToolUseBlock."""
        from claude_agent_sdk import ToolUseBlock
        return ToolUseBlock(id="test-id", name=name, input=input_data)

    def _make_text_block(self, text: str):
        """Create a mock TextBlock."""
        from claude_agent_sdk import TextBlock
        return TextBlock(text=text)

    def test_perceive_extracts_submitted_profile(self, db, user_id):
        """Agent submits a profile via submit_profile tool -> returns UserProfile."""
        _populate_multi_platform(db, user_id)

        profile_data = {
            "identity_anchor": "Builder exploring consciousness",
            "active_threads": [
                {"name": "Syke", "description": "Personal context daemon",
                 "intensity": "high", "platforms": ["github"], "recent_signals": []}
            ],
            "recent_detail": "Hackathon prep",
            "background_context": "Long history of personalization",
            "voice_patterns": {
                "tone": "direct",
                "vocabulary_notes": [],
                "communication_style": "technical",
                "examples": [],
            },
        }

        from claude_agent_sdk import AssistantMessage, ResultMessage

        # Build mock message sequence
        messages = [
            AssistantMessage(
                content=[
                    self._make_text_block("Let me explore the footprint..."),
                    self._make_tool_use_block("mcp__perception__get_source_overview", {}),
                ],
                model="claude-opus-4-6",
            ),
            AssistantMessage(
                content=[
                    self._make_tool_use_block("mcp__perception__submit_profile", profile_data),
                    self._make_submit_result_block(profile_data),
                ],
                model="claude-opus-4-6",
            ),
            ResultMessage(
                subtype="success",
                duration_ms=5000,
                duration_api_ms=4000,
                is_error=False,
                num_turns=3,
                session_id="test-session",
                total_cost_usd=0.50,
            ),
        ]

        async def mock_receive_messages():
            for msg in messages:
                yield msg

        with patch("syke.perception.agentic_perceiver.ClaudeSDKClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.query = AsyncMock()
            mock_instance.receive_response = mock_receive_messages
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            from syke.perception.agentic_perceiver import AgenticPerceiver

            perceiver = AgenticPerceiver(db, user_id)
            profile = perceiver.perceive(full=True)

            assert isinstance(profile, UserProfile)
            assert profile.identity_anchor == "Builder exploring consciousness"
            assert len(profile.active_threads) == 1
            assert profile.active_threads[0].name == "Syke"
            assert profile.user_id == user_id

            # Verify metrics are captured from ResultMessage
            assert perceiver.metrics.cost_usd == 0.50
            assert perceiver.metrics.num_turns == 3
            assert perceiver.metrics.duration_ms == 5000
            assert perceiver.metrics.duration_api_ms == 4000
            # cost_usd should flow through to the profile
            assert profile.cost_usd == 0.50

    def test_perceive_raises_without_submission(self, db, user_id):
        """Agent finishes without calling submit_profile -> RuntimeError."""
        _populate_multi_platform(db, user_id)

        from claude_agent_sdk import AssistantMessage, ResultMessage

        # No submit_profile call in messages
        messages = [
            AssistantMessage(
                content=[self._make_text_block("I explored but forgot to submit.")],
                model="claude-opus-4-6",
            ),
            ResultMessage(
                subtype="success",
                duration_ms=3000,
                duration_api_ms=2000,
                is_error=False,
                num_turns=2,
                session_id="test-session",
            ),
        ]

        async def mock_receive_messages():
            for msg in messages:
                yield msg

        with patch("syke.perception.agentic_perceiver.ClaudeSDKClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.query = AsyncMock()
            mock_instance.receive_response = mock_receive_messages
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            from syke.perception.agentic_perceiver import AgenticPerceiver

            perceiver = AgenticPerceiver(db, user_id)
            with pytest.raises(RuntimeError, match="submit_profile"):
                perceiver.perceive(full=True)

    def test_discovery_callback_called(self, db, user_id):
        """on_discovery callback receives intermediate messages."""
        _populate_multi_platform(db, user_id)

        profile_data = {
            "identity_anchor": "Test user",
            "active_threads": [],
            "recent_detail": "Testing",
            "background_context": "Testing",
        }

        from claude_agent_sdk import AssistantMessage, ResultMessage

        messages = [
            AssistantMessage(
                content=[
                    self._make_text_block("Exploring the data..."),
                    self._make_tool_use_block("mcp__perception__browse_timeline", {"limit": 10}),
                ],
                model="claude-opus-4-6",
            ),
            AssistantMessage(
                content=[
                    self._make_tool_use_block("mcp__perception__submit_profile", profile_data),
                    self._make_submit_result_block(profile_data),
                ],
                model="claude-opus-4-6",
            ),
            ResultMessage(
                subtype="success",
                duration_ms=3000,
                duration_api_ms=2000,
                is_error=False,
                num_turns=2,
                session_id="test-session",
                total_cost_usd=0.25,
            ),
        ]

        async def mock_receive_messages():
            for msg in messages:
                yield msg

        discoveries = []

        def capture_discovery(event_type: str, detail: str):
            discoveries.append((event_type, detail))

        with patch("syke.perception.agentic_perceiver.ClaudeSDKClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.query = AsyncMock()
            mock_instance.receive_response = mock_receive_messages
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            from syke.perception.agentic_perceiver import AgenticPerceiver

            perceiver = AgenticPerceiver(db, user_id)
            perceiver.perceive(full=True, on_discovery=capture_discovery)

        # Should have received at least: reasoning, tool_call(browse_timeline), tool_call(submit_profile), result
        event_types = [d[0] for d in discoveries]
        assert "reasoning" in event_types
        assert "tool_call" in event_types
        assert "result" in event_types

    def test_none_callback_does_not_crash(self, db, user_id):
        """on_discovery=None should work without error."""
        _populate_multi_platform(db, user_id)

        profile_data = {
            "identity_anchor": "Test user",
            "active_threads": [],
            "recent_detail": "Testing",
            "background_context": "Testing",
        }

        from claude_agent_sdk import AssistantMessage, ResultMessage

        messages = [
            AssistantMessage(
                content=[
                    self._make_text_block("Exploring..."),
                    self._make_tool_use_block("mcp__perception__submit_profile", profile_data),
                    self._make_submit_result_block(profile_data),
                ],
                model="claude-opus-4-6",
            ),
            ResultMessage(
                subtype="success",
                duration_ms=2000,
                duration_api_ms=1000,
                is_error=False,
                num_turns=1,
                session_id="test-session",
                total_cost_usd=0.10,
            ),
        ]

        async def mock_receive_messages():
            for msg in messages:
                yield msg

        with patch("syke.perception.agentic_perceiver.ClaudeSDKClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.query = AsyncMock()
            mock_instance.receive_response = mock_receive_messages
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            from syke.perception.agentic_perceiver import AgenticPerceiver

            perceiver = AgenticPerceiver(db, user_id)
            profile = perceiver.perceive(full=True, on_discovery=None)

            assert isinstance(profile, UserProfile)
            assert profile.identity_anchor == "Test user"


# ---------------------------------------------------------------------------
# Layer 3: Delta merge tests
# ---------------------------------------------------------------------------


class TestMergeDeltaIntoProfile:
    """Test the merge_delta_into_profile function."""

    def _make_existing_profile(self, user_id: str) -> UserProfile:
        return UserProfile(
            user_id=user_id,
            identity_anchor="A builder exploring consciousness and personalization.",
            active_threads=[
                {"name": "Syke", "description": "Personal context daemon", "intensity": "high",
                 "platforms": ["github", "claude-code"], "recent_signals": ["Feb 10: hackathon prep"]},
                {"name": "Stale project", "description": "Old thing", "intensity": "low",
                 "platforms": ["github"], "recent_signals": []},
            ],
            recent_detail="Working on Syke v0.2 for hackathon submission.",
            background_context="Long history of building personalization tools.",
            world_state="Syke v0.2 shipped. Working on v0.3 delta perception.",
            voice_patterns={
                "tone": "Direct and intense",
                "vocabulary_notes": ["psychonaut", "perception"],
                "communication_style": "Technical, fast-paced",
                "examples": ["Ship it."],
            },
            sources=["github", "claude-code", "chatgpt"],
            events_count=100,
            cost_usd=0.78,
        )

    def test_delta_preserves_unchanged_fields(self, user_id):
        """Fields omitted from delta are preserved from existing profile."""
        existing = self._make_existing_profile(user_id)
        delta = {
            "recent_detail": "Now working on v0.3 with delta perception.",
            "active_threads": [
                {"name": "Syke v0.3", "description": "Delta perception implementation",
                 "intensity": "high", "platforms": ["github"], "recent_signals": ["Feb 15: implementing delta merge"]},
            ],
        }

        result = merge_delta_into_profile(
            existing, delta, user_id, events_count=120, sources=["github", "claude-code", "chatgpt"], cost_usd=0.08,
        )

        # Changed fields should be updated
        assert result.recent_detail == "Now working on v0.3 with delta perception."
        assert len(result.active_threads) == 1
        assert result.active_threads[0].name == "Syke v0.3"

        # Unchanged fields should be preserved
        assert result.identity_anchor == existing.identity_anchor
        assert result.background_context == existing.background_context
        assert result.world_state == existing.world_state
        assert result.voice_patterns is not None
        assert result.voice_patterns.tone == "Direct and intense"

        # Metadata should be fresh
        assert result.events_count == 120
        assert result.cost_usd == 0.08

    def test_delta_updates_all_fields(self, user_id):
        """When all fields are provided in delta, all are updated."""
        existing = self._make_existing_profile(user_id)
        delta = {
            "identity_anchor": "New identity understanding.",
            "active_threads": [{"name": "New", "description": "New thread"}],
            "recent_detail": "New detail.",
            "background_context": "New background.",
            "world_state": "New world state.",
            "voice_patterns": {"tone": "Relaxed", "vocabulary_notes": [], "communication_style": "Casual", "examples": []},
        }

        result = merge_delta_into_profile(
            existing, delta, user_id, events_count=150, sources=["github"], cost_usd=0.50,
        )

        assert result.identity_anchor == "New identity understanding."
        assert result.recent_detail == "New detail."
        assert result.background_context == "New background."
        assert result.world_state == "New world state."
        assert result.voice_patterns.tone == "Relaxed"
        assert len(result.active_threads) == 1

    def test_delta_empty_preserves_everything(self, user_id):
        """An empty delta preserves the entire existing profile."""
        existing = self._make_existing_profile(user_id)
        delta = {}

        result = merge_delta_into_profile(
            existing, delta, user_id, events_count=100, sources=["github"], cost_usd=0.02,
        )

        assert result.identity_anchor == existing.identity_anchor
        assert result.recent_detail == existing.recent_detail
        assert result.background_context == existing.background_context
        assert result.world_state == existing.world_state
        assert len(result.active_threads) == len(existing.active_threads)

    def test_delta_ignores_falsy_values(self, user_id):
        """Empty strings and empty lists in delta don't overwrite existing data."""
        existing = self._make_existing_profile(user_id)
        delta = {
            "identity_anchor": "",  # Falsy — should be ignored
            "active_threads": [],  # Falsy — should be ignored
            "recent_detail": "Updated detail.",
        }

        result = merge_delta_into_profile(
            existing, delta, user_id, events_count=100, sources=["github"], cost_usd=0.05,
        )

        # Empty string should NOT overwrite existing
        assert result.identity_anchor == existing.identity_anchor
        # Empty list should NOT overwrite existing
        assert len(result.active_threads) == len(existing.active_threads)
        # Non-empty value should overwrite
        assert result.recent_detail == "Updated detail."

    def test_delta_returns_valid_user_profile(self, user_id):
        """Merged result is a valid UserProfile."""
        existing = self._make_existing_profile(user_id)
        delta = {"recent_detail": "New detail."}

        result = merge_delta_into_profile(
            existing, delta, user_id, events_count=100, sources=["github"], cost_usd=0.05,
        )

        assert isinstance(result, UserProfile)
        assert result.user_id == user_id


