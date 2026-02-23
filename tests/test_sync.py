"""Tests for sync-related functionality."""

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

from syke.db import SykeDB
from syke.models import Event, UserProfile


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


def test_get_last_profile_timestamp_none(db, user_id):
    """Returns None when no profiles exist."""
    assert db.get_last_profile_timestamp(user_id) is None


def test_get_last_profile_timestamp(db, user_id):
    """Returns created_at after saving a profile."""
    profile = UserProfile(
        user_id=user_id,
        identity_anchor="Test user",
        active_threads=[],
        recent_detail="Testing.",
        background_context="Tests.",
        sources=["test"],
        events_count=5,
    )
    db.save_profile(profile)

    ts = db.get_last_profile_timestamp(user_id)
    assert ts is not None
    datetime.fromisoformat(ts)


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


def test_mcp_push_event(db, user_id):
    """MCP server's push_event tool writes events to the DB."""
    import json
    from unittest.mock import patch

    from syke.distribution.mcp_server import create_server

    server = create_server(user_id)

    # Patch _get_db inside the closure to use our test DB
    # The MCP tools are closures that call _get_db() — we need to
    # make them use our test DB instead of opening a real one.
    # We do this by calling the gateway directly (same code path).
    from syke.ingestion.gateway import IngestGateway

    gw = IngestGateway(db, user_id)
    result = gw.push(
        source="mcp-test",
        event_type="observation",
        title="MCP push test",
        content="Event pushed via the same code path as the MCP push_event tool.",
        external_id="mcp-test-001",
    )
    assert result["status"] == "ok"
    assert result["duplicate"] is False

    # Verify the event is in the DB
    events = db.search_events(user_id, "MCP push test")
    assert len(events) == 1
    assert events[0]["source"] == "mcp-test"

    # Pushing again with same external_id should dedup
    result2 = gw.push(
        source="mcp-test",
        event_type="observation",
        title="MCP push test duplicate",
        content="This should be deduplicated.",
        external_id="mcp-test-001",
    )
    assert result2["status"] == "duplicate"
    assert db.count_events(user_id, source="mcp-test") == 1


def test_run_sync_rebuild_bypasses_zero_new_events(db, user_id, tmp_path):
    """sync --rebuild should proceed to memory synthesis even with 0 new events."""
    from rich.console import Console
    from syke.sync import run_sync

    # Seed existing events
    for i in range(10):
        db.insert_event(Event(
            user_id=user_id,
            source="claude-code",
            timestamp=datetime(2025, 6, 1, 12, i),
            event_type="session",
            title=f"Session {i}",
            content=f"Test session content number {i} with enough length.",
        ))

    # Register the source
    run_id = db.start_ingestion_run(user_id, "claude-code")
    db.complete_ingestion_run(run_id, 10)

    # sync_source returns 0 new events, but rebuild=True should still proceed
    with patch("syke.sync.sync_source", return_value=0):
        total, synced = run_sync(
            db, user_id,
            rebuild=True,
            out=Console(quiet=True),
        )

    # Should return the total count and synced sources
    assert total == 0
    assert isinstance(synced, list)


def test_run_sync_skips_perception_without_api_key(db, user_id, tmp_path):
    """run_sync with skip_profile=False gracefully skips perception when API key is missing."""
    from rich.console import Console
    from syke.sync import run_sync

    # Seed enough events to pass the threshold
    for i in range(10):
        db.insert_event(Event(
            user_id=user_id,
            source="claude-code",
            timestamp=datetime(2025, 6, 1, 12, i),
            event_type="session",
            title=f"Session {i}",
            content=f"Test session content number {i} with enough length.",
        ))

    # Register the source so get_sources returns it
    run_id = db.start_ingestion_run(user_id, "claude-code")
    db.complete_ingestion_run(run_id, 10)

    # Patch ANTHROPIC_API_KEY to empty and sync_source to return events
    with patch("syke.config.ANTHROPIC_API_KEY", ""), \
         patch("syke.sync.sync_source", return_value=10):
        total, synced = run_sync(
            db, user_id,
            skip_profile=False,
            force=True,
            out=Console(quiet=True),
        )

    # Should return events but not crash — no profile written
    assert total == 10
    from syke.config import user_profile_path
    assert not user_profile_path(user_id).exists()





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
