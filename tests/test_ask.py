"""Tests for the ask agent."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from syke.db import SykeDB
from syke.distribution.ask_agent import ask
from syke.models import Event


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

        with patch("syke.distribution.ask_agent._run_ask") as mock_run:
            mock_run.return_value = "They are building Syke for a hackathon."
            # We need to patch asyncio.run since ask() calls it
            with patch("syke.distribution.ask_agent.asyncio") as mock_asyncio:
                mock_asyncio.run.return_value = (
                    "They are building Syke for a hackathon."
                )

                result = ask(db, user_id, "What is the user working on?")
                assert "Syke" in result


class TestAskErrorHandling:
    def test_generic_error_returns_fallback(self, db, user_id):
        """Generic exception triggers local fallback instead of bare error."""
        _seed_events(db, user_id, 3)

        import asyncio

        with patch("syke.distribution.ask_agent.asyncio") as mock_asyncio:
            mock_asyncio.run.side_effect = RuntimeError("Agent SDK not available")
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            result = ask(db, user_id, "What is happening?")
            # Should return useful content, not a bare error
            assert result.strip() != ""
            assert "fallback" in result.lower()

    def test_rate_limit_event_returns_partial_answer(self, db, user_id):
        """Unknown stream events (e.g. rate_limit_event) return partial answer instead of crashing."""
        from claude_agent_sdk import ClaudeSDKError, AssistantMessage, TextBlock

        _seed_events(db, user_id, 3)

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

        with patch(
            "syke.distribution.ask_agent.ClaudeSDKClient", return_value=mock_client
        ):
            result = ask(db, user_id, "What is happening?")
            assert partial_text in result

    def test_rate_limit_event_before_response_continues_stream(self, db, user_id):
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            TextBlock,
        )

        _seed_events(db, user_id, 3)

        answer_text = "Working on Syke for the hackathon."

        async def _fake_receive():
            # rate_limit_event advisory arrives first — as a SystemMessage (post-patch)
            yield SystemMessage(
                subtype="rate_limit_event", data={"type": "rate_limit_event"}
            )
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

        with patch(
            "syke.distribution.ask_agent.ClaudeSDKClient", return_value=mock_client
        ):
            result = ask(db, user_id, "What is happening?")
            assert answer_text in result

    def test_claudecode_env_cleared_before_subprocess(self, db, user_id, monkeypatch):
        _seed_events(db, user_id, 3)

        monkeypatch.setenv("CLAUDECODE", "1")

        captured_env: dict[str, str] = {}

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

        with patch(
            "syke.distribution.ask_agent.ClaudeSDKClient", side_effect=_capturing_client
        ):
            ask(db, user_id, "What is happening?")

        assert "CLAUDECODE" not in captured_env



class TestAskFallback:
    """Tests for local fallback when Agent SDK fails."""

    def test_empty_response_triggers_fallback(self, db, user_id):
        """When agent returns no text, fallback returns memex or error message."""
        _seed_events(db, user_id, 3)

        async def _fake_receive():
            return
            yield  # async generator that yields nothing

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.query = AsyncMock()
        mock_client.receive_response = _fake_receive

        with patch(
            "syke.distribution.ask_agent.ClaudeSDKClient", return_value=mock_client
        ):
            result = ask(db, user_id, "What is happening?")
            # Should NOT be empty
            assert result.strip() != ""
            # Should indicate fallback or show useful content
            assert ("fallback" in result.lower() or
                    "no answer" in result.lower() or
                    len(result) > 10)

    def test_sdk_exception_triggers_fallback(self, db, user_id):
        """ClaudeSDKError triggers local fallback instead of generic error."""
        from claude_agent_sdk import ClaudeSDKError

        _seed_events(db, user_id, 3)

        async def _fake_receive():
            raise ClaudeSDKError("Connection failed")
            yield  # type: ignore

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.query = AsyncMock()
        mock_client.receive_response = _fake_receive

        with patch(
            "syke.distribution.ask_agent.ClaudeSDKClient", return_value=mock_client
        ):
            result = ask(db, user_id, "What is happening?")
            assert result.strip() != ""
            # Should NOT contain the old "ask() failed" error format
            assert "ask() failed" not in result

    def test_fallback_includes_memex_when_available(self, db, user_id):
        """Fallback includes memex content when DB has it."""
        from syke.distribution.ask_agent import _local_fallback
        from syke.memory.memex import update_memex

        _seed_events(db, user_id, 3)
        update_memex(db, user_id, "# Memex\nUser is a Python developer.")

        result = _local_fallback(db, user_id, "what does the user do?")
        assert "Python developer" in result
        assert "fallback" in result.lower()

    def test_fallback_with_no_data_returns_message(self, db, user_id):
        """Fallback with empty DB returns helpful message."""
        from syke.distribution.ask_agent import _local_fallback

        result = _local_fallback(db, user_id, "anything")
        assert "no answer" in result.lower() or "fallback" in result.lower()
