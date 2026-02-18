"""Tests for MCP server and ask() agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


def test_ask_without_api_key_returns_auth_guidance(monkeypatch):
    """ask() returns auth guidance when SDK auth fails with no API key or claude login."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("syke.config.load_api_key", lambda: "")

    from syke.distribution.ask_agent import ask

    db = MagicMock()
    db.count_events.return_value = 100
    db.get_latest_profile.return_value = MagicMock()

    with patch("syke.distribution.ask_agent.ClaudeSDKClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("Authentication failed")
        )
        result = ask(db, "testuser", "What am I working on?")
        assert "ask() failed" in result
        assert "ANTHROPIC_API_KEY" in result or "claude login" in result
