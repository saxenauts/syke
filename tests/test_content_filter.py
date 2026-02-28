from __future__ import annotations

import pytest

from syke.ingestion.base import ContentFilter


def _make_whatsapp_line(index: int = 0) -> str:
    return f"[10/6/25, 5:08:3{index % 10} AM] Alice: Hey, are you coming tonight?"


def _make_normal_lines(count: int) -> list[str]:
    return [
        f"Line {i}: Implementing the data pipeline for event ingestion."
        for i in range(count)
    ]


def _anthropic_key() -> str:
    return "sk" + "-" + "ant" + "-api03-" + ("a" * 32)


def _openai_key() -> str:
    return "s" + "k-" + ("a" * 24)


def _github_token() -> str:
    return "gh" + "p_" + ("a" * 36)


def _slack_token() -> str:
    return "x" + "oxb-" + "123456789-abcdefghi"


def _aws_key() -> str:
    return "AK" + "IA" + ("A" * 16)


@pytest.fixture
def cf() -> ContentFilter:
    return ContentFilter()


@pytest.mark.parametrize(
    "content,forbidden_fragment",
    [
        (f"My key is {_anthropic_key()}", _anthropic_key()),
        (f"OPENAI_API_KEY={_openai_key()}", _openai_key()),
        (f"token: {_github_token()}", _github_token()),
        (f"SLACK_TOKEN={_slack_token()}", _slack_token()),
        (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature",
            "eyJhbGciOiJ",
        ),
        (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...",
            "BEGIN RSA PRIVATE KEY",
        ),
        (
            "DATABASE_URL=postgres://admin:s3cretP4ss@db.example.com:5432/mydb",
            "s3cretP4ss",
        ),
    ],
)
def test_sanitize_redacts_credentials(
    cf: ContentFilter, content: str, forbidden_fragment: str
) -> None:
    result = cf.sanitize(content)
    assert "[REDACTED]" in result
    assert forbidden_fragment not in result


def test_sanitize_redacts_multiple_credentials(cf: ContentFilter) -> None:
    content = (
        f"OPENAI_KEY={_openai_key()}\n"
        f"AWS_KEY={_aws_key()}\n"
        "DB=postgres://user:pass123@host/db"
    )
    result = cf.sanitize(content)
    assert result.count("[REDACTED]") >= 3


def test_sanitize_leaves_normal_content_unchanged(cf: ContentFilter) -> None:
    content = "Implementing a SQLite adapter for timeline storage using Python 3.12."
    assert cf.sanitize(content) == content


@pytest.mark.parametrize(
    "normal_count,msg_count,expected_skip,reason_part",
    [
        (6, 4, True, "private messaging"),
        (40, 1, False, ""),
        (200, 11, True, "embedded private messages"),
    ],
)
def test_private_message_thresholds(
    cf: ContentFilter,
    normal_count: int,
    msg_count: int,
    expected_skip: bool,
    reason_part: str,
) -> None:
    content = "\n".join(
        _make_normal_lines(normal_count)
        + [_make_whatsapp_line(i) for i in range(msg_count)]
    )
    skip, reason = cf.should_skip(content)
    assert skip is expected_skip
    if reason_part:
        assert reason_part in reason
    else:
        assert reason == ""


def test_short_content_with_private_messages_not_skipped(cf: ContentFilter) -> None:
    content = "\n".join(_make_whatsapp_line(i) for i in range(5))
    skip, reason = cf.should_skip(content)
    assert skip is False
    assert reason == ""


def test_empty_content_is_skipped(cf: ContentFilter) -> None:
    skip, reason = cf.should_skip("")
    assert skip is True
    assert "empty" in reason


def test_process_pipeline_paths(cf: ContentFilter) -> None:
    result, reason = cf.process("")
    assert result is None
    assert "empty" in reason

    private_content = "\n".join(
        _make_normal_lines(2) + [_make_whatsapp_line(i) for i in range(10)]
    )
    result, reason = cf.process(private_content)
    assert result is None
    assert "private" in reason

    normal = "Working on the ingestion pipeline today."
    result, reason = cf.process(normal, title="Session")
    assert result == normal
    assert reason == "kept"


def test_stats_accounting(cf: ContentFilter) -> None:
    assert cf.stats == {"kept": 0, "skipped": 0, "sanitized": 0}

    _ = cf.sanitize("Normal content")
    _ = cf.sanitize(f"key = {_openai_key()}")
    _ = cf.should_skip(
        "\n".join(_make_normal_lines(2) + [_make_whatsapp_line(i) for i in range(10)])
    )
    _ = cf.should_skip("")

    assert cf.stats["kept"] == 2
    assert cf.stats["sanitized"] == 1
    assert cf.stats["skipped"] == 1
