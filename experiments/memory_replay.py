#!/usr/bin/env python3
"""Replay Sandbox — continual evaluation for Syke's memory pipeline.

Replays a frozen event dataset through the full synthesis pipeline day-by-day,
starting from empty state. Snapshots the memex after each cycle and records metrics.

See experiments/REPLAY_SANDBOX.md for full documentation.

Canonical frozen dataset:
    experiments/data/frozen_saxenauts.db  (128,904 events, 2025-08-20 to 2026-03-17)
    Source user ID: fresh_test

Usage:
    python experiments/memory_replay.py \
        --source-db experiments/data/frozen_saxenauts.db \
        --output-dir /tmp/replay_output \
        --user-id replay_v1 \
        --source-user-id fresh_test \
        --dry-run

    python experiments/memory_replay.py \
        --source-db experiments/data/frozen_saxenauts.db \
        --output-dir /tmp/replay_output \
        --user-id replay_v1 \
        --source-user-id fresh_test \
        --max-days 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke.db import SykeDB
from syke.memory.synthesis import synthesize

log = logging.getLogger(__name__)

_EXPERIMENTS_DB = Path(__file__).resolve().parent / "experiments.db"
_RUNS_DIR = Path(__file__).resolve().parent / "runs"


def _init_experiments_db() -> sqlite3.Connection:
    """Create/open the experiments DB with the runs table."""
    conn = sqlite3.connect(str(_EXPERIMENTS_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        condition TEXT,
        skill_hash TEXT,
        start_day TEXT,
        end_day TEXT,
        total_days INTEGER,
        total_events INTEGER,
        total_cost REAL,
        total_turns INTEGER,
        total_input_tokens INTEGER,
        total_output_tokens INTEGER,
        final_memex_chars INTEGER,
        total_memories INTEGER,
        total_links INTEGER,
        started_at TEXT,
        completed_at TEXT,
        results_path TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    conn.commit()
    return conn


def _register_run(output_dir: Path, result_data: dict[str, Any]) -> None:
    """Write run summary to experiments DB and regenerate manifest.json."""
    conn = _init_experiments_db()
    m = result_data.get("metadata", {})
    tl = result_data.get("timeline", [])

    run_name = output_dir.name
    run_id = f"{run_name}_{m.get('started_at', '')}"

    # Aggregate metrics from timeline
    total_cost = sum(t.get("cost_usd", 0) for t in tl)
    total_turns = sum(t.get("turns", 0) for t in tl)
    total_in = sum(t.get("input_tokens", 0) for t in tl)
    total_out = sum(t.get("output_tokens", 0) for t in tl)
    final_chars = tl[-1]["chars"] if tl else 0
    final_mems = tl[-1].get("memories_active", 0) if tl else 0
    final_links = tl[-1].get("links_count", 0) if tl else 0
    start_day = tl[0]["day"] if tl else None
    end_day = tl[-1]["day"] if tl else None

    conn.execute(
        """INSERT OR REPLACE INTO runs
           (id, name, condition, skill_hash, start_day, end_day,
            total_days, total_events, total_cost, total_turns,
            total_input_tokens, total_output_tokens,
            final_memex_chars, total_memories, total_links,
            started_at, completed_at, results_path)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id, run_name, m.get("condition"), m.get("skill_hash"),
            start_day, end_day,
            m.get("total_days", len(tl)), m.get("total_events", 0),
            total_cost, total_turns, total_in, total_out,
            final_chars, final_mems, final_links,
            m.get("started_at"), m.get("completed_at"),
            str(output_dir / "replay_results.json"),
        ),
    )
    conn.commit()

    # Regenerate manifest.json for the viz
    rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
    manifest = []
    for r in rows:
        manifest.append({
            "id": r["id"],
            "name": r["name"],
            "condition": r["condition"],
            "start_day": r["start_day"],
            "end_day": r["end_day"],
            "days": r["total_days"],
            "events": r["total_events"],
            "cost": r["total_cost"],
            "turns": r["total_turns"],
            "in_tokens": r["total_input_tokens"],
            "out_tokens": r["total_output_tokens"],
            "memex_chars": r["final_memex_chars"],
            "memories": r["total_memories"],
            "results_path": f"./runs/{r['name']}/replay_results.json",
        })
    (_RUNS_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    conn.close()

# Neutral condition: minimal prompt, no Syke-specific guidance. Call commit_cycle when done.
_NEUTRAL_PROMPT = (
    "You are a memory assistant. Read the new events and update the memory store.\n"
    "Create memories for important facts. Update existing memories when they change.\n"
    "Call commit_cycle when done."
)

# Pointer instruction line in the production skill file — removed for no_pointers condition.
_POINTER_INSTRUCTION = (
    "- Point to memories when details exist"
    " — the map routes, the memories hold the story.\n"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay events through full Syke pipeline")
    parser.add_argument("--source-db", required=True, help="Path to source DB with events")
    parser.add_argument("--output-dir", required=True, help="Directory for replay DB + results")
    parser.add_argument("--user-id", default="replay", help="User ID for replay")
    parser.add_argument(
        "--source-user-id",
        default=None,
        help="User ID in source DB (defaults to --user-id if not set)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count days/events without running synthesis",
    )
    parser.add_argument("--max-days", type=int, help="Stop after N days (for testing)")
    parser.add_argument("--start-day", help="Start from this date (YYYY-MM-DD)")
    parser.add_argument(
        "--condition",
        default="production",
        choices=["production", "no_pointers", "neutral"],
        help="Prompt condition for ablation",
    )
    parser.add_argument(
        "--skill",
        metavar="FILE",
        help="Path to custom skill/prompt file (overrides --condition and synthesis.md)",
    )
    return parser.parse_args()


def get_days_from_source(source_path: Path, user_id: str) -> list[str]:
    """Get distinct days from source DB, ordered chronologically."""
    conn = sqlite3.connect(str(source_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT DISTINCT DATE(timestamp) as day FROM events "
        "WHERE timestamp IS NOT NULL AND user_id = ? "
        "ORDER BY day",
        (user_id,),
    ).fetchall()
    conn.close()
    return [row["day"] for row in rows if row["day"]]


def count_events_for_day(conn: sqlite3.Connection, user_id: str, day: str) -> int:
    """Count events for a specific day."""
    row = conn.execute(
        "SELECT COUNT(*) FROM events WHERE user_id = ? AND DATE(timestamp) = ?",
        (user_id, day),
    ).fetchone()
    return row[0] if row else 0


def copy_events_for_day(
    replay_db: SykeDB,
    source_path: Path,
    source_user_id: str,
    replay_user_id: str,
    day: str,
) -> int:
    """Copy events for a specific day from source to replay DB. Returns count copied."""
    # Attach source DB and copy events
    replay_db.conn.execute("ATTACH DATABASE ? AS source", (str(source_path),))

    # Insert events for this day
    replay_db.conn.execute(
        """INSERT INTO events
           SELECT * FROM source.events
           WHERE DATE(timestamp) = ? AND user_id = ?""",
        (day, source_user_id),
    )

    # If user IDs differ, update them
    if source_user_id != replay_user_id:
        replay_db.conn.execute(
            "UPDATE events SET user_id = ? WHERE DATE(timestamp) = ? AND user_id = ?",
            (replay_user_id, day, source_user_id),
        )

    replay_db.conn.commit()

    # Count copied
    count = count_events_for_day(replay_db.conn, replay_user_id, day)

    # Detach source
    replay_db.conn.execute("DETACH DATABASE source")

    return count


def snapshot_memex(
    db: SykeDB,
    user_id: str,
    day: str,
    cycle_num: int,
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Capture memex state and metrics after synthesis."""
    memex = db.get_memex(user_id)
    content = memex["content"] if memex else ""

    # Count pointers (→ Memory: patterns)
    arrow_memory_pattern = len(re.findall(r"→\s*Memory:", content))

    return {
        "day": day,
        "cycle": cycle_num,
        "memex_version": cycle_num,
        "memex_content": content,
        "chars": len(content),
        "sections": content.count("## "),
        "arrows_total": content.count("→"),
        "arrows_memory": arrow_memory_pattern,
        "memories_active": db.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE user_id = ? AND active = 1",
            (user_id,),
        ).fetchone()[0],
        "memories_total": db.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0],
        "links_count": db.conn.execute(
            "SELECT COUNT(*) FROM links WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0],
        "events_today": db.conn.execute(
            "SELECT COUNT(*) FROM events WHERE user_id = ? AND DATE(timestamp) = ?",
            (user_id, day),
        ).fetchone()[0],
        "events_total": db.conn.execute(
            "SELECT COUNT(*) FROM events WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0],
        "cost_usd": result.get("cost_usd", 0) if result else 0,
        "turns": result.get("num_turns", 0) if result else 0,
        "input_tokens": result.get("input_tokens", 0) if result else 0,
        "output_tokens": result.get("output_tokens", 0) if result else 0,
        "cache_read_tokens": result.get("cache_read_tokens", 0) if result else 0,
        "duration_ms": result.get("duration_ms", 0) if result else 0,
        "status": result.get("status", "unknown") if result else "dry_run",
    }


def save_memex_version(output_dir: Path, version: int, content: str) -> None:
    """Save memex content to a versioned markdown file."""
    memex_dir = output_dir / "memex"
    memex_dir.mkdir(parents=True, exist_ok=True)
    version_path = memex_dir / f"v{version:03d}.md"
    version_path.write_text(content)


def build_skill_override(condition: str) -> str | None:
    """Return the skill file content to use for this ablation condition.

    Returns None for production (use the real skill file).
    Passes the string to synthesize(skill_override=...) — no global state.
    """
    from syke.memory.synthesis import _load_skill_file

    if condition == "no_pointers":
        base, _ = _load_skill_file()
        return base.replace(_POINTER_INSTRUCTION, "")
    if condition == "neutral":
        return _NEUTRAL_PROMPT
    return None  # production


def run_replay(
    source_db_path: Path,
    output_dir: Path,
    user_id: str,
    source_user_id: str,
    dry_run: bool,
    max_days: int | None,
    start_day: str | None,
    condition: str,
    skill_file: Path | None = None,
) -> dict[str, Any]:
    """Run the full replay experiment."""
    # Use a neutral internal user_id so the DB doesn't leak experiment names.
    # The original user_id is preserved in metadata for tracking.
    external_user_id = user_id
    user_id = "user"

    started_at = datetime.now(UTC)

    # Get days from source
    days = get_days_from_source(source_db_path, source_user_id)

    # Filter by start_day if specified
    if start_day:
        days = [d for d in days if d >= start_day]

    # Limit by max_days if specified
    if max_days:
        days = days[:max_days]

    total_events = 0
    for day in days:
        conn = sqlite3.connect(str(source_db_path))
        total_events += count_events_for_day(conn, source_user_id, day)
        conn.close()

    log.info(
        "Source: %s (%d days, %d events, user=%s)",
        source_db_path,
        len(days),
        total_events,
        source_user_id,
    )

    if dry_run:
        print(f"Dry run: {len(days)} days, {total_events} total events")
        for i, day in enumerate(days, 1):
            conn = sqlite3.connect(str(source_db_path))
            count = count_events_for_day(conn, source_user_id, day)
            conn.close()
            print(f"  Day {i}: {day} ({count} events)")
        return {
            "dry_run": True,
            "total_days": len(days),
            "total_events": total_events,
        }

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create fresh replay DB
    replay_db_path = output_dir / "replay.db"
    if replay_db_path.exists():
        replay_db_path.unlink()
    replay_db = SykeDB(replay_db_path)
    # SykeDB auto-initializes

    # --skill flag overrides both --condition and synthesis.md
    if skill_file:
        skill_override = skill_file.read_text(encoding="utf-8")
    else:
        skill_override = build_skill_override(condition)

    # Read skill file for provenance
    skill_path = Path(__file__).resolve().parent.parent / "syke" / "memory" / "skills" / "synthesis.md"
    try:
        skill_text = skill_override or skill_path.read_text(encoding="utf-8")
        skill_hash = hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
    except FileNotFoundError:
        skill_text, skill_hash = "", ""

    timeline: list[dict[str, Any]] = []
    cumulative_cost = 0.0

    try:
        for i, day in enumerate(days, 1):
            cycle_start = datetime.now(UTC).isoformat()
            # Copy events for this day
            events_copied = copy_events_for_day(
                replay_db,
                source_db_path,
                source_user_id,
                user_id,
                day,
            )

            # Purge syke-source events before AND after synthesis.
            # Before: clean slate so agent doesn't see prior cycle traces.
            # After: clean up traces created by self-observation hooks during synthesis.
            def _purge_syke():
                replay_db.conn.execute(
                    "DELETE FROM events WHERE user_id = ? AND source = 'syke'",
                    (user_id,),
                )
                replay_db.conn.commit()

            _purge_syke()

            # Run synthesis (skill_override=None means use the real skill file)
            result = synthesize(replay_db, user_id, force=True, skill_override=skill_override)

            _purge_syke()  # Clean up traces created during this cycle

            # Advance cursor to last non-trace event of this day
            last_event_row = replay_db.conn.execute(
                "SELECT id FROM events WHERE user_id = ? AND DATE(timestamp) = ? "
                "AND source != 'syke' ORDER BY timestamp DESC LIMIT 1",
                (user_id, day),
            ).fetchone()
            if last_event_row:
                replay_db.set_synthesis_cursor(user_id, last_event_row[0])

            # Snapshot memex
            snapshot = snapshot_memex(replay_db, user_id, day, i, result)

            # Collect memory ops for this cycle
            ops_rows = replay_db.conn.execute(
                "SELECT operation, input_summary, output_summary, memory_ids, created_at "
                "FROM memory_ops WHERE user_id = ? AND created_at >= ? ORDER BY created_at",
                (user_id, cycle_start),
            ).fetchall()
            snapshot["memory_ops"] = [dict(row) for row in ops_rows]
            snapshot["transcript"] = result.get("transcript", [])

            timeline.append(snapshot)

            # Save memex version
            memex = replay_db.get_memex(user_id)
            if memex and memex["content"]:
                save_memex_version(output_dir, i, memex["content"])

            # Track cumulative cost
            cost_val = result.get("cost_usd", 0)
            cost = float(cost_val) if isinstance(cost_val, (int, float)) else 0.0
            cumulative_cost += cost

            # Progress output
            print(
                f"Day {i}/{len(days)} | {day} | +{events_copied:,} events | "
                f"memex: {snapshot['chars']:,} chars | {snapshot['arrows_memory']} pointers | "
                f"${cost:.2f}"
            )

        # Finalize
        completed_at = datetime.now(UTC)

        result_data = {
            "metadata": {
                "source_db": str(source_db_path),
                "replay_db": str(replay_db_path),
                "user_id": external_user_id,
                "internal_user_id": user_id,
                "source_user_id": source_user_id,
                "condition": condition,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "total_days": len(days),
                "total_events": total_events,
                "total_cost_usd": cumulative_cost,
                "skill_content": skill_text,
                "skill_hash": skill_hash,
            },
            "timeline": timeline,
        }

        # Write results
        results_path = output_dir / "replay_results.json"
        results_path.write_text(json.dumps(result_data, indent=2))

        # Register in experiments DB + update manifest
        _register_run(output_dir, result_data)

        print(f"\nResults written to: {results_path}")
        print(f"Total cost: ${cumulative_cost:.2f}")

        return result_data

    finally:
        replay_db.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args()

    source_path = Path(args.source_db).resolve()
    if not source_path.exists():
        raise SystemExit(f"Source DB not found: {source_path}")

    output_dir = Path(args.output_dir).resolve()
    source_user_id = args.source_user_id or args.user_id

    skill_file = Path(args.skill).resolve() if args.skill else None
    if skill_file and not skill_file.exists():
        raise SystemExit(f"Skill file not found: {skill_file}")

    run_replay(
        source_db_path=source_path,
        output_dir=output_dir,
        user_id=args.user_id,
        source_user_id=source_user_id,
        dry_run=args.dry_run,
        max_days=args.max_days,
        start_day=args.start_day,
        condition=args.condition,
        skill_file=skill_file,
    )


if __name__ == "__main__":
    main()
