"""
Workspace management for the Pi agent runtime.

The workspace is the contract between Syke and the agent:
- events.db: read-only timeline (Syke manages ingestion)
- agent.db: agent's own database (memories, graph, memex, anything)
- sessions/: Pi session JSONL (audit trail, replayable)
- scripts/: agent-developed analysis tools
- files/: agent-managed file storage
- scratch/: working memory
"""

from __future__ import annotations

import logging
import os
import sqlite3
import stat
from pathlib import Path

from syke.config import DATA_DIR

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(os.path.expanduser("~/.syke/workspace"))

# Workspace subdirectories the agent uses
WORKSPACE_DIRS = ["scripts", "files", "scratch"]

# Session storage for Pi JSONL audit trail
SESSIONS_DIR = WORKSPACE_ROOT / "sessions"

# The two databases
EVENTS_DB = WORKSPACE_ROOT / "events.db"
AGENT_DB = WORKSPACE_ROOT / "agent.db"

# Memex lives as a file the agent can also maintain
MEMEX_PATH = WORKSPACE_ROOT / "memex.md"


def setup_workspace(user_id: str, source_db_path: Path | None = None) -> Path:
    """
    Initialize the workspace directory structure.

    Creates dirs, copies events.db as read-only, ensures agent.db exists.
    Returns the workspace root path.
    """
    logger.info(f"Setting up workspace at {WORKSPACE_ROOT}")
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(exist_ok=True)

    for subdir in WORKSPACE_DIRS:
        (WORKSPACE_ROOT / subdir).mkdir(exist_ok=True)

    # Copy events.db as read-only snapshot
    if source_db_path is None:
        source_db_path = Path(DATA_DIR) / user_id / "syke.db"

    if source_db_path.exists():
        refresh_events_db(source_db_path)
    else:
        logger.warning(f"Source DB not found at {source_db_path}")

    # Ensure agent.db exists (agent creates its own schema)
    if not AGENT_DB.exists():
        AGENT_DB.touch()
        logger.info("Created empty agent.db")

    # Write sandbox config — allow network for LLM provider API calls
    from syke.runtime.sandbox import write_sandbox_config

    write_sandbox_config(WORKSPACE_ROOT, allow_network=True, allowed_domains=[
        "*.openai.azure.com",
        "*.services.ai.azure.com",
        "*.openai.com",
        "api.anthropic.com",
        "openrouter.ai",
        "api.z.ai",
        "api.kimi.com",
        "api.groq.com",
        "generativelanguage.googleapis.com",
        "127.0.0.1",
        "localhost",
    ])

    logger.info("Workspace setup complete")
    return WORKSPACE_ROOT


def refresh_events_db(source_db_path: Path) -> None:
    """
    Refresh the read-only events.db copy before a synthesis cycle.

    Uses SQLite online backup API for a consistent snapshot,
    then sets the file to read-only so the agent cannot modify it.
    """
    logger.info(f"Refreshing events.db from {source_db_path}")

    # Make writable (including WAL/SHM) if exists, for overwrite
    for suffix in ("", "-shm", "-wal"):
        p = Path(str(EVENTS_DB) + suffix)
        if p.exists():
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    # SQLite online backup — consistent even if source is being written to
    src = sqlite3.connect(str(source_db_path))
    dst = sqlite3.connect(str(EVENTS_DB))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    # Set read-only: owner can read, no one can write
    for suffix in ("", "-shm", "-wal"):
        p = Path(str(EVENTS_DB) + suffix)
        if p.exists():
            os.chmod(p, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    logger.info(f"events.db refreshed ({EVENTS_DB.stat().st_size} bytes, read-only)")

def validate_workspace() -> dict:
    """
    Validate workspace structure is intact.

    Returns dict with status and any issues found.
    """
    issues = []

    if not WORKSPACE_ROOT.exists():
        issues.append("workspace root missing")
        return {"valid": False, "issues": issues}

    if not EVENTS_DB.exists():
        issues.append("events.db missing")
    elif os.access(EVENTS_DB, os.W_OK):
        issues.append("events.db is writable (should be read-only)")

    if not AGENT_DB.exists():
        issues.append("agent.db missing")

    for subdir in WORKSPACE_DIRS:
        if not (WORKSPACE_ROOT / subdir).exists():
            issues.append(f"{subdir}/ directory missing")

    if not SESSIONS_DIR.exists():
        issues.append("sessions/ directory missing")

    return {"valid": len(issues) == 0, "issues": issues}


def workspace_status() -> dict:
    """Get workspace status for logging and diagnostics."""
    status = {
        "root": str(WORKSPACE_ROOT),
        "exists": WORKSPACE_ROOT.exists(),
        "events_db_exists": EVENTS_DB.exists(),
        "agent_db_exists": AGENT_DB.exists(),
        "memex_exists": MEMEX_PATH.exists(),
    }

    if AGENT_DB.exists():
        status["agent_db_size"] = AGENT_DB.stat().st_size

    if EVENTS_DB.exists():
        status["events_db_size"] = EVENTS_DB.stat().st_size
        status["events_db_readonly"] = not os.access(EVENTS_DB, os.W_OK)

    # Count agent scripts
    scripts_dir = WORKSPACE_ROOT / "scripts"
    if scripts_dir.exists():
        status["scripts_count"] = len(list(scripts_dir.glob("*.py")))

    # Count session files
    if SESSIONS_DIR.exists():
        status["session_count"] = len(list(SESSIONS_DIR.glob("*.jsonl")))

    return status


def get_pending_event_count(user_id: str) -> tuple[int, str | None]:
    """
    Count events pending synthesis by reading the cursor from the
    source DB and counting events in the workspace events.db.

    Returns (pending_count, cursor_value).
    """
    if not EVENTS_DB.exists():
        return 0, None

    conn = sqlite3.connect(f"file:{EVENTS_DB}?mode=ro", uri=True)
    try:
        # Get cursor
        cursor_val = None
        try:
            row = conn.execute(
                "SELECT last_event_id FROM synthesis_cursor WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row:
                cursor_val = row[0]
        except sqlite3.OperationalError:
            pass  # Table may not exist yet
        # Count pending events
        if cursor_val:
            count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE user_id = ? AND id > ?",
                (user_id, cursor_val),
            ).fetchone()[0]
        else:
            count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]

        return count, cursor_val
    finally:
        conn.close()
