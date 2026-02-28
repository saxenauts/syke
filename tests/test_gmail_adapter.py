"""Gmail adapter unit tests.

Tests message parsing, body extraction, timestamp handling, query building,
backend selection, dedup, and content filtering — all with mocked Gmail API
responses (no network or credentials needed).
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from syke.db import SykeDB
from syke.ingestion.gmail import (
    GmailAdapter,
    _gog_authenticated,
    _python_oauth_available,
)
from syke.models import IngestionResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    db = SykeDB(tmp_path / "test.db")
    db.initialize()
    yield db
    db.close()


@pytest.fixture
def user_id():
    return "test_user"


@pytest.fixture
def adapter(db, user_id):
    return GmailAdapter(db, user_id)


def _make_message(
    msg_id: str = "abc123",
    subject: str = "Test Subject",
    from_addr: str = "sender@example.com",
    to_addr: str = "me@example.com",
    date_str: str = "Mon, 10 Feb 2026 14:30:00 -0800",
    body_text: str = "Hello, this is the email body.",
    snippet: str = "Hello, this is...",
    labels: list[str] | None = None,
    thread_id: str = "thread_1",
    internal_date: str | None = "1739226600000",  # approx Feb 10 2026
) -> dict[str, object]:
    """Build a realistic Gmail API message dict."""
    body_b64 = base64.urlsafe_b64encode(body_text.encode()).decode()
    msg: dict[str, object] = {
        "id": msg_id,
        "threadId": thread_id,
        "snippet": snippet,
        "labelIds": labels or ["INBOX", "CATEGORY_PERSONAL"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": from_addr},
                {"name": "To", "value": to_addr},
                {"name": "Date", "value": date_str},
            ],
            "body": {"data": body_b64},
        },
    }
    if internal_date:
        msg["internalDate"] = internal_date
    return msg


def _make_multipart_message(
    msg_id: str = "multi123",
    subject: str = "Multipart Email",
    body_text: str = "Plain text part.",
    html_text: str = "<p>HTML part</p>",
) -> dict[str, object]:
    """Build a multipart Gmail message with text/plain and text/html parts."""
    plain_b64 = base64.urlsafe_b64encode(body_text.encode()).decode()
    html_b64 = base64.urlsafe_b64encode(html_text.encode()).decode()
    return {
        "id": msg_id,
        "threadId": "thread_multi",
        "snippet": body_text[:50],
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Date", "value": "Tue, 11 Feb 2026 10:00:00 +0000"},
            ],
            "body": {},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": plain_b64},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": html_b64},
                },
            ],
        },
    }


def _make_nested_multipart_message(msg_id: str = "nested123") -> dict[str, object]:
    """Build a nested multipart message (multipart/mixed > multipart/alternative)."""
    body_b64 = base64.urlsafe_b64encode(b"Nested plain text.").decode()
    return {
        "id": msg_id,
        "threadId": "thread_nested",
        "snippet": "Nested plain text.",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Subject", "value": "Nested Multipart"},
                {"name": "From", "value": "nested@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Date", "value": "Tue, 11 Feb 2026 12:00:00 +0000"},
            ],
            "body": {},
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "body": {},
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": body_b64},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {
                                "data": base64.urlsafe_b64encode(
                                    b"<p>Nested HTML</p>"
                                ).decode()
                            },
                        },
                    ],
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# Message to Event conversion
# ---------------------------------------------------------------------------


class TestMessageToEvent:
    def test_basic_conversion(self, adapter):
        """Simple message produces correct Event fields."""
        msg = _make_message()
        event = adapter._message_to_event(msg)

        assert event is not None
        assert event.source == "gmail"
        assert event.external_id == "abc123"
        assert event.title == "Test Subject"
        assert event.event_type == "email_received"
        assert "From: sender@example.com" in event.content
        assert "Hello, this is the email body." in event.content
        assert event.metadata["from"] == "sender@example.com"
        assert event.metadata["thread_id"] == "thread_1"

    def test_sent_email_detected(self, adapter):
        """Messages with SENT label produce email_sent event type."""
        msg = _make_message(labels=["SENT", "INBOX"])
        event = adapter._message_to_event(msg)

        assert event is not None
        assert event.event_type == "email_sent"
        assert "To: me@example.com" in event.content

    def test_missing_id_returns_none(self, adapter):
        """Message with no id returns None."""
        msg = _make_message()
        msg["id"] = ""
        assert adapter._message_to_event(msg) is None

    def test_dedup_by_external_id(self, adapter, db, user_id):
        """Second call with same message id returns None (dedup)."""
        msg = _make_message(msg_id="dedup_test")
        event1 = adapter._message_to_event(msg)
        assert event1 is not None
        db.insert_events([event1])

        event2 = adapter._message_to_event(msg)
        assert event2 is None

    def test_no_subject_fallback(self, adapter):
        """Missing Subject header falls back to '(no subject)'."""
        msg = _make_message()
        payload = cast(dict[str, object], msg["payload"])
        payload["headers"] = [
            {"name": "From", "value": "sender@example.com"},
            {"name": "To", "value": "me@example.com"},
            {"name": "Date", "value": "Mon, 10 Feb 2026 14:30:00 -0800"},
        ]
        event = adapter._message_to_event(msg)
        assert event is not None
        assert event.title == "(no subject)"

    def test_snippet_fallback_when_no_body(self, adapter):
        """When body is empty, content uses snippet."""
        msg = _make_message(body_text="", snippet="Just a snippet here")
        payload = cast(dict[str, object], msg["payload"])
        payload["body"] = {}
        event = adapter._message_to_event(msg)
        assert event is not None
        assert "Just a snippet here" in event.content


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


class TestBackendSelection:
    @patch("syke.ingestion.gmail._gog_authenticated", return_value=True)
    @patch("syke.ingestion.gmail._fetch_via_gog", return_value=[])
    def test_gog_backend_preferred(self, mock_fetch, mock_auth, adapter):
        """When gog is authenticated, it's used over Python OAuth."""
        result = adapter.ingest(account="test@gmail.com")
        assert result.events_count == 0
        mock_fetch.assert_called_once()

    @patch("syke.ingestion.gmail._gog_authenticated", return_value=False)
    @patch("syke.ingestion.gmail._python_oauth_available", return_value=True)
    @patch("syke.ingestion.gmail._get_python_service")
    @patch("syke.ingestion.gmail._fetch_via_python", return_value=[])
    def test_python_fallback_when_no_gog(
        self, mock_fetch, mock_service, mock_oauth, mock_gog, adapter
    ):
        """Falls back to Python OAuth when gog isn't authenticated."""
        result = adapter.ingest()
        assert result.events_count == 0
        mock_fetch.assert_called_once()

    @patch("syke.ingestion.gmail._gog_authenticated", return_value=False)
    @patch("syke.ingestion.gmail._python_oauth_available", return_value=False)
    def test_no_backend_raises(self, mock_oauth, mock_gog, adapter):
        """Raises RuntimeError when no backend is available."""
        with pytest.raises(RuntimeError, match="No Gmail backend available"):
            adapter.ingest()

    @patch("syke.ingestion.gmail._gog_authenticated", return_value=True)
    @patch("syke.ingestion.gmail._fetch_via_gog")
    def test_gog_ingests_messages(self, mock_fetch, mock_auth, adapter, db, user_id):
        """Gog backend processes messages into events."""
        mock_fetch.return_value = [
            _make_message(msg_id="gog_1", subject="First"),
            _make_message(msg_id="gog_2", subject="Second"),
        ]
        result = adapter.ingest(account="test@gmail.com")
        assert result.events_count == 2
        assert db.count_events(user_id) == 2

    @patch("syke.ingestion.gmail._gog_authenticated", return_value=True)
    @patch("syke.ingestion.gmail._fetch_via_gog")
    def test_gog_ingestion_records_run(
        self, mock_fetch, mock_auth, adapter, db, user_id
    ):
        """Ingestion creates a tracked run in the database."""
        mock_fetch.return_value = [_make_message(msg_id="run_test")]
        result = adapter.ingest(account="test@gmail.com")
        assert result.run_id is not None
        assert result.source == "gmail"


# ---------------------------------------------------------------------------
# Full pipeline with dedup
# ---------------------------------------------------------------------------


class TestFullPipeline:
    @patch("syke.ingestion.gmail._gog_authenticated", return_value=True)
    @patch("syke.ingestion.gmail._fetch_via_gog")
    def test_dedup_across_runs(self, mock_fetch, mock_auth, db, user_id):
        """Second ingestion with same messages inserts 0."""
        messages = [
            _make_message(msg_id="dup_1", subject="First"),
            _make_message(msg_id="dup_2", subject="Second"),
        ]
        mock_fetch.return_value = messages

        adapter = GmailAdapter(db, user_id)
        result1 = adapter.ingest(account="test@gmail.com")
        assert result1.events_count == 2

        result2 = adapter.ingest(account="test@gmail.com")
        assert result2.events_count == 0
        assert db.count_events(user_id) == 2

    @patch("syke.ingestion.gmail._gog_authenticated", return_value=True)
    @patch("syke.ingestion.gmail._fetch_via_gog")
    def test_incremental_adds_new_only(self, mock_fetch, mock_auth, db, user_id):
        """Incremental run adds only new messages."""
        mock_fetch.return_value = [_make_message(msg_id="inc_1", subject="First Email")]
        adapter = GmailAdapter(db, user_id)
        result1 = adapter.ingest(account="test@gmail.com")
        assert result1.events_count == 1

        mock_fetch.return_value = [
            _make_message(msg_id="inc_1", subject="First Email"),  # existing
            _make_message(
                msg_id="inc_2",
                subject="Second Email",
                date_str="Tue, 11 Feb 2026 09:00:00 -0800",
            ),  # new
        ]
        result2 = adapter.ingest(account="test@gmail.com")
        assert result2.events_count == 1
        assert db.count_events(user_id) == 2


# ---------------------------------------------------------------------------
# Backend detection helpers
# ---------------------------------------------------------------------------


class TestBackendDetection:
    @patch("shutil.which", return_value=None)
    def test_gog_not_installed(self, mock_which):
        """_gog_authenticated returns False when gog not installed."""
        assert _gog_authenticated("test@gmail.com") is False

    @patch("shutil.which", return_value="/usr/bin/gog")
    @patch("subprocess.run")
    def test_gog_no_tokens(self, mock_run, mock_which):
        """_gog_authenticated returns False when no tokens stored."""
        mock_run.return_value = MagicMock(returncode=0, stdout="No tokens stored")
        assert _gog_authenticated("test@gmail.com") is False

    @patch("shutil.which", return_value="/usr/bin/gog")
    @patch("subprocess.run")
    def test_gog_authenticated(self, mock_run, mock_which):
        """_gog_authenticated returns True when tokens exist."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="test@gmail.com  gmail  2026-02-10T..."
        )
        assert _gog_authenticated("test@gmail.com") is True
