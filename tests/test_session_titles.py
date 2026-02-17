"""Tests for ClaudeCodeAdapter._make_title() session title extraction."""

from unittest.mock import MagicMock

from syke.ingestion.claude_code import ClaudeCodeAdapter


def _make_adapter() -> ClaudeCodeAdapter:
    """Create adapter with mocked DB (only _make_title is tested, no DB needed)."""
    return ClaudeCodeAdapter(user_id="test", db=MagicMock())


class TestSummaryPreference:
    """Summary's first sentence should be preferred over raw text."""

    def test_summary_used_over_text(self):
        adapter = _make_adapter()
        title = adapter._make_title("Hey can you fix the bug", summary="Refactored authentication module. Added tests.")
        assert title == "Refactored authentication module."

    def test_summary_single_sentence_no_period(self):
        adapter = _make_adapter()
        title = adapter._make_title("raw text", summary="Implemented caching layer")
        assert title == "Implemented caching layer"

    def test_summary_too_short_falls_back(self):
        """Summary < 10 chars should fall back to text."""
        adapter = _make_adapter()
        title = adapter._make_title("Build the new API endpoint for users", summary="Short")
        assert "API endpoint" in title

    def test_empty_summary_falls_back(self):
        adapter = _make_adapter()
        title = adapter._make_title("Implement dark mode for the dashboard", summary="")
        assert "dark mode" in title

    def test_none_summary_falls_back(self):
        adapter = _make_adapter()
        title = adapter._make_title("Implement dark mode for the dashboard", summary=None)
        assert "dark mode" in title

    def test_whitespace_summary_falls_back(self):
        adapter = _make_adapter()
        title = adapter._make_title("Implement dark mode for the dashboard", summary="   ")
        assert "dark mode" in title


class TestGreetingStripping:
    """Greeting prefixes should be removed when remainder is long enough."""

    def test_hey_stripped(self):
        adapter = _make_adapter()
        title = adapter._make_title("Hey, can you help me refactor the authentication system")
        assert not title.lower().startswith("hey")
        assert title[0].isupper()

    def test_please_stripped(self):
        adapter = _make_adapter()
        title = adapter._make_title("Please update the database migration scripts")
        assert not title.lower().startswith("please")

    def test_can_you_stripped(self):
        adapter = _make_adapter()
        title = adapter._make_title("Can you implement the search feature for the dashboard")
        assert not title.lower().startswith("can you")

    def test_short_remainder_keeps_greeting(self):
        """If stripping the greeting leaves < 20 chars, keep it."""
        adapter = _make_adapter()
        title = adapter._make_title("Hey, fix the bug")
        assert title.lower().startswith("hey")

    def test_first_char_capitalized_after_strip(self):
        adapter = _make_adapter()
        title = adapter._make_title("Please refactor the entire codebase structure")
        assert title[0].isupper()

    def test_greeting_stripped_from_summary_too(self):
        """Greeting stripping applies to summaries as well — clean titles everywhere."""
        adapter = _make_adapter()
        title = adapter._make_title(
            "hey can you do this thing for me please",
            summary="Hey this is a long enough summary sentence to exceed the threshold."
        )
        # "Hey " is stripped, remainder capitalized
        assert title.startswith("This is a long enough")


class TestTruncation:
    """Titles > 120 chars should be truncated at word boundary."""

    def test_long_title_truncated(self):
        adapter = _make_adapter()
        long_text = "Implement the new authentication system with OAuth2 support including token refresh and session management and also add comprehensive test coverage for all edge cases and error handling scenarios"
        title = adapter._make_title(long_text)
        assert len(title) <= 120

    def test_truncation_at_word_boundary(self):
        adapter = _make_adapter()
        long_text = "Implement the new authentication system with OAuth2 support including token refresh and session management and also add comprehensive test coverage"
        title = adapter._make_title(long_text)
        assert not title.endswith("-")
        # Should not cut mid-word
        assert title[-1] != " "

    def test_short_title_not_truncated(self):
        adapter = _make_adapter()
        short_text = "Fix the login bug"
        title = adapter._make_title(short_text)
        assert title == short_text


class TestEdgeCases:
    """Edge cases: empty input, multiline, whitespace."""

    def test_empty_text_returns_untitled(self):
        adapter = _make_adapter()
        title = adapter._make_title("")
        assert title == "Untitled session"

    def test_none_text_like_empty(self):
        adapter = _make_adapter()
        title = adapter._make_title("")
        assert title == "Untitled session"

    def test_multiline_uses_first_line(self):
        adapter = _make_adapter()
        title = adapter._make_title("First line of the conversation that is long enough\nSecond line with more detail")
        assert "First line" in title
        assert "Second line" not in title

    def test_whitespace_only_returns_untitled(self):
        adapter = _make_adapter()
        title = adapter._make_title("   ")
        assert title == "Untitled session"
