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
    def test_ask_without_api_key_returns_auth_guidance(self, db, user_id, monkeypatch):
        """ask() returns auth guidance when SDK auth fails (no API key or claude login)."""
        _seed_events(db, user_id, 5)
        _seed_profile(db, user_id)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        # Mock load_api_key to prevent fallback to ~/.syke/.env, then simulate auth failure
        with patch("syke.config.load_api_key", return_value=""):
            with patch("syke.distribution.ask_agent.ClaudeSDKClient") as mock_client_cls:
                mock_client_cls.return_value.__aenter__ = AsyncMock(
                    side_effect=Exception("Authentication failed: no API key or claude login")
                )
                result = ask(db, user_id, "What am I working on?")
                assert "ask() failed" in result
                assert "ANTHROPIC_API_KEY" in result or "claude login" in result


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

    def test_rate_limit_event_returns_partial_answer(self, db, user_id):
        """Unknown stream events (e.g. rate_limit_event) return partial answer instead of crashing."""
        from claude_agent_sdk import ClaudeSDKError, AssistantMessage, TextBlock
        _seed_events(db, user_id, 3)
        _seed_profile(db, user_id)

        partial_text = "They are building Syke."

        async def _fake_receive():
            msg = MagicMock(spec=AssistantMessage)
            block = MagicMock(spec=TextBlock)
            block.text = partial_text
            msg.content = [block]
            yield msg
            raise ClaudeSDKError("Unknown message type: rate_limit_event")

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.query = AsyncMock()
        mock_client.receive_response = _fake_receive

        with patch("syke.distribution.ask_agent.ClaudeSDKClient", return_value=mock_client):
            result = ask(db, user_id, "What is happening?")
            assert partial_text in result

    def test_rate_limit_event_before_response_continues_stream(self, db, user_id):
        """rate_limit_event arriving before the real answer doesn't swallow the answer.

        With the parse_message patch applied, rate_limit_event is returned as a
        SystemMessage (which the loop skips) and the stream continues to deliver
        the actual AssistantMessage.
        """
        from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock
        _seed_events(db, user_id, 3)
        _seed_profile(db, user_id)

        answer_text = "Working on Syke for the hackathon."

        async def _fake_receive():
            # rate_limit_event advisory arrives first — as a SystemMessage (post-patch)
            yield SystemMessage(subtype="rate_limit_event", data={"type": "rate_limit_event"})
            # then the actual answer
            msg = MagicMock(spec=AssistantMessage)
            block = MagicMock(spec=TextBlock)
            block.text = answer_text
            msg.content = [block]
            yield msg
            # then the result
            result_msg = MagicMock(spec=ResultMessage)
            result_msg.total_cost_usd = 0.0
            result_msg.num_turns = 1
            result_msg.duration_api_ms = 100
            yield result_msg

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.query = AsyncMock()
        mock_client.receive_response = _fake_receive

        with patch("syke.distribution.ask_agent.ClaudeSDKClient", return_value=mock_client):
            result = ask(db, user_id, "What is happening?")
            assert answer_text in result

    def test_claudecode_env_cleared_before_subprocess(self, db, user_id, monkeypatch):
        """CLAUDECODE env var is removed before the SDK subprocess is spawned."""
        _seed_events(db, user_id, 3)
        _seed_profile(db, user_id)

        monkeypatch.setenv("CLAUDECODE", "1")

        captured_env: dict = {}

        async def _fake_receive():
            return
            yield  # make it an async generator

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.query = AsyncMock()
        mock_client.receive_response = _fake_receive

        import os

        def _capturing_client(*args, **kwargs):
            captured_env.update(os.environ)
            return mock_client

        with patch("syke.distribution.ask_agent.ClaudeSDKClient", side_effect=_capturing_client):
            ask(db, user_id, "What is happening?")

        assert "CLAUDECODE" not in captured_env
