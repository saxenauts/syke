"""Tests for the ask agent."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from syke.db import SykeDB
from syke.distribution.ask_agent import ask
from syke.models import Event, UserProfile


def _seed_profile(db: SykeDB, user_id: str) -> UserProfile:
    profile = UserProfile(
        user_id=user_id,
        identity_anchor="A builder of personal context systems.",
        active_threads=[],
        recent_detail="Working on Syke.",
        background_context="Years of AI work.",
        world_state="Building Syke for hackathon.",
        sources=["claude-code"],
        events_count=10,
    )
    db.save_profile(profile)
    return profile


def _seed_events(db: SykeDB, user_id: str, count: int = 3):
    for i in range(count):
        event = Event(
            user_id=user_id,
            source="test-source",
            event_type="conversation",
            title=f"Event {i}",
            content=f"Content {i}",
            timestamp=datetime(2026, 2, 10 + i, 12, 0, 0),
        )
        db.insert_event(event)


class TestAskNoData:
    def test_returns_no_data_message(self, db, user_id):
        """With zero events and no profile, returns no-data message without API call."""
        result = ask(db, user_id, "What is the user working on?")
        assert "no data" in result.lower()
        assert "setup" in result.lower()


class TestAskWithData:
    def test_ask_with_mocked_client(self, db, user_id):
        """With seeded data and mocked agent, returns an answer."""
        _seed_events(db, user_id, 5)
        _seed_profile(db, user_id)

        with patch("syke.distribution.ask_agent._run_ask") as mock_run:
            mock_run.return_value = "They are building Syke for a hackathon."
            # We need to patch asyncio.run since ask() calls it
            with patch("syke.distribution.ask_agent.asyncio") as mock_asyncio:
                mock_asyncio.run.return_value = "They are building Syke for a hackathon."
                mock_asyncio.TimeoutError = TimeoutError
                mock_asyncio.wait_for = AsyncMock(return_value="They are building Syke for a hackathon.")
                result = ask(db, user_id, "What is the user working on?")
                assert "Syke" in result


class TestAskNoApiKey:
    def test_ask_without_api_key_returns_clear_message(self, db, user_id, monkeypatch):
        """ask() returns clear message when ANTHROPIC_API_KEY is not set."""
        _seed_events(db, user_id, 5)
        _seed_profile(db, user_id)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        # Mock load_api_key to prevent fallback to ~/.syke/.env
        with patch("syke.config.load_api_key", return_value=""):
            result = ask(db, user_id, "What am I working on?")
            assert "ANTHROPIC_API_KEY" in result
            assert "requires" in result.lower() or "need" in result.lower()


class TestAskErrorHandling:
    def test_timeout_returns_message(self, db, user_id):
        """Timeout returns a user-friendly message."""
        _seed_events(db, user_id, 3)
        _seed_profile(db, user_id)

        import asyncio
        with patch("syke.distribution.ask_agent.asyncio") as mock_asyncio:
            mock_asyncio.run.side_effect = asyncio.TimeoutError()
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            result = ask(db, user_id, "What is happening?")
            assert "timed out" in result.lower()

    def test_generic_error_returns_message(self, db, user_id):
        """Generic exception returns error message."""
        _seed_events(db, user_id, 3)
        _seed_profile(db, user_id)

        import asyncio
        with patch("syke.distribution.ask_agent.asyncio") as mock_asyncio:
            mock_asyncio.run.side_effect = RuntimeError("Agent SDK not available")
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            result = ask(db, user_id, "What is happening?")
            assert "error" in result.lower()
