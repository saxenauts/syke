"""Tests for MCP server and ask() agent."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_ask_without_api_key_returns_clear_message(monkeypatch):
    """ask() returns clear message when ANTHROPIC_API_KEY is not set."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("syke.config.load_api_key", lambda: "")

    from syke.distribution.ask_agent import ask

    db = MagicMock()
    db.count_events.return_value = 100
    db.get_latest_profile.return_value = MagicMock()

    result = ask(db, "testuser", "What am I working on?")
    assert "ANTHROPIC_API_KEY" in result
    assert "requires" in result.lower() or "need" in result.lower()
