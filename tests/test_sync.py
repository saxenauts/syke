"""Tests for sync-related functionality."""

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

from syke.db import SykeDB
from syke.models import Event


def test_get_last_sync_timestamp_none(db, user_id):
    """Returns None when no ingestion runs exist."""
    assert db.get_last_sync_timestamp(user_id, "claude-code") is None


def test_get_last_sync_timestamp(db, user_id):
    """Returns correct timestamp after a completed ingestion run."""
    run_id = db.start_ingestion_run(user_id, "claude-code")
    db.complete_ingestion_run(run_id, 10)

    ts = db.get_last_sync_timestamp(user_id, "claude-code")
    assert ts is not None
    # Should be a valid ISO timestamp
    datetime.fromisoformat(ts)


def test_get_last_sync_timestamp_per_source(db, user_id):
    """Each source tracks its own last sync independently."""
    run1 = db.start_ingestion_run(user_id, "claude-code")
    db.complete_ingestion_run(run1, 10)

    run2 = db.start_ingestion_run(user_id, "github")
    db.complete_ingestion_run(run2, 5)

    ts_cc = db.get_last_sync_timestamp(user_id, "claude-code")
    ts_gh = db.get_last_sync_timestamp(user_id, "github")
    assert ts_cc is not None
    assert ts_gh is not None
    # Gmail has no runs
    assert db.get_last_sync_timestamp(user_id, "gmail") is None


def test_get_last_sync_ignores_failed(db, user_id):
    """Failed ingestion runs are not returned."""
    run_id = db.start_ingestion_run(user_id, "claude-code")
    db.complete_ingestion_run(run_id, 0, error="something broke")

    assert db.get_last_sync_timestamp(user_id, "claude-code") is None



def test_sync_no_new_events_skips_perception(db, user_id):
    """When re-ingesting produces 0 new events, perception should be skippable.

    This tests the DB dedup behavior that sync relies on.
    """
    event = Event(
        user_id=user_id,
        source="claude-code",
        timestamp=datetime(2025, 6, 1, 12, 0),
        event_type="session",
        title="Existing session",
        content="Already ingested session content that is long enough.",
    )
    # First insert succeeds
    assert db.insert_event(event) is True
    # Second insert is a dedup — returns False
    assert db.insert_event(event) is False
    # Count unchanged
    assert db.count_events(user_id) == 1


def test_sync_threshold_constant():
    """SYNC_EVENT_THRESHOLD is set to 5."""
    from syke.sync import SYNC_EVENT_THRESHOLD
    assert SYNC_EVENT_THRESHOLD == 5


def test_gateway_push_event(db, user_id):
    """IngestGateway push writes events to the DB and deduplicates."""
    from syke.ingestion.gateway import IngestGateway

    gw = IngestGateway(db, user_id)
    result = gw.push(
        source="cli-test",
        event_type="observation",
        title="Gateway push test",
        content="Event pushed via the gateway code path.",
        external_id="gateway-test-001",
    )
    assert result["status"] == "ok"
    assert result["duplicate"] is False
    events = db.search_events(user_id, "Gateway push test")
    assert len(events) == 1
    assert events[0]["source"] == "cli-test"
    # Pushing again with same external_id should dedup
    result2 = gw.push(
        source="cli-test",
        event_type="observation",
        title="Gateway push test duplicate",
        content="This should be deduplicated.",
        external_id="gateway-test-001",
    )
    assert result2["status"] == "duplicate"
    assert db.count_events(user_id, source="cli-test") == 1






def test_github_adapter_detects_gh_token(db, user_id):
    """GitHubAdapter picks up token from `gh auth token` when no env var is set."""
    import os
    from syke.ingestion.github_ import GitHubAdapter

    saved = os.environ.pop("GITHUB_TOKEN", None)
    try:
        with patch.object(GitHubAdapter, "_detect_gh_token", return_value="ghp_testtoken123"):
            adapter = GitHubAdapter(db, user_id)
            assert adapter.token == "ghp_testtoken123"
    finally:
        if saved is not None:
            os.environ["GITHUB_TOKEN"] = saved


def test_github_adapter_no_gh_cli(db, user_id):
    """GitHubAdapter falls back to empty string when gh CLI is not installed."""
    import os
    from syke.ingestion.github_ import GitHubAdapter

    saved = os.environ.pop("GITHUB_TOKEN", None)
    try:
        with patch("subprocess.run", side_effect=FileNotFoundError("gh not found")):
            adapter = GitHubAdapter(db, user_id)
            assert adapter.token == ""
    finally:
        if saved is not None:
            os.environ["GITHUB_TOKEN"] = saved
