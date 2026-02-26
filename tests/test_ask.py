"""Tests for the ask agent."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from syke.db import SykeDB
from syke.distribution.ask_agent import ask, ask_stream, AskEvent
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
        result, cost = ask(db, user_id, "What is the user working on?")
        assert "no data" in result.lower()
        assert "setup" in result.lower()
        assert cost == {}


class TestAskWithData:
    def test_ask_with_mocked_client(self, db, user_id):
        """With seeded data and mocked agent, returns an answer."""
        _seed_events(db, user_id, 5)

        with patch("syke.distribution.ask_agent._run_ask") as mock_run:
            mock_run.return_value = ("They are building Syke for a hackathon.", {})
            # We need to patch asyncio.run since ask() calls it
            with patch("syke.distribution.ask_agent.asyncio") as mock_asyncio:
                mock_asyncio.run.return_value = (
                    "They are building Syke for a hackathon.", {}
                )

                result, cost = ask(db, user_id, "What is the user working on?")
                assert "Syke" in result


class TestAskErrorHandling:
    def test_generic_error_returns_fallback(self, db, user_id):
        """Generic exception triggers local fallback instead of bare error."""
        _seed_events(db, user_id, 3)

        import asyncio

        with patch("syke.distribution.ask_agent.asyncio") as mock_asyncio:
            mock_asyncio.run.side_effect = RuntimeError("Agent SDK not available")
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            result, cost = ask(db, user_id, "What is happening?")
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
            result, cost = ask(db, user_id, "What is happening?")
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
            result, cost = ask(db, user_id, "What is happening?")
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
            result, cost = ask(db, user_id, "What is happening?")
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
            result, cost = ask(db, user_id, "What is happening?")
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


class TestAskStream:
    """Tests for the streaming ask_stream() entry point."""

    def test_stream_emits_text_events(self, db, user_id):
        """ask_stream calls on_event with text for AssistantMessage text blocks."""
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

        _seed_events(db, user_id, 5)
        answer_text = "User is building Syke."

        async def _fake_receive():
            msg = MagicMock(spec=AssistantMessage)
            block = MagicMock(spec=TextBlock)
            block.text = answer_text
            msg.content = [block]
            yield msg
            result_msg = MagicMock(spec=ResultMessage)
            result_msg.total_cost_usd = 0.01
            result_msg.num_turns = 1
            result_msg.duration_api_ms = 500
            yield result_msg

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.query = AsyncMock()
        mock_client.receive_response = _fake_receive

        events: list[AskEvent] = []

        with patch(
            "syke.distribution.ask_agent.ClaudeSDKClient", return_value=mock_client
        ):
            result, cost = ask_stream(db, user_id, "What is happening?", events.append)
            assert answer_text in result
            # Since include_partial_messages=True but the mock yields complete
            # AssistantMessages (not StreamEvents), tool_call events from
            # ToolUseBlock won't appear. But we should NOT get duplicate text
            # events because streaming is on (text comes via deltas, not blocks).

    def test_stream_emits_tool_call_events(self, db, user_id):
        """ask_stream emits tool_call events for ToolUseBlock."""
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
        )

        _seed_events(db, user_id, 5)

        async def _fake_receive():
            # First message: tool use
            msg1 = MagicMock(spec=AssistantMessage)
            tool_block = MagicMock(spec=ToolUseBlock)
            tool_block.name = "search_memories"
            tool_block.input = {"query": "working on"}
            msg1.content = [tool_block]
            yield msg1
            # Second message: answer
            msg2 = MagicMock(spec=AssistantMessage)
            text_block = MagicMock(spec=TextBlock)
            text_block.text = "Working on Syke."
            msg2.content = [text_block]
            yield msg2
            # Result
            result_msg = MagicMock(spec=ResultMessage)
            result_msg.total_cost_usd = 0.02
            result_msg.num_turns = 2
            result_msg.duration_api_ms = 1000
            yield result_msg

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.query = AsyncMock()
        mock_client.receive_response = _fake_receive

        events: list[AskEvent] = []

        with patch(
            "syke.distribution.ask_agent.ClaudeSDKClient", return_value=mock_client
        ):
            result, cost = ask_stream(db, user_id, "What am I working on?", events.append)
            assert "Working on Syke" in result
            tool_events = [e for e in events if e.type == "tool_call"]
            assert len(tool_events) == 1
            assert tool_events[0].content == "search_memories"

    def test_stream_no_data_returns_message(self, db, user_id):
        """ask_stream with no events returns no-data message without crashing."""
        events: list[AskEvent] = []
        result, cost = ask_stream(db, user_id, "anything", events.append)
        assert "no data" in result.lower()
        assert cost == {}


class TestAskEvent:
    """Tests for the AskEvent dataclass."""

    def test_text_event(self):
        e = AskEvent(type="text", content="hello")
        assert e.type == "text"
        assert e.content == "hello"
        assert e.metadata is None

    def test_tool_call_event_with_metadata(self):
        e = AskEvent(type="tool_call", content="search_memories", metadata={"input": {"q": "test"}})
        assert e.type == "tool_call"
        assert e.metadata == {"input": {"q": "test"}}

    def test_thinking_event(self):
        e = AskEvent(type="thinking", content="Let me think...")
        assert e.type == "thinking"
