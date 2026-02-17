"""Tests for the perception engine (unit tests — no LLM calls)."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from syke.db import SykeDB
from syke.models import Event, UserProfile
from syke.perception.perceiver import Perceiver


def test_build_timeline_text(db, user_id):
    """Timeline text is built with recency weighting."""
    now = datetime.now(UTC)

    # Add events at different ages
    db.insert_event(Event(
        user_id=user_id, source="test",
        timestamp=now - timedelta(days=1),
        event_type="recent", title="Yesterday", content="Very recent event.",
    ))
    db.insert_event(Event(
        user_id=user_id, source="test",
        timestamp=now - timedelta(weeks=3),
        event_type="medium", title="Three weeks ago", content="Medium-term event.",
    ))
    db.insert_event(Event(
        user_id=user_id, source="test",
        timestamp=now - timedelta(weeks=12),
        event_type="old", title="Three months ago", content="Old event.",
    ))

    perceiver = Perceiver(db, user_id)
    text = perceiver._build_timeline_text()

    assert "RECENT" in text
    assert "Yesterday" in text
    assert "Very recent event" in text
    assert "MEDIUM TERM" in text
    assert "Three weeks ago" in text
    assert "BACKGROUND" in text


# --- _extract_json tests ---


class TestExtractJson:
    """Tests for Perceiver._extract_json — JSON extraction from LLM responses."""

    def _perceiver(self, db, user_id):
        return Perceiver(db, user_id)

    def test_pure_json(self, db, user_id):
        p = self._perceiver(db, user_id)
        raw = '{"identity_anchor": "test user", "active_threads": []}'
        result = p._extract_json(raw)
        assert result == {"identity_anchor": "test user", "active_threads": []}

    def test_json_in_markdown_fences(self, db, user_id):
        p = self._perceiver(db, user_id)
        raw = '```json\n{"identity_anchor": "fenced"}\n```'
        result = p._extract_json(raw)
        assert result["identity_anchor"] == "fenced"

    def test_json_with_surrounding_prose(self, db, user_id):
        p = self._perceiver(db, user_id)
        raw = 'Here is the profile:\n{"identity_anchor": "prose"}\nHope this helps!'
        result = p._extract_json(raw)
        assert result["identity_anchor"] == "prose"

    def test_no_json_raises_valueerror(self, db, user_id):
        p = self._perceiver(db, user_id)
        with pytest.raises(ValueError, match="Could not parse profile JSON"):
            p._extract_json("No JSON here at all, just text.")

    def test_invalid_json_raises_valueerror(self, db, user_id):
        p = self._perceiver(db, user_id)
        with pytest.raises(ValueError, match="Could not parse profile JSON"):
            p._extract_json("{broken json: ???}")

    def test_nested_braces(self, db, user_id):
        p = self._perceiver(db, user_id)
        raw = '{"a": {"nested": true}, "b": [1, 2]}'
        result = p._extract_json(raw)
        assert result["a"]["nested"] is True
        assert result["b"] == [1, 2]


# --- _parse_profile tests ---


class TestParseProfile:
    """Tests for Perceiver._parse_profile — LLM output to UserProfile."""

    VALID_RESPONSE = json.dumps({
        "identity_anchor": "Software engineer exploring consciousness and personalization",
        "active_threads": [
            {"name": "Syke Development", "description": "Building a personal context daemon",
             "intensity": "high", "platforms": ["claude-code", "github"]},
        ],
        "recent_detail": "Focused on hackathon prep.",
        "background_context": "Long interest in representing humans with data.",
        "world_state": "Currently building Syke for the Claude Code hackathon. Main focus is fresh onboarding flow.",
        "voice_patterns": {
            "tone": "Direct and technical",
            "vocabulary_notes": ["uses 'vibe' often"],
            "communication_style": "Exploratory, stream-of-consciousness",
        },
    })

    def _perceiver(self, db, user_id):
        return Perceiver(db, user_id)

    def test_valid_full_response(self, db, user_id):
        p = self._perceiver(db, user_id)
        profile = p._parse_profile(self.VALID_RESPONSE, events_count=100, sources=["claude-code", "github"])

        assert isinstance(profile, UserProfile)
        assert profile.user_id == user_id
        assert profile.identity_anchor == "Software engineer exploring consciousness and personalization"
        assert len(profile.active_threads) == 1
        assert profile.active_threads[0].name == "Syke Development"
        assert profile.active_threads[0].intensity == "high"
        assert profile.world_state == "Currently building Syke for the Claude Code hackathon. Main focus is fresh onboarding flow."
        assert profile.events_count == 100
        assert profile.sources == ["claude-code", "github"]

    def test_missing_optional_fields(self, db, user_id):
        """voice_patterns absent should default to None, world_state defaults to empty."""
        p = self._perceiver(db, user_id)
        raw = json.dumps({
            "identity_anchor": "minimal",
            "active_threads": [],
            "recent_detail": "",
            "background_context": "",
        })
        profile = p._parse_profile(raw, events_count=0, sources=[])
        assert profile.voice_patterns is None
        assert profile.world_state == ""

    def test_empty_active_threads(self, db, user_id):
        p = self._perceiver(db, user_id)
        raw = json.dumps({
            "identity_anchor": "nobody",
            "active_threads": [],
            "recent_detail": "",
            "background_context": "",
        })
        profile = p._parse_profile(raw, events_count=0, sources=[])
        assert profile.active_threads == []

    def test_extra_fields_ignored(self, db, user_id):
        """LLM sometimes returns extra keys — they should not crash parsing."""
        p = self._perceiver(db, user_id)
        raw = json.dumps({
            "identity_anchor": "extra",
            "active_threads": [],
            "recent_detail": "",
            "background_context": "",
            "unexpected_field": "should be ignored",
            "another_extra": 42,
        })
        profile = p._parse_profile(raw, events_count=5, sources=["test"])
        assert profile.identity_anchor == "extra"
        assert profile.events_count == 5

    def test_markdown_fenced_response(self, db, user_id):
        """LLM wraps response in ```json fences."""
        p = self._perceiver(db, user_id)
        raw = "```json\n" + self.VALID_RESPONSE + "\n```"
        profile = p._parse_profile(raw, events_count=50, sources=["chatgpt"])
        assert profile.identity_anchor == "Software engineer exploring consciousness and personalization"

    def test_invalid_json_raises(self, db, user_id):
        p = self._perceiver(db, user_id)
        with pytest.raises(ValueError, match="Could not parse profile JSON"):
            p._parse_profile("This is not JSON at all.", events_count=0, sources=[])

    def test_model_field_set_from_client(self, db, user_id):
        """Profile.model should reflect the LLM client's model name."""
        p = self._perceiver(db, user_id)
        raw = json.dumps({
            "identity_anchor": "model check",
            "active_threads": [],
            "recent_detail": "",
            "background_context": "",
        })
        profile = p._parse_profile(raw, events_count=0, sources=[])
        assert profile.model == p.client.model
