"""
Pi-based agentic synthesis.

Uses the persistent Pi runtime to run synthesis cycles.
The agent operates in the workspace with full tool access:
- reads events.db (immutable timeline)
- writes agent.db (memories, graph, whatever it needs)
- updates memex.md (living synthesis document)
- builds scripts in scripts/ (persistent analysis tools)

This replaces the old spawn-per-cycle PiClient approach with a
persistent runtime managed by the Syke daemon.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from syke.config import CFG, DATA_DIR
from syke.db import SykeDB
from syke.runtime import get_pi_runtime, start_pi_runtime, stop_pi_runtime
from syke.runtime.workspace import (
    AGENT_DB,
    EVENTS_DB,
    MEMEX_PATH,
    SESSIONS_DIR,
    WORKSPACE_ROOT,
    get_pending_event_count,
    refresh_events_db,
    setup_workspace,
    validate_workspace,
    workspace_status,
)
logger = logging.getLogger(__name__)

# ── Skill prompt loading ──────────────────────────────────────────────

SKILL_PATH = Path(__file__).parent / "skills" / "pi_synthesis.md"


def _load_skill_prompt(
    pending_count: int,
    cursor: str | None,
    cycle_number: int,
) -> str:
    """Load and hydrate the synthesis skill prompt."""
    if not SKILL_PATH.exists():
        raise FileNotFoundError(f"Skill prompt not found: {SKILL_PATH}")

    template = SKILL_PATH.read_text()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    return template.format(
        pending_count=pending_count,
        cursor=cursor or "none (first cycle)",
        current_time=now,
        cycle_number=cycle_number,
    )


# ── Cycle count ───────────────────────────────────────────────────────

def _get_cycle_count(db: SykeDB, user_id: str) -> int:
    """Get total completed synthesis cycles for this user."""
    try:
        rows = db.get_cycle_records(user_id, limit=1)
        if rows:
            # Approximate from cycle_records count
            conn = db.conn
            count = conn.execute(
                "SELECT COUNT(*) FROM cycle_records WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
            return count
    except Exception:
        pass
    return 0


# ── Post-cycle validation ────────────────────────────────────────────

def _validate_cycle_output() -> dict:
    """
    Validate what the agent produced during the cycle.

    Checks:
    - memex.md exists and is non-empty
    - agent.db exists and has been written to
    - No corruption detected
    """
    issues = []
    stats = {}

    # Check memex
    if MEMEX_PATH.exists():
        content = MEMEX_PATH.read_text().strip()
        stats["memex_size"] = len(content)
        if not content:
            issues.append("memex.md is empty")
    else:
        issues.append("memex.md was not created")

    # Check agent.db
    if AGENT_DB.exists() and AGENT_DB.stat().st_size > 0:
        try:
            conn = sqlite3.connect(str(AGENT_DB))
            # Check what tables exist
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            stats["agent_tables"] = [t[0] for t in tables]

            # Count memories if table exists
            for t in tables:
                if t[0] == "memories":
                    count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                    stats["memory_count"] = count
                    break
            conn.close()
        except sqlite3.Error as e:
            issues.append(f"agent.db read error: {e}")
    else:
        # Empty agent.db on first cycle is OK — agent will create schema
        stats["agent_db_empty"] = True

    # Check events.db wasn't tampered with
    if EVENTS_DB.exists():
        import os
        if os.access(EVENTS_DB, os.W_OK):
            issues.append("events.db is writable (security violation)")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "stats": stats,
    }


# ── Memex sync: workspace → Syke DB ─────────────────────────────────

def _sync_memex_to_db(db: SykeDB, user_id: str) -> bool:
    """
    Read memex.md from workspace and sync it into Syke's main DB
    so the distribution layer can serve it.
    """
    if not MEMEX_PATH.exists():
        logger.warning("No memex.md to sync")
        return False

    content = MEMEX_PATH.read_text().strip()
    if not content:
        logger.warning("memex.md is empty, skipping sync")
        return False

    from syke.memory.memex import update_memex

    try:
        update_memex(db, user_id, content)
        logger.info(f"Memex synced to DB ({len(content)} chars)")
        return True
    except Exception as e:
        logger.error(f"Failed to sync memex: {e}")
        return False


# ── Main entry point ──────────────────────────────────────────────────

def pi_synthesize(
    db: SykeDB,
    user_id: str,
    *,
    force: bool = False,
) -> dict:
    """
    Run one Pi synthesis cycle.

    This is the main entry point called by synthesis.py when runtime='pi'.

    Flow:
    1. Setup/validate workspace
    2. Refresh events.db snapshot
    3. Check pending event threshold
    4. Build skill prompt
    5. Send to persistent Pi runtime
    6. Validate output
    7. Sync memex to Syke DB
    8. Record cycle

    Returns dict with cycle results and metrics.
    """
    start_time = time.time()
    result = {
        "method": "pi",
        "status": "pending",
        "user_id": user_id,
    }

    # ── 1. Setup workspace ──
    source_db = Path(DATA_DIR) / user_id / "syke.db"
    setup_workspace(user_id, source_db_path=source_db)

    ws_validation = validate_workspace()
    if not ws_validation["valid"]:
        logger.error(f"Workspace validation failed: {ws_validation['issues']}")
        result["status"] = "error"
        result["error"] = f"Workspace invalid: {ws_validation['issues']}"
        return result

    # ── 2. Refresh events.db ──
    if source_db.exists():
        refresh_events_db(source_db)

    # ── 3. Check pending events ──
    pending_count, cursor = get_pending_event_count(user_id)
    threshold = 1
    if CFG and hasattr(CFG, "synthesis") and CFG.synthesis:
        threshold = getattr(CFG.synthesis, "event_threshold", 1)

    if pending_count < threshold and not force:
        logger.info(
            f"Below threshold: {pending_count} pending < {threshold} required"
        )
        result["status"] = "skipped"
        result["reason"] = f"Below threshold ({pending_count}/{threshold})"
        return result

    logger.info(f"Starting Pi synthesis: {pending_count} pending events")

    # ── 4. Build skill prompt ──
    cycle_number = _get_cycle_count(db, user_id) + 1
    prompt = _load_skill_prompt(pending_count, cursor, cycle_number)

    # ── 5. Record cycle start ──
    cycle_id = None
    try:
        cycle_id = db.insert_cycle_record(
            user_id=user_id,
            cursor_start=cursor,
            skill_hash="pi_synthesis",
            prompt_hash=str(hash(prompt))[:16],
            model="pi",
        )
    except Exception as e:
        logger.warning(f"Failed to record cycle start: {e}")

    # ── 6. Send to Pi runtime ──
    timeout = 300  # 5 minutes default
    if CFG and hasattr(CFG, "synthesis") and CFG.synthesis:
        timeout = getattr(CFG.synthesis, "timeout", 300)

    try:
        runtime = get_pi_runtime()
        pi_result = runtime.prompt(prompt, timeout=timeout)
    except Exception as e:
        logger.exception("Pi runtime failed during synthesis cycle")

        try:
            stop_pi_runtime()
        except Exception:
            logger.exception("Failed to stop Pi runtime during recovery")

        try:
            start_pi_runtime(
                workspace_dir=WORKSPACE_ROOT,
                session_dir=SESSIONS_DIR,
            )
            logger.info("Pi runtime restarted after synthesis failure")
        except Exception:
            logger.exception("Failed to restart Pi runtime during recovery")

        failure_duration = int((time.time() - start_time) * 1000)
        result["status"] = "error"
        result["error"] = f"Pi runtime failed: {e}"
        result["duration_ms"] = failure_duration
        result["total_duration_ms"] = failure_duration

        if cycle_id:
            try:
                db.complete_cycle_record(
                    cycle_id=cycle_id,
                    status="failed",
                    duration_ms=failure_duration,
                )
            except Exception:
                pass
        return result
    result["duration_ms"] = pi_result.duration_ms
    result["tool_calls"] = len(pi_result.tool_calls)

    if not pi_result.ok:
        logger.error(f"Pi synthesis failed: {pi_result.error}")
        result["status"] = "error"
        result["error"] = pi_result.error

        if cycle_id:
            try:
                db.complete_cycle_record(
                    cycle_id=cycle_id,
                    status="failed",
                    duration_ms=pi_result.duration_ms,
                )
            except Exception:
                pass
        return result

    # ── 7. Validate output ──
    validation = _validate_cycle_output()
    result["validation"] = validation

    if not validation["valid"]:
        logger.warning(f"Cycle output validation issues: {validation['issues']}")
        # Don't fail hard — first cycles may not produce everything

    # ── 8. Sync memex to Syke DB ──
    memex_synced = _sync_memex_to_db(db, user_id)
    result["memex_synced"] = memex_synced

    # ── 9. Advance cursor ──
    # Read the latest event ID from events.db to set as new cursor
    try:
        conn = sqlite3.connect(f"file:{EVENTS_DB}?mode=ro", uri=True)
        latest = conn.execute(
            "SELECT id FROM events WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        conn.close()

        if latest:
            db.set_synthesis_cursor(user_id, latest[0])
            result["cursor_end"] = latest[0]
            logger.info(f"Cursor advanced to {latest[0]}")
    except Exception as e:
        logger.warning(f"Failed to advance cursor: {e}")

    # ── 10. Complete cycle record ──
    total_duration = int((time.time() - start_time) * 1000)

    if cycle_id:
        try:
            db.complete_cycle_record(
                cycle_id=cycle_id,
                status="completed",
                cursor_end=result.get("cursor_end"),
                events_processed=pending_count,
                memex_updated=memex_synced,
                duration_ms=total_duration,
            )
        except Exception as e:
            logger.warning(f"Failed to complete cycle record: {e}")

    result["status"] = "completed"
    result["events_processed"] = pending_count
    result["total_duration_ms"] = total_duration
    result["workspace"] = workspace_status()

    logger.info(
        f"Pi synthesis complete: {pending_count} events, "
        f"{result.get('tool_calls', 0)} tool calls, "
        f"{total_duration}ms"
    )

    return result
