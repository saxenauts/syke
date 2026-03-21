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

# Condition prompts for ablation experiments
CONDITION_PROMPTS = {
    "no_pointers": None,  # Will patch SYNTHESIS_PROMPT to remove pointer line
    "neutral": """You are Syke's memory synthesizer. You maintain a living map of
who this person is — through memories you create, update, and connect.

CRITICAL CONTRACT: When you finish, you MUST call the finalize_memex tool exactly once.
Reserve your last turn for it. If nothing changed, call it with status='unchanged' immediately.
- status='updated' + full rewritten memex content when the memex should change.
- status='unchanged' when the current memex should stay as-is.
- Do not wrap the memex in XML or markdown code fences.

## Current Memex
{memex_content}
{new_events_summary}
Read the memex first. Keep it concise (aim for 3000-4000 chars). Summarize key patterns,
active projects, and recent context. Call finalize_memex when done.
{temporal_context}
Remember: call finalize_memex exactly once when done. Do not end without calling it.""",
}


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
        "status": result.get("status", "unknown") if result else "dry_run",
    }


def save_memex_version(output_dir: Path, version: int, content: str) -> None:
    """Save memex content to a versioned markdown file."""
    memex_dir = output_dir / "memex"
    memex_dir.mkdir(parents=True, exist_ok=True)
    version_path = memex_dir / f"v{version:03d}.md"
    version_path.write_text(content)


def patch_synthesis_prompt(condition: str) -> str | None:
    """Return original prompt if patching needed, None otherwise."""
    import syke.memory.synthesis as synth_module

    if condition == "no_pointers":
        # Remove the pointer line from the prompt
        original = synth_module.SYNTHESIS_PROMPT
        patched = original.replace(
            "- Point to memories when details exist — the map routes, the memories hold the story.\n",
            "",
        )
        synth_module.SYNTHESIS_PROMPT = patched
        return original
    elif condition == "neutral":
        original = synth_module.SYNTHESIS_PROMPT
        synth_module.SYNTHESIS_PROMPT = CONDITION_PROMPTS["neutral"]
        return original
    return None


def restore_synthesis_prompt(original: str | None) -> None:
    """Restore original prompt if it was patched."""
    if original is not None:
        import syke.memory.synthesis as synth_module

        synth_module.SYNTHESIS_PROMPT = original


def run_replay(
    source_db_path: Path,
    output_dir: Path,
    user_id: str,
    source_user_id: str,
    dry_run: bool,
    max_days: int | None,
    start_day: str | None,
    condition: str,
) -> dict[str, Any]:
    """Run the full replay experiment."""
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

    # Patch prompt if needed
    original_prompt = patch_synthesis_prompt(condition)

    timeline: list[dict[str, Any]] = []
    cumulative_cost = 0.0

    try:
        for i, day in enumerate(days, 1):
            # Copy events for this day
            events_copied = copy_events_for_day(
                replay_db,
                source_db_path,
                source_user_id,
                user_id,
                day,
            )

            # Run synthesis
            result = synthesize(replay_db, user_id, force=True)

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
                "user_id": user_id,
                "source_user_id": source_user_id,
                "condition": condition,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "total_days": len(days),
                "total_events": total_events,
                "total_cost_usd": cumulative_cost,
            },
            "timeline": timeline,
        }

        # Write results
        results_path = output_dir / "replay_results.json"
        results_path.write_text(json.dumps(result_data, indent=2))

        print(f"\nResults written to: {results_path}")
        print(f"Total cost: ${cumulative_cost:.2f}")

        return result_data

    finally:
        restore_synthesis_prompt(original_prompt)
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

    run_replay(
        source_db_path=source_path,
        output_dir=output_dir,
        user_id=args.user_id,
        source_user_id=source_user_id,
        dry_run=args.dry_run,
        max_days=args.max_days,
        start_day=args.start_day,
        condition=args.condition,
    )


if __name__ == "__main__":
    main()
