#!/usr/bin/env python3
"""Replay Sandbox — continual evaluation for Syke's memory pipeline.

Replays a frozen event dataset through the full synthesis pipeline one observed
day at a time, starting from empty state. "Day" here means a distinct
DATE(timestamp) present in the source DB for the selected user, not a contiguous
calendar day with gaps filled in. Snapshots the memex after each cycle and
records metrics.

See docs/RUNTIME_AND_REPLAY.md for the current replay workflow.

Replay source:
    A local frozen replay DB chosen by --source-db
    A source user chosen by --source-user-id

Important window semantics:
    --max-days N   => take the first N observed days after any start-day filter
    --start-day D  => start at the first observed day >= D

So a run like --max-days 31 uses a 31-day replay window from the larger frozen
dataset. It does not imply the source DB itself only contains 31 days of data.

Usage:
    python experiments/memory_replay.py \
        --source-db /path/to/local/frozen_replay.db \
        --output-dir /tmp/replay_output \
        --user-id replay_v1 \
        --source-user-id source_user \
        --dry-run

    python experiments/memory_replay.py \
        --source-db /path/to/local/frozen_replay.db \
        --output-dir /tmp/replay_output \
        --user-id replay_v1 \
        --source-user-id source_user \
        --max-days 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke.db import SykeDB
from syke.llm.backends.pi_synthesis import pi_synthesize as synthesize

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
    total_cost = sum(t.get("cost_usd") or 0 for t in tl)
    total_turns = sum(t.get("turns") or 0 for t in tl)
    total_in = sum(t.get("input_tokens") or 0 for t in tl)
    total_out = sum(t.get("output_tokens") or 0 for t in tl)
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

# Neutral condition: minimal prompt, no Syke-specific guidance.
_NEUTRAL_PROMPT = (
    "You are a memory assistant. Read the new events from events.db.\n"
    "Update syke.db and MEMEX.md to reflect the important durable changes.\n"
    "Stop when the workspace state is updated."
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
    parser.add_argument(
        "--max-days",
        type=int,
        help="Stop after N observed event days after any --start-day filter",
    )
    parser.add_argument(
        "--start-day",
        help="Start from the first observed day >= this date (YYYY-MM-DD)",
    )
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
    parser.add_argument(
        "--runtime",
        help="Legacy flag. Pi is the only supported replay runtime.",
    )
    parser.add_argument(
        "--model",
        help="Override model for this replay run",
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
    replay_db.event_conn.execute("ATTACH DATABASE ? AS source", (str(source_path),))

    # Insert events for this day
    replay_db.event_conn.execute(
        """INSERT INTO events
           SELECT * FROM source.events
           WHERE DATE(timestamp) = ? AND user_id = ?""",
        (day, source_user_id),
    )

    # If user IDs differ, update them
    if source_user_id != replay_user_id:
        replay_db.event_conn.execute(
            "UPDATE events SET user_id = ? WHERE DATE(timestamp) = ? AND user_id = ?",
            (replay_user_id, day, source_user_id),
        )

    replay_db.event_conn.commit()

    # Count copied
    count = count_events_for_day(replay_db.event_conn, replay_user_id, day)

    # Detach source
    replay_db.event_conn.execute("DETACH DATABASE source")

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
        "events_today": db.event_conn.execute(
            "SELECT COUNT(*) FROM events WHERE user_id = ? AND DATE(timestamp) = ?",
            (user_id, day),
        ).fetchone()[0],
        "events_total": db.event_conn.execute(
            "SELECT COUNT(*) FROM events WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0],
        "cost_usd": (result.get("cost_usd") or 0) if result else 0,
        "tool_calls": (result.get("tool_calls") or 0) if result else 0,
        "tool_name_counts": dict(result.get("tool_name_counts") or {}) if result else {},
        "turns": (result.get("num_turns") or 0) if result else 0,
        "input_tokens": (result.get("input_tokens") or 0) if result else 0,
        "output_tokens": (result.get("output_tokens") or 0) if result else 0,
        "cache_read_tokens": (result.get("cache_read_tokens") or 0) if result else 0,
        "duration_ms": (result.get("duration_ms") or 0) if result else 0,
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
    from syke.llm.backends.pi_synthesis import SKILL_PATH

    if condition == "no_pointers":
        base = SKILL_PATH.read_text(encoding="utf-8")
        return base.replace(_POINTER_INSTRUCTION, "")
    if condition == "neutral":
        return _NEUTRAL_PROMPT
    return None  # production


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


def _validate_workspace_contract(
    workspace_root: Path,
    syke_db_path: Path,
    *,
    require_events_db: bool,
) -> None:
    """Ensure replay keeps one writable store and one readonly evidence store."""
    issues: list[str] = []

    if not syke_db_path.exists():
        issues.append(f"missing canonical DB: {syke_db_path}")

    if require_events_db:
        events_db = workspace_root / "events.db"
        if not events_db.exists():
            issues.append(f"missing events snapshot DB: {events_db}")
        else:
            if _paths_match(events_db, syke_db_path):
                issues.append("events.db must not alias syke.db")
            if events_db.stat().st_mode & stat.S_IWUSR:
                issues.append("events.db is writable; expected readonly snapshot")

    if issues:
        joined = "; ".join(issues)
        raise RuntimeError(f"Replay workspace contract violation: {joined}")


def configure_replay_workspace(output_dir: Path) -> tuple[Path, Path]:
    """Bind replay runs to an isolated Pi workspace under the run output dir."""
    from syke.runtime import stop_pi_runtime
    from syke.runtime import workspace as workspace_module
    from syke.llm.backends import pi_synthesis as pi_synthesis_module

    workspace_root = output_dir / "workspace"
    sessions_dir = workspace_root / "sessions"
    events_db = workspace_root / "events.db"
    syke_db = workspace_root / "syke.db"
    memex_path = workspace_root / "MEMEX.md"
    workspace_state = workspace_root / ".workspace_state.json"

    stop_pi_runtime()

    workspace_module.WORKSPACE_ROOT = workspace_root
    workspace_module.SESSIONS_DIR = sessions_dir
    workspace_module.EVENTS_DB = events_db
    workspace_module.SYKE_DB = syke_db
    workspace_module.MEMEX_PATH = memex_path
    workspace_module.WORKSPACE_STATE = workspace_state

    pi_synthesis_module.WORKSPACE_ROOT = workspace_root
    pi_synthesis_module.SESSIONS_DIR = sessions_dir
    pi_synthesis_module.EVENTS_DB = events_db
    pi_synthesis_module.SYKE_DB = syke_db
    pi_synthesis_module.MEMEX_PATH = memex_path

    os.environ["SYKE_REPLAY_WORKSPACE"] = str(workspace_root)
    return workspace_root, syke_db


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
    runtime: str = "pi",
    model: str | None = None,
) -> dict[str, Any]:
    """Run the full replay experiment."""
    if runtime and runtime != "pi":
        raise ValueError("Replay runtime 'claude' has been removed. Use Pi.")

    # Use a neutral internal user_id so the DB doesn't leak experiment names.
    # The original user_id is preserved in metadata for tracking.
    external_user_id = user_id
    user_id = "user"

    started_at = datetime.now(UTC)

    # Get the full set of observed days from source before windowing.
    all_days = get_days_from_source(source_db_path, source_user_id)
    dataset_start_day = all_days[0] if all_days else None
    dataset_end_day = all_days[-1] if all_days else None
    dataset_total_days = len(all_days)

    # Apply replay window selection.
    days = list(all_days)

    # Filter by start_day if specified
    if start_day:
        days = [d for d in days if d >= start_day]

    # Limit by max_days if specified
    if max_days:
        days = days[:max_days]

    selected_start_day = days[0] if days else None
    selected_end_day = days[-1] if days else None

    total_events = 0
    for day in days:
        conn = sqlite3.connect(str(source_db_path))
        total_events += count_events_for_day(conn, source_user_id, day)
        conn.close()

    log.info(
        "Source dataset: %s (%d observed days, %s to %s, user=%s)",
        source_db_path,
        dataset_total_days,
        dataset_start_day or "n/a",
        dataset_end_day or "n/a",
        source_user_id,
    )
    log.info(
        "Selected replay window: %d observed days, %d events, %s to %s",
        len(days),
        total_events,
        selected_start_day or "n/a",
        selected_end_day or "n/a",
    )

    if dry_run:
        print(
            f"Dry run: dataset has {dataset_total_days} observed days "
            f"({dataset_start_day} to {dataset_end_day}); selected window has "
            f"{len(days)} observed days ({selected_start_day} to {selected_end_day}), "
            f"{total_events} total events"
        )
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
    workspace_root, replay_db_path = configure_replay_workspace(output_dir)
    log.info("Replay workspace: %s", workspace_root)

    # Create fresh run-local canonical DB.
    _unlink_if_present(replay_db_path)

    replay_db = SykeDB(replay_db_path)
    # SykeDB auto-initializes

    _validate_workspace_contract(workspace_root, replay_db_path, require_events_db=False)

    # --skill flag overrides both --condition and synthesis.md
    if skill_file:
        skill_override = skill_file.read_text(encoding="utf-8")
    else:
        skill_override = build_skill_override(condition)

    # Read the actual Pi synthesis skill file for provenance.
    from syke.llm.backends.pi_synthesis import SKILL_PATH

    try:
        skill_text = skill_override if skill_override is not None else SKILL_PATH.read_text(encoding="utf-8")
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
                replay_db.event_conn.execute(
                    "DELETE FROM events WHERE user_id = ? AND source = 'syke'",
                    (user_id,),
                )
                replay_db.event_conn.commit()

            _purge_syke()

            result = synthesize(
                replay_db,
                user_id,
                force=True,
                skill_override=skill_override,
                model_override=model,
            )

            _purge_syke()  # Clean up traces created during this cycle
            _validate_workspace_contract(workspace_root, replay_db_path, require_events_db=True)

            # Advance cursor to last non-trace event of this day
            last_event_row = replay_db.event_conn.execute(
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
                "syke_db": str(replay_db_path),
                "events_db": str(workspace_root / "events.db"),
                "user_id": external_user_id,
                "internal_user_id": user_id,
                "source_user_id": source_user_id,
                "condition": condition,
                "dataset_start_day": dataset_start_day,
                "dataset_end_day": dataset_end_day,
                "dataset_observed_days": dataset_total_days,
                "selected_start_day": selected_start_day,
                "selected_end_day": selected_end_day,
                "selected_observed_days": len(days),
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
        from syke.runtime import stop_pi_runtime

        stop_pi_runtime()
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
        runtime=args.runtime,
        model=args.model,
    )


if __name__ == "__main__":
    main()
