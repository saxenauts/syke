"""Pi Synthesis — lightweight synthesis using PiClient instead of Claude Agent SDK.

Mirrors the synthesis.py flow but replaces the Claude SDK agentic loop with a
single-turn PiClient call. No MCP servers, no Bash tools, no sandbox — just
prompt → response → commit.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from syke.config import CFG, SYNC_EVENT_THRESHOLD
from syke.db import SykeDB
from syke.llm.pi_client import PiClient, resolve_pi_model
from syke.memory.memex import get_memex, update_memex

log = logging.getLogger(__name__)

_SKILL_DIR = Path(__file__).resolve().parent / "skills"
_DEFAULT_SKILL = _SKILL_DIR / "pi_synthesis.md"
_FALLBACK_PROMPT = (
    "You are Syke's synthesis agent. Given new events and the current memex, "
    "rewrite the memex to incorporate the new information. "
    "Return ONLY the full rewritten memex document."
)


def _load_skill(path_override: str | None = None) -> tuple[str, str]:
    """Load skill prompt and return (content, sha256_hash)."""
    if path_override is not None:
        h = hashlib.sha256(path_override.encode("utf-8")).hexdigest()
        return path_override, h
    try:
        content = _DEFAULT_SKILL.read_text(encoding="utf-8").strip()
        if not content:
            log.warning("Skill file at %s is empty, using fallback", _DEFAULT_SKILL)
            content = _FALLBACK_PROMPT
    except FileNotFoundError:
        log.warning("Skill file not found at %s, using fallback", _DEFAULT_SKILL)
        content = _FALLBACK_PROMPT
    return content, hashlib.sha256(content.encode("utf-8")).hexdigest()


def _fetch_pending_events(
    db: SykeDB, user_id: str, cursor_id: str | None
) -> list[dict]:
    """Fetch events after cursor, excluding self-observation traces."""
    if cursor_id:
        rows = db.conn.execute(
            "SELECT id, source, event_type, title, content, timestamp "
            "FROM events WHERE user_id = ? AND id > ? AND source != 'syke' "
            "ORDER BY id ASC",
            (user_id, cursor_id),
        ).fetchall()
    else:
        rows = db.conn.execute(
            "SELECT id, source, event_type, title, content, timestamp "
            "FROM events WHERE user_id = ? AND source != 'syke' "
            "ORDER BY id ASC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _format_events_block(events: list[dict], limit: int = 80_000) -> str:
    """Format events into a text block, truncating to stay within token limits."""
    lines: list[str] = []
    total = 0
    for ev in events:
        entry = (
            f"--- event {ev['id']} ---\n"
            f"source: {ev['source']}  type: {ev['event_type']}  "
            f"time: {ev['timestamp']}\n"
            f"title: {ev.get('title') or '(none)'}\n"
            f"{ev['content']}\n"
        )
        if total + len(entry) > limit:
            lines.append(f"... truncated ({len(events) - len(lines)} events remaining)")
            break
        lines.append(entry)
        total += len(entry)
    return "\n".join(lines)


def pi_synthesize(
    db: SykeDB,
    user_id: str,
    force: bool = False,
    skill_override: str | None = None,
) -> dict:
    """Run a single-turn Pi synthesis cycle.

    Args:
        db: SykeDB instance.
        user_id: Target user.
        force: When True, skip the event-threshold check (same semantics as
               synthesize()).
        skill_override: Optional skill prompt content to use instead of the
                        default on-disk skill file.

    Returns a metrics dict with keys: status, cost_usd, input_tokens,
    output_tokens, duration_ms, events_processed, memex_updated, error.
    """
    # ------------------------------------------------------------------
    # 1. Check cursor + count pending events + threshold gate
    # ------------------------------------------------------------------
    cursor_id = db.get_synthesis_cursor(user_id)
    if cursor_id:
        pending_count = db.count_events_after_id(
            user_id, cursor_id, exclude_source="syke"
        )
    else:
        pending_count = db.count_events(user_id) - db.count_events(
            user_id, source="syke"
        )

    if pending_count == 0:
        log.debug("Pi synthesis: 0 pending events for %s, skipping", user_id)
        return {"status": "skipped", "reason": "no_pending_events"}

    if not force and pending_count < SYNC_EVENT_THRESHOLD:
        log.debug(
            "Pi synthesis: %d pending < threshold %d for %s, skipping",
            pending_count,
            SYNC_EVENT_THRESHOLD,
            user_id,
        )
        return {"status": "skipped", "reason": "below_threshold"}

    # ------------------------------------------------------------------
    # 2. Load current memex + fetch pending events
    # ------------------------------------------------------------------
    memex_data = get_memex(db, user_id)
    current_memex = memex_data["content"] if memex_data else ""

    events = _fetch_pending_events(db, user_id, cursor_id)
    events_block = _format_events_block(events)

    # Determine new cursor (newest non-syke event)
    newest_row = db.conn.execute(
        "SELECT id FROM events WHERE user_id = ? AND source != 'syke' "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    new_cursor = newest_row[0] if newest_row else cursor_id

    # ------------------------------------------------------------------
    # 3. Load skill prompt
    # ------------------------------------------------------------------
    skill_content, skill_hash = _load_skill(skill_override)

    # ------------------------------------------------------------------
    # 4. Build prompt + call PiClient
    # ------------------------------------------------------------------
    model = resolve_pi_model(CFG)

    cycle_id = db.insert_cycle_record(
        user_id,
        cursor_start=cursor_id,
        skill_hash=skill_hash,
        model=model,
    )

    user_message = (
        f"## Current Memex\n\n{current_memex or '[Empty — first run]'}\n\n"
        f"## New Events ({pending_count})\n\n{events_block}\n\n"
        "Rewrite the memex to incorporate these events. "
        "Return ONLY the full rewritten memex document, no commentary."
    )

    start_ts = time.monotonic()

    try:
        with PiClient(model=model) as client:
            # Turn 1: set system context with skill prompt
            client.prompt(skill_content)
            # Turn 2: send the actual synthesis request
            result = client.prompt(user_message)
            # Grab session stats for cost tracking
            stats = client.command("get_session_stats")
    except Exception as exc:
        duration_ms = int((time.monotonic() - start_ts) * 1000)
        log.error("Pi synthesis failed for %s: %s", user_id, exc)
        db.complete_cycle_record(
            cycle_id,
            status="error",
            cost_usd=0,
            input_tokens=0,
            output_tokens=0,
            duration_ms=duration_ms,
        )
        return {
            "status": "error",
            "error": str(exc),
            "duration_ms": duration_ms,
            "events_processed": 0,
            "memex_updated": False,
        }

    duration_ms = int((time.monotonic() - start_ts) * 1000)
    content = result["output"].strip() if result.get("output") else ""

    # Extract token counts from usage or session stats
    usage = result.get("usage", {})
    if stats.get("success"):
        sdata = stats.get("data", stats)
        input_tokens = int(sdata.get("input_tokens", 0))
        output_tokens = int(sdata.get("output_tokens", 0))
        cost_usd = float(sdata.get("cost_usd", 0.0))
    else:
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        cost_usd = 0.0

    # ------------------------------------------------------------------
    # 5. On success: update memex + advance cursor
    # ------------------------------------------------------------------
    if not content:
        log.warning("Pi synthesis returned empty content for %s", user_id)
        db.complete_cycle_record(
            cycle_id,
            status="failed",
            cost_usd=cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
        )
        return {
            "status": "failed",
            "error": "empty response from model",
            "cost_usd": cost_usd,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration_ms": duration_ms,
            "events_processed": pending_count,
            "memex_updated": False,
        }

    update_memex(db, user_id, content)
    db.set_synthesis_cursor(user_id, new_cursor)

    # ------------------------------------------------------------------
    # 6. Complete cycle record
    # ------------------------------------------------------------------
    db.complete_cycle_record(
        cycle_id,
        status="completed",
        cursor_end=new_cursor,
        events_processed=pending_count,
        memories_created=0,
        memories_updated=0,
        links_created=0,
        memex_updated=1,
        cost_usd=cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
    )

    log.info(
        "Pi synthesis completed for %s: %d events, %d chars, $%.4f",
        user_id,
        pending_count,
        len(content),
        cost_usd,
    )

    # ------------------------------------------------------------------
    # 7. Return metrics
    # ------------------------------------------------------------------
    return {
        "status": "ok",
        "cost_usd": cost_usd,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_ms": duration_ms,
        "events_processed": pending_count,
        "memex_updated": True,
    }
