from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from syke.ingestion.claude_code import ClaudeCodeAdapter


@pytest.fixture
def adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter(user_id="test", db=MagicMock())


@pytest.mark.parametrize(
    "text,summary,expected",
    [
        (
            "Hey can you fix the bug",
            "Refactored authentication module. Added tests.",
            "Refactored authentication module.",
        ),
        (
            "hey can you do this thing",
            "Hey this is a long enough summary sentence to exceed the threshold.",
            "This is a long enough summary sentence to exceed the threshold.",
        ),
    ],
)
def test_summary_preferred_when_valid(
    adapter: ClaudeCodeAdapter, text: str, summary: str, expected: str
) -> None:
    assert adapter._make_title(text, summary=summary) == expected


@pytest.mark.parametrize("summary", ["Short", "", None])
def test_summary_falls_back_to_text_when_invalid(
    adapter: ClaudeCodeAdapter, summary: str | None
) -> None:
    out = adapter._make_title("Implement dark mode for the dashboard", summary=summary)
    assert "dark mode" in out


@pytest.mark.parametrize(
    "raw,prefix",
    [
        ("Hey, can you help me refactor the authentication system", "hey"),
        ("Can you implement the search feature for the dashboard", "can you"),
    ],
)
def test_greeting_prefixes_are_stripped_for_long_remainder(
    adapter: ClaudeCodeAdapter, raw: str, prefix: str
) -> None:
    title = adapter._make_title(raw)
    assert not title.lower().startswith(prefix)
    assert title[0].isupper()


def test_short_remainder_keeps_greeting(adapter: ClaudeCodeAdapter) -> None:
    title = adapter._make_title("Hey, fix the bug")
    assert title.lower().startswith("hey")


def test_truncates_at_word_boundary(adapter: ClaudeCodeAdapter) -> None:
    long_text = (
        "Implement the new authentication system with OAuth2 support including token refresh "
        "and session management and also add comprehensive test coverage"
    )
    title = adapter._make_title(long_text)
    assert len(title) <= 120
    assert not title.endswith(" ")
    assert not title.endswith("-")


def test_short_text_not_truncated(adapter: ClaudeCodeAdapter) -> None:
    assert adapter._make_title("Fix the login bug") == "Fix the login bug"


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_or_whitespace_returns_untitled(
    adapter: ClaudeCodeAdapter, raw: str
) -> None:
    assert adapter._make_title(raw) == "Untitled session"


def test_multiline_uses_first_line(adapter: ClaudeCodeAdapter) -> None:
    title = adapter._make_title(
        "First line of the conversation that is long enough\nSecond line with more detail"
    )
    assert "First line" in title
    assert "Second line" not in title
