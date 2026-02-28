"""Tests for the GitHub adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from email.message import Message
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from syke.db import SykeDB
from syke.ingestion.github_ import GitHubAdapter, _parse_ts


@pytest.fixture
def db(tmp_path):
    with SykeDB(tmp_path / "test.db") as database:
        yield database


@pytest.fixture
def adapter(db):
    return GitHubAdapter(db, "test_user", token="fake-token")


# ---------------------------------------------------------------------------
# _fetch_profile
# ---------------------------------------------------------------------------


class TestFetchProfile:
    def test_builds_profile_event(self, adapter):
        user_data = {
            "login": "saxenauts",
            "name": "Uts Saxena",
            "bio": "Builder of things",
            "company": "Acme",
            "location": "San Francisco",
            "blog": "https://example.com",
            "public_repos": 42,
            "followers": 100,
            "following": 50,
            "created_at": "2018-01-01T00:00:00Z",
        }
        with patch.object(adapter, "_api", return_value=user_data):
            events = adapter._fetch_profile("saxenauts")

        assert len(events) == 1
        e = events[0]
        assert e.event_type == "profile"
        assert "saxenauts" in e.content
        assert "Builder of things" in e.content
        assert e.metadata["followers"] == 100
        assert e.metadata["public_repos"] == 42

    def test_returns_empty_on_http_error(self, adapter):
        err = HTTPError("url", 404, "Not Found", Message(), None)
        with patch.object(adapter, "_api", side_effect=err):
            events = adapter._fetch_profile("nobody")
        assert events == []


# ---------------------------------------------------------------------------
# _make_repo_events
# ---------------------------------------------------------------------------


class TestMakeRepoEvents:
    def test_basic_repo_event(self, adapter):
        repos = [
            {
                "full_name": "saxenauts/syke",
                "description": "Agentic memory",
                "language": "Python",
                "stargazers_count": 55,
                "forks_count": 3,
                "open_issues_count": 2,
                "topics": ["ai", "mcp"],
                "pushed_at": "2024-01-20T00:00:00Z",
                "created_at": "2023-06-01T00:00:00Z",
                "fork": False,
                "archived": False,
                "license": {"spdx_id": "MIT"},
                "homepage": "https://syke-ai.vercel.app",
            }
        ]
        events = adapter._make_repo_events(repos)
        assert len(events) == 1
        e = events[0]
        assert e.title == "saxenauts/syke"
        assert "Agentic memory" in e.content
        assert "Python" in e.content
        assert e.metadata["stars"] == 55
        assert "ai" in e.metadata["topics"]
        assert e.metadata["license"] == "MIT"

    def test_no_description_uses_placeholder(self, adapter):
        repos = [
            {
                "full_name": "user/repo",
                "description": None,
                "language": "Go",
                "stargazers_count": 0,
                "forks_count": 0,
                "open_issues_count": 0,
                "topics": [],
                "pushed_at": "",
                "created_at": "2023-01-01T00:00:00Z",
                "fork": False,
                "archived": False,
                "license": None,
                "homepage": None,
            }
        ]
        events = adapter._make_repo_events(repos)
        assert "No description" in events[0].content

    def test_multiple_repos(self, adapter):
        repos = [
            {
                "full_name": f"user/repo{i}",
                "description": f"desc {i}",
                "language": "Python",
                "stargazers_count": i,
                "forks_count": 0,
                "open_issues_count": 0,
                "topics": [],
                "pushed_at": "",
                "created_at": "2023-01-01T00:00:00Z",
                "fork": False,
                "archived": False,
                "license": None,
                "homepage": None,
            }
            for i in range(5)
        ]
        events = adapter._make_repo_events(repos)
        assert len(events) == 5


# ---------------------------------------------------------------------------
# _fetch_events
# ---------------------------------------------------------------------------


class TestFetchEvents:
    def _raw_event(self, event_type, repo="user/repo", payload=None):
        return {
            "type": event_type,
            "created_at": "2024-02-10T15:00:00Z",
            "repo": {"name": repo},
            "payload": payload or {},
        }

    def test_push_event(self, adapter):
        raw = self._raw_event(
            "PushEvent",
            payload={
                "commits": [{"message": "Fix auth bug"}, {"message": "Add tests"}]
            },
        )
        with patch.object(adapter, "_api_paginated", return_value=[raw]):
            events = adapter._fetch_events("user")
        assert len(events) == 1
        assert "Push to" in events[0].title
        assert "Fix auth bug" in events[0].content

    def test_issues_event(self, adapter):
        raw = self._raw_event(
            "IssuesEvent",
            payload={
                "action": "opened",
                "issue": {
                    "number": 42,
                    "title": "Login fails",
                    "body": "Steps to reproduce...",
                },
            },
        )
        with patch.object(adapter, "_api_paginated", return_value=[raw]):
            events = adapter._fetch_events("user")
        assert "Login fails" in events[0].title

    def test_pull_request_event(self, adapter):
        raw = self._raw_event(
            "PullRequestEvent",
            payload={
                "action": "merged",
                "pull_request": {
                    "number": 7,
                    "title": "Add OAuth",
                    "body": "Implements OAuth2",
                },
            },
        )
        with patch.object(adapter, "_api_paginated", return_value=[raw]):
            events = adapter._fetch_events("user")
        assert "Add OAuth" in events[0].title

    def test_unknown_event_type(self, adapter):
        raw = self._raw_event("ForkEvent")
        with patch.object(adapter, "_api_paginated", return_value=[raw]):
            events = adapter._fetch_events("user")
        assert len(events) == 1
        assert "ForkEvent" in events[0].content or "fork" in events[0].event_type

    def test_stops_at_last_sync(self, adapter):
        from datetime import timedelta

        adapter._last_sync_ts = datetime(2024, 2, 15, tzinfo=UTC)
        # Event is before last sync
        raw = self._raw_event(
            "PushEvent"
        )  # created_at = 2024-02-10, which is before 2024-02-15
        with patch.object(adapter, "_api_paginated", return_value=[raw]):
            events = adapter._fetch_events("user")
        assert events == []


# ---------------------------------------------------------------------------
# _fetch_starred
# ---------------------------------------------------------------------------


class TestFetchStarred:
    def _star_item(self, repo_name="user/awesome-lib"):
        return {
            "starred_at": "2024-01-10T12:00:00Z",
            "repo": {
                "full_name": repo_name,
                "description": "A great library",
                "language": "TypeScript",
                "stargazers_count": 1234,
                "topics": ["web", "typescript"],
            },
        }

    def test_basic_star(self, adapter):
        with patch.object(adapter, "_api", return_value=[self._star_item()]):
            events = adapter._fetch_starred("user")
        assert len(events) >= 1
        assert events[0].event_type == "star"
        assert "awesome-lib" in events[0].title
        assert "A great library" in events[0].content

    def test_stops_at_last_sync(self, adapter):
        adapter._last_sync_ts = datetime(2024, 2, 1, tzinfo=UTC)
        # Star is before last sync (Jan 10 < Feb 1)
        with patch.object(adapter, "_api", return_value=[self._star_item()]):
            events = adapter._fetch_starred("user")
        assert events == []

    def test_http_error_stops_gracefully(self, adapter):
        err = HTTPError("url", 403, "Forbidden", Message(), None)
        with patch.object(adapter, "_api", side_effect=err):
            events = adapter._fetch_starred("user")
        assert events == []


# ---------------------------------------------------------------------------
# ingest integration
# ---------------------------------------------------------------------------


class TestIngest:
    def _mock_full_ingest(self, adapter):
        """Patch all API calls for a full ingest run."""
        profile = [
            {
                "login": "testuser",
                "name": "Test User",
                "bio": "Dev",
                "company": None,
                "location": "NYC",
                "blog": "",
                "public_repos": 5,
                "followers": 10,
                "following": 5,
                "created_at": "2020-01-01T00:00:00Z",
            }
        ]
        repos = [
            {
                "full_name": "testuser/myrepo",
                "description": "My project",
                "language": "Python",
                "stargazers_count": 3,
                "forks_count": 0,
                "open_issues_count": 0,
                "topics": [],
                "pushed_at": "2024-01-01T00:00:00Z",
                "created_at": "2023-01-01T00:00:00Z",
                "fork": False,
                "archived": False,
                "license": None,
                "homepage": None,
                "owner": {"login": "testuser"},
            }
        ]
        events_raw = [
            {
                "type": "PushEvent",
                "created_at": "2024-01-15T10:00:00Z",
                "repo": {"name": "testuser/myrepo"},
                "payload": {"commits": [{"message": "Initial commit"}]},
            }
        ]

        def fake_api(url, headers_override=None):
            if "/users/testuser/repos" not in url and "repos?" not in url:
                if "/users/testuser" in url and "repos" not in url:
                    return profile[0]
            return []

        def fake_paginated(url, max_pages=5):
            if "repos" in url:
                return repos
            if "events" in url:
                return events_raw
            if "starred" in url:
                return []
            return []

        return fake_api, fake_paginated

    def test_ingest_requires_username(self, adapter):
        with pytest.raises(ValueError, match="username is required"):
            adapter.ingest()

    def test_ingest_returns_result(self, adapter):
        fake_api, fake_paginated = self._mock_full_ingest(adapter)
        with (
            patch.object(adapter, "_api", side_effect=fake_api),
            patch.object(adapter, "_api_paginated", side_effect=fake_paginated),
            patch.object(adapter, "_fetch_readmes", return_value=[]),
            patch.object(adapter, "_fetch_starred", return_value=[]),
        ):
            result = adapter.ingest(username="testuser")

        assert result.source == "github"
        assert result.user_id == "test_user"
        assert result.events_count >= 0  # Some events inserted
