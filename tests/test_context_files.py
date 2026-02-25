"""Tests for context file distribution (memex → client context files)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from syke.distribution.context_files import (
    CLAUDE_GLOBAL_MD,
    distribute_memex,
    ensure_claude_include,
)


def test_distribute_memex_writes_file(db, user_id, tmp_path):
    """distribute_memex writes memex content to user data dir."""
    # Insert a memex memory
    from syke.models import Memory

    mem = Memory(
        id="memex-001",
        user_id=user_id,
        content="# Memex — test_user\n\n## Identity\nTest identity.",
        source_event_ids=["__memex__"],
    )
    db.insert_memory(mem)

    with patch("syke.config.user_data_dir", return_value=tmp_path):
        path = distribute_memex(db, user_id)

    assert path is not None
    assert path == tmp_path / "CLAUDE.md"
    written = path.read_text()
    # Preamble present
    assert "# Syke" in written
    assert "auto-generated" in written
    assert "syke ask" in written
    # Memex content present after preamble
    assert "# Memex — test_user" in written
    assert "Test identity." in written


def test_distribute_memex_returns_none_when_empty(db, user_id, tmp_path):
    """distribute_memex returns None when no memex exists and no data."""
    with patch("syke.config.user_data_dir", return_value=tmp_path):
        path = distribute_memex(db, user_id)

    assert path is None
    assert not (tmp_path / "CLAUDE.md").exists()


def test_distribute_memex_skips_placeholder(db, user_id, tmp_path):
    """distribute_memex returns None for placeholder '[No data yet.]' content."""
    with patch("syke.config.user_data_dir", return_value=tmp_path):
        path = distribute_memex(db, user_id)

    assert path is None


def test_ensure_claude_include_creates_file(tmp_path):
    """ensure_claude_include creates ~/.claude/CLAUDE.md if it doesn't exist."""
    fake_claude_md = tmp_path / ".claude" / "CLAUDE.md"

    with patch("syke.distribution.context_files.CLAUDE_GLOBAL_MD", fake_claude_md):
        result = ensure_claude_include("test_user")

    assert result is True
    assert fake_claude_md.exists()
    assert "@~/.syke/data/test_user/CLAUDE.md" in fake_claude_md.read_text()


def test_ensure_claude_include_appends_to_existing(tmp_path):
    """ensure_claude_include appends include line to existing CLAUDE.md."""
    fake_claude_md = tmp_path / ".claude" / "CLAUDE.md"
    fake_claude_md.parent.mkdir(parents=True)
    fake_claude_md.write_text("# Existing content\n\nSome user rules here.")

    with patch("syke.distribution.context_files.CLAUDE_GLOBAL_MD", fake_claude_md):
        result = ensure_claude_include("test_user")

    assert result is True
    content = fake_claude_md.read_text()
    # Original content preserved
    assert "# Existing content" in content
    assert "Some user rules here." in content
    # Include line appended
    assert "@~/.syke/data/test_user/CLAUDE.md" in content


def test_ensure_claude_include_idempotent(tmp_path):
    """ensure_claude_include does not duplicate the include line."""
    fake_claude_md = tmp_path / ".claude" / "CLAUDE.md"
    fake_claude_md.parent.mkdir(parents=True)
    fake_claude_md.write_text("# Rules\n\n@~/.syke/data/test_user/CLAUDE.md\n")

    with patch("syke.distribution.context_files.CLAUDE_GLOBAL_MD", fake_claude_md):
        result = ensure_claude_include("test_user")

    assert result is True
    content = fake_claude_md.read_text()
    # Should appear exactly once
    assert content.count(".syke/data/test_user/CLAUDE.md") == 1


def test_ensure_claude_include_handles_permission_error(tmp_path):
    """ensure_claude_include returns False on OSError."""
    fake_claude_md = tmp_path / "readonly" / "CLAUDE.md"

    with patch("syke.distribution.context_files.CLAUDE_GLOBAL_MD", fake_claude_md):
        # Make parent dir read-only so mkdir fails
        fake_claude_md.parent.mkdir(parents=True)
        fake_claude_md.parent.chmod(0o444)
        try:
            result = ensure_claude_include("test_user")
            assert result is False
        finally:
            fake_claude_md.parent.chmod(0o755)


def test_distribute_then_include_end_to_end(db, user_id, tmp_path):
    """Full flow: distribute memex then ensure include — file chain is valid."""
    from syke.models import Memory

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    claude_md = tmp_path / ".claude" / "CLAUDE.md"

    mem = Memory(
        id="memex-e2e",
        user_id=user_id,
        content="# Memex — test_user\n\nEnd to end test.",
        source_event_ids=["__memex__"],
    )
    db.insert_memory(mem)

    with patch("syke.config.user_data_dir", return_value=data_dir):
        path = distribute_memex(db, user_id)

    assert path is not None
    written = path.read_text()
    # Preamble + memex content both present
    assert "# Syke" in written
    assert "# Memex — test_user" in written
    assert "End to end test." in written

    with patch("syke.distribution.context_files.CLAUDE_GLOBAL_MD", claude_md):
        result = ensure_claude_include(user_id)

    assert result is True
    assert "@~/.syke/data/test_user/CLAUDE.md" in claude_md.read_text()
