"""
Workspace management for the Pi agent runtime.

The workspace is the contract between Syke and Pi:
- events.db: read-only evidence snapshot
- syke.db: canonical writable learned-memory database
- MEMEX.md: routed memory artifact
- sessions/: Pi session JSONL
- scripts/, files/, scratch/: runtime-owned workspace
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import stat
import time
from pathlib import Path

from syke.config import user_events_db_path, user_syke_db_path
from syke.runtime.agents_md import write_agents_md

logger = logging.getLogger(__name__)

_WORKSPACE_ROOT_OVERRIDE = os.environ.get("SYKE_WORKSPACE_ROOT", "~/.syke/workspace")
WORKSPACE_ROOT = Path(os.path.expanduser(_WORKSPACE_ROOT_OVERRIDE))

# Workspace subdirectories the agent uses
WORKSPACE_DIRS = ["scripts", "files", "scratch"]

# Session storage for Pi JSONL audit trail
SESSIONS_DIR = WORKSPACE_ROOT / "sessions"

# Workspace databases
EVENTS_DB = WORKSPACE_ROOT / "events.db"
SYKE_DB = WORKSPACE_ROOT / "syke.db"

# Memex lives as a file the agent can also maintain
MEMEX_PATH = WORKSPACE_ROOT / "MEMEX.md"
WORKSPACE_STATE = WORKSPACE_ROOT / ".workspace_state.json"


def _artifact_state(path: Path) -> dict[str, int | bool]:
    if not path.exists():
        return {"exists": False}

    stat_result = path.stat()
    return {
        "exists": True,
        "size": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
    }


def _source_db_state(source_db_path: Path) -> dict[str, dict[str, int | bool]]:
    return {
        "main": _artifact_state(source_db_path),
        "wal": _artifact_state(Path(str(source_db_path) + "-wal")),
        "shm": _artifact_state(Path(str(source_db_path) + "-shm")),
    }


def _load_workspace_state() -> dict[str, object]:
    if not WORKSPACE_STATE.exists():
        return {}

    try:
        raw = json.loads(WORKSPACE_STATE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_workspace_state(state: dict[str, object]) -> None:
    WORKSPACE_STATE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _events_db_size() -> int:
    return EVENTS_DB.stat().st_size if EVENTS_DB.exists() else 0


def _path_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _unlink_if_present(path: Path) -> None:
    if _path_present(path):
        path.unlink()


def _paths_match(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return False


def _bind_syke_db(canonical_path: Path) -> None:
    canonical_path = canonical_path.expanduser()
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.touch(exist_ok=True)

    if _path_present(SYKE_DB):
        if _paths_match(SYKE_DB, canonical_path):
            return
        _unlink_if_present(SYKE_DB)

    if SYKE_DB.resolve() == canonical_path.resolve():
        SYKE_DB.touch(exist_ok=True)
        return

    target = Path(os.path.relpath(canonical_path, start=SYKE_DB.parent))
    SYKE_DB.symlink_to(target)


def _clear_workspace_subdir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def reset_workspace_artifacts(*, preserve_sessions: bool = True) -> None:
    """Clear agent-owned workspace state when the backing store changes."""
    logger.info("Resetting Pi workspace artifacts")

    for path in (SYKE_DB, MEMEX_PATH):
        _unlink_if_present(path)

    for subdir in WORKSPACE_DIRS:
        _clear_workspace_subdir(WORKSPACE_ROOT / subdir)

    if not preserve_sessions:
        _clear_workspace_subdir(SESSIONS_DIR)


def prepare_workspace(
    user_id: str,
    source_db_path: Path | None = None,
    syke_db_path: Path | None = None,
    *,
    force_refresh: bool = False,
) -> dict[str, object]:
    """Prepare the workspace and return refresh metadata for observability."""
    logger.info(f"Setting up workspace at {WORKSPACE_ROOT}")
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(exist_ok=True)

    for subdir in WORKSPACE_DIRS:
        (WORKSPACE_ROOT / subdir).mkdir(exist_ok=True)

    if source_db_path is None:
        source_db_path = user_events_db_path(user_id)
    if syke_db_path is None:
        syke_db_path = user_syke_db_path(user_id)

    source_db_path = source_db_path.expanduser()
    syke_db_path = syke_db_path.expanduser()
    prior_state = _load_workspace_state()
    prior_source = prior_state.get("source_db")
    prior_syke_db = prior_state.get("syke_db")
    current_source = str(source_db_path.resolve()) if source_db_path.exists() else str(source_db_path)
    current_syke_db = str(syke_db_path.resolve()) if syke_db_path.exists() else str(syke_db_path)
    binding_changed = (
        (isinstance(prior_source, str) and prior_source and prior_source != current_source)
        or (isinstance(prior_syke_db, str) and prior_syke_db and prior_syke_db != current_syke_db)
    )
    if binding_changed:
        from syke.runtime import stop_pi_runtime

        stop_pi_runtime()
        reset_workspace_artifacts()

    if source_db_path.exists():
        refresh = refresh_events_db(source_db_path, force=force_refresh)
    else:
        logger.warning(f"Source DB not found at {source_db_path}")
        refresh = {
            "refreshed": False,
            "reason": "source_missing",
            "duration_ms": 0,
            "source_db": str(source_db_path),
            "source_size_bytes": 0,
            "dest_size_bytes": _events_db_size(),
        }

    syke_db_created = not syke_db_path.exists()
    _bind_syke_db(syke_db_path)
    if syke_db_created:
        logger.info("Created canonical syke.db at %s", syke_db_path)

    state = _load_workspace_state()
    state["source_db"] = current_source
    state["syke_db"] = current_syke_db
    _write_workspace_state(state)

    write_agents_md(WORKSPACE_ROOT)

    # Write sandbox config — allow network for LLM provider API calls
    from syke.runtime.sandbox import write_sandbox_config

    write_sandbox_config(
        WORKSPACE_ROOT,
        allow_network=True,
        allowed_domains=[
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
        ],
    )

    logger.info("Workspace setup complete")
    return {
        "root": WORKSPACE_ROOT,
        "refresh": refresh,
        "syke_db_created": syke_db_created,
    }


def setup_workspace(
    user_id: str,
    source_db_path: Path | None = None,
    syke_db_path: Path | None = None,
) -> Path:
    """
    Initialize the workspace directory structure.

    Creates dirs, copies events.db as read-only, and binds workspace syke.db.
    Returns the workspace root path.
    """
    prepared = prepare_workspace(
        user_id,
        source_db_path=source_db_path,
        syke_db_path=syke_db_path,
    )
    return Path(prepared["root"])


def refresh_events_db(source_db_path: Path, *, force: bool = False) -> dict[str, object]:
    """
    Refresh the read-only events.db copy before a synthesis cycle.

    Uses SQLite online backup API for a consistent snapshot,
    then sets the file to read-only so the agent cannot modify it.
    """
    logger.info(f"Refreshing events.db from {source_db_path}")
    started = time.monotonic()
    source_db_path = source_db_path.expanduser()
    source_state = _source_db_state(source_db_path)
    source_db_resolved = str(source_db_path.resolve())
    source_size_bytes = int(source_state["main"].get("size", 0)) if source_state["main"] else 0

    prior_state = _load_workspace_state()
    if (
        not force
        and EVENTS_DB.exists()
        and prior_state.get("source_db") == source_db_resolved
        and prior_state.get("source_state") == source_state
    ):
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info("events.db refresh skipped (source unchanged)")
        return {
            "refreshed": False,
            "reason": "unchanged",
            "duration_ms": duration_ms,
            "source_db": source_db_resolved,
            "source_size_bytes": source_size_bytes,
            "dest_size_bytes": _events_db_size(),
        }

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
    dest_size_bytes = EVENTS_DB.stat().st_size
    duration_ms = int((time.monotonic() - started) * 1000)
    state = _load_workspace_state()
    state.update(
        {
            "source_db": source_db_resolved,
            "source_state": source_state,
            "refreshed_at": time.time(),
            "events_db_size": dest_size_bytes,
        }
    )
    _write_workspace_state(state)
    logger.info(f"events.db refreshed ({dest_size_bytes} bytes, read-only)")
    return {
        "refreshed": True,
        "reason": "refreshed",
        "duration_ms": duration_ms,
        "source_db": source_db_resolved,
        "source_size_bytes": source_size_bytes,
        "dest_size_bytes": dest_size_bytes,
    }


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

    if not SYKE_DB.exists():
        issues.append("syke.db missing")

    if not (WORKSPACE_ROOT / "AGENTS.md").exists():
        issues.append("AGENTS.md missing")

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
        "syke_db_exists": SYKE_DB.exists(),
        "memex_exists": MEMEX_PATH.exists(),
    }

    if SYKE_DB.exists():
        status["syke_db_size"] = SYKE_DB.stat().st_size

    if EVENTS_DB.exists():
        status["events_db_size"] = EVENTS_DB.stat().st_size
        status["events_db_readonly"] = not os.access(EVENTS_DB, os.W_OK)

    state = _load_workspace_state()
    if state:
        status["syke_db_target"] = state.get("syke_db")
        status["events_db_source"] = state.get("source_db")
        status["events_db_last_refresh_epoch"] = state.get("refreshed_at")

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
    Count events pending synthesis by reading the cursor from workspace syke.db
    and counting events in the workspace events.db.

    Returns (pending_count, cursor_value).
    """
    if not EVENTS_DB.exists():
        return 0, None

    cursor_val = None
    if SYKE_DB.exists():
        state_conn = sqlite3.connect(f"file:{SYKE_DB}?mode=ro", uri=True)
        try:
            try:
                row = state_conn.execute(
                    "SELECT last_event_id FROM synthesis_cursor WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                if row:
                    cursor_val = row[0]
            except sqlite3.OperationalError:
                pass
        finally:
            state_conn.close()

    conn = sqlite3.connect(f"file:{EVENTS_DB}?mode=ro", uri=True)
    try:
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
