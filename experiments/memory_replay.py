"""Memory Replay Experiment.

Replays the last N days of real events through the memory layer day-by-day,
then benchmarks ask() with and without accumulated memories.

Usage:
    .venv/bin/python experiments/memory_replay.py --dry-run
    .venv/bin/python experiments/memory_replay.py --days 7 --user saxenauts
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from syke.db import SykeDB
from syke.models import Event, UserProfile
from syke.memory.synthesis import synthesize, _get_new_events_summary
from syke.distribution.ask_agent import (
    ASK_TOOLS,
    ASK_SYSTEM_PROMPT_TEMPLATE,
    _patch_sdk_for_rate_limit,
)

from syke.memory.tools import create_memory_tools, MEMORY_TOOL_NAMES
from syke.memory.memex import get_memex_for_injection

log = logging.getLogger(__name__)

# Benchmark uses read-only memory tools (no mutation)
BENCHMARK_MEMORY_TOOLS = [
    "search_memories",
    "search_evidence",
    "follow_links",
    "get_recent_memories",
    "get_memex",
]

# Cost estimates for dry-run
CONSOLIDATION_COST_ESTIMATE = 0.25  # per day
BENCHMARK_COST_ESTIMATE = 0.15  # per question per arm


def get_experiment_db_path() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"/tmp/syke_experiment_{ts}.db"


def get_real_db_path(user: str) -> Path:
    return Path.home() / f".syke/data/{user}/syke.db"


def read_real_db_events(user: str, days: int) -> tuple[list[dict], dict | None]:
    """Read events from real DB (READ ONLY). Returns (events, profile_dict)."""
    real_path = get_real_db_path(user)
    if not real_path.exists():
        raise FileNotFoundError(f"Real DB not found: {real_path}")

    conn = sqlite3.connect(str(real_path))
    conn.row_factory = sqlite3.Row

    # WAL checkpoint before reading
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()

    # Read events from last N days
    rows = conn.execute(
        """SELECT id, user_id, source, timestamp, event_type, title, content, metadata
           FROM events
           WHERE user_id = ?
           AND timestamp >= datetime('now', ? || ' days')
           ORDER BY timestamp ASC""",
        (user, f"-{days}"),
    ).fetchall()

    events = [dict(row) for row in rows]

    # Read profile — real DB stores as profile_json blob
    profile_row = conn.execute(
        """SELECT profile_json FROM profiles WHERE user_id = ? ORDER BY created_at DESC LIMIT 1""",
        (user,),
    ).fetchone()
    profile = json.loads(profile_row["profile_json"]) if profile_row else None

    conn.close()
    return events, profile


def group_events_by_day(events: list[dict]) -> dict[str, list[dict]]:
    """Group events by UTC day (YYYY-MM-DD)."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        day = ev["timestamp"][:10]
        by_day[day].append(ev)
    return dict(sorted(by_day.items()))


def create_experiment_db(db_path: str, profile: dict | None, user: str) -> SykeDB:
    """Create a fresh experiment DB. No profile pre-loading — memex bootstraps from events."""
    db = SykeDB(db_path)
    return db


def insert_day_events(db: SykeDB, day_events: list[dict]) -> None:
    """Insert a day's events into experiment DB."""
    for ev_dict in day_events:
        metadata = ev_dict.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                import json as _json

                metadata = _json.loads(metadata)
            except Exception:
                metadata = {}

        event = Event(
            id=ev_dict["id"],
            user_id=ev_dict["user_id"],
            source=ev_dict["source"],
            timestamp=ev_dict["timestamp"],
            event_type=ev_dict["event_type"],
            title=ev_dict["title"] or "",
            content=ev_dict["content"] or "",
            metadata=metadata,
        )
        try:
            db.insert_event(event)
        except Exception:
            pass  # Skip duplicates


async def _run_benchmark_ask(
    db: SykeDB,
    user: str,
    question: str,
    use_memory: bool,
) -> dict:
    """Run a single benchmark question. Returns metrics dict."""
    from claude_agent_sdk import (
        ClaudeSDKClient,
        ClaudeAgentOptions,
        AssistantMessage,
        ResultMessage,
        TextBlock,
        PermissionResultAllow,
        create_sdk_mcp_server,
    )
    from syke.config import ASK_MODEL, ASK_MAX_TURNS, ASK_BUDGET

    _patch_sdk_for_rate_limit()

    event_count = db.count_events(user)

    # Build single merged MCP server (memory tools only, matching ask_agent.py)
    memory_tools_list = create_memory_tools(db, user)
    server = create_sdk_mcp_server(
        name="syke", version="1.0.0", tools=memory_tools_list
    )

    memex_content = get_memex_for_injection(db, user)
    system_prompt = ASK_SYSTEM_PROMPT_TEMPLATE.format(memex_content=memex_content)

    # Build tool allowlist — exclude mutation tools for benchmarks
    benchmark_mem_tools = BENCHMARK_MEMORY_TOOLS if use_memory else []
    allowed = [f"mcp__syke__{name}" for name in benchmark_mem_tools]

    tool_calls: list[str] = []

    async def _track_and_allow(tool_name, tool_input, context=None):
        tool_calls.append(tool_name)
        return PermissionResultAllow()

    os.environ.pop("CLAUDECODE", None)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"syke": server},
        allowed_tools=allowed,
        permission_mode="bypassPermissions",
        max_turns=ASK_MAX_TURNS,
        max_budget_usd=ASK_BUDGET,
        model=ASK_MODEL,
        can_use_tool=_track_and_allow,
        env={},
    )

    task = f"Answer this question about user '{user}' ({event_count} events in timeline):\n\n{question}"
    answer_parts: list[str] = []
    cost_usd = 0.0
    num_turns = 0

    start = time.time()
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(task)
            from claude_agent_sdk import ClaudeSDKError

            try:
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text.strip():
                                answer_parts.append(block.text.strip())
                    elif isinstance(message, ResultMessage):
                        cost_usd = message.total_cost_usd or 0.0
                        num_turns = message.num_turns or 0
                        break
            except ClaudeSDKError as e:
                if "Unknown message type" not in str(e):
                    raise
    except Exception as e:
        return {
            "answer": f"ERROR: {e}",
            "tool_calls_count": len(tool_calls),
            "tool_calls": tool_calls,
            "cost_usd": cost_usd,
            "duration_s": round(time.time() - start, 2),
            "num_turns": num_turns,
            "error": str(e),
        }

    return {
        "answer": answer_parts[-1] if answer_parts else "",
        "tool_calls_count": len(tool_calls),
        "tool_calls": tool_calls,
        "cost_usd": cost_usd,
        "duration_s": round(time.time() - start, 2),
        "num_turns": num_turns,
    }


BENCHMARK_TIMEOUT_S = 120  # Max seconds per benchmark question


def run_benchmark_ask(db: SykeDB, user: str, question: str, use_memory: bool) -> dict:
    """Sync wrapper for benchmark ask with timeout."""
    try:
        return asyncio.run(asyncio.wait_for(
            _run_benchmark_ask(db, user, question, use_memory),
            timeout=BENCHMARK_TIMEOUT_S,
        ))
    except asyncio.TimeoutError:
        return {
            "answer": f"TIMEOUT after {BENCHMARK_TIMEOUT_S}s",
            "tool_calls_count": 0,
            "tool_calls": [],
            "cost_usd": 0.0,
            "duration_s": float(BENCHMARK_TIMEOUT_S),
            "num_turns": 0,
            "error": f"Timed out after {BENCHMARK_TIMEOUT_S}s",
        }


def count_links_in_db(db: SykeDB, user: str) -> int:
    """Count links for a user."""
    try:
        rows = db.conn.execute(
            "SELECT COUNT(*) FROM links WHERE user_id = ?", (user,)
        ).fetchone()
        return rows[0] if rows else 0
    except Exception:
        return 0


def run_experiment(args: argparse.Namespace) -> None:
    """Run the full experiment."""
    user = args.user
    days = args.days
    dry_run = args.dry_run
    synthesis_only = getattr(args, "synthesis_only", False)

    print(f"\n{'=' * 60}")
    print(f"MEMORY REPLAY EXPERIMENT")
    print(
        f"User: {user} | Days: {days} | Dry-run: {dry_run} | Synthesis-only: {synthesis_only}"
    )
    print(f"{'=' * 60}\n")

    # Load benchmark questions
    questions_path = Path(__file__).parent / "benchmark_questions.json"
    if not questions_path.exists():
        print(f"ERROR: {questions_path} not found. Run Task 3 first.")
        sys.exit(1)
    questions = json.loads(questions_path.read_text())
    print(f"Loaded {len(questions)} benchmark questions")

    # Read real DB
    print(f"\nReading real DB for last {days} days...")
    events, profile = read_real_db_events(user, days)
    print(f"Found {len(events)} events")

    by_day = group_events_by_day(events)
    print(f"Grouped into {len(by_day)} days: {', '.join(sorted(by_day.keys()))}")

    if profile:
        identity_anchor = profile.get("identity_anchor", "")[:100]
        print(f"\nProfile: {identity_anchor}...")
    else:
        print("\nWARNING: No profile found")

    # Dry-run: show plan and exit
    if dry_run:
        print(f"\n{'=' * 60}")
        print("DRY RUN PLAN")
        print(f"{'=' * 60}")
        print(f"\nConsolidation runs ({len(by_day)} days):")
        for day, day_events in sorted(by_day.items()):
            by_source = defaultdict(int)
            for ev in day_events:
                by_source[ev["source"]] += 1
            events_seen = min(len(day_events), 30)
            print(
                f"  {day}: {len(day_events)} events ({dict(by_source)}) \u2192 synthesizer sees {events_seen}/30"
            )

        print(f"\nBenchmark questions ({len(questions)}):")
        for q in questions:
            print(f"  [{q['axis']}] {q['question'][:80]}...")

        synthesis_cost = len(by_day) * CONSOLIDATION_COST_ESTIMATE
        benchmark_cost = len(questions) * 2 * BENCHMARK_COST_ESTIMATE
        total_cost = synthesis_cost + benchmark_cost
        print(f"\nEstimated cost:")
        print(
            f"  Consolidation: {len(by_day)} \u00d7 ${CONSOLIDATION_COST_ESTIMATE:.2f} = ${synthesis_cost:.2f}"
        )
        print(
            f"  Benchmark: {len(questions)} questions \u00d7 2 arms \u00d7 ${BENCHMARK_COST_ESTIMATE:.2f} = ${benchmark_cost:.2f}"
        )
        print(f"  Total: ~${total_cost:.2f}")
        print(f"\nDRY RUN COMPLETE - no API calls made")
        return

    # Full run
    experiment_id = f"replay_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    db_path = get_experiment_db_path()
    print(f"\nExperiment ID: {experiment_id}")
    print(f"Experiment DB: {db_path}")

    # Create experiment DB with profile
    print("\nCreating experiment DB...")
    exp_db = create_experiment_db(db_path, profile, user)

    # Consolidation runs
    synthesis_runs = []
    print(f"\n{'=' * 40}")
    print("PHASE 1: CONSOLIDATION RUNS")
    print(f"{'=' * 40}")

    for day in sorted(by_day.keys()):
        day_events = by_day[day]
        by_source: dict[str, int] = defaultdict(int)
        for ev in day_events:
            by_source[ev["source"]] += 1

        print(f"\nDay {day}: inserting {len(day_events)} events...")
        insert_day_events(exp_db, day_events)

        total_events_in_db = exp_db.count_events(user)
        events_seen = min(len(day_events), 30)  # synthesis limit

        memories_before = exp_db.count_memories(user)
        links_before = count_links_in_db(exp_db, user)

        print(f"  Running synthesis (force=True)...")
        start = time.time()
        result = synthesize(exp_db, user, force=True)
        duration = round(time.time() - start, 2)

        memories_after = exp_db.count_memories(user)
        links_after = count_links_in_db(exp_db, user)
        memex = exp_db.get_memex(user)
        memex_content = memex["content"] if memex else ""
        memex_length = len(memex_content)

        run_data = {
            "day": day,
            "events_total": len(day_events),
            "events_by_source": dict(by_source),
            "events_seen_by_synthesizer": events_seen,
            "memories_before": memories_before,
            "memories_after": memories_after,
            "links_created": links_after - links_before,
            "memex_updated": result.get("memex_updated", False),
            "memex_length_chars": memex_length,
            "cost_usd": result.get("cost_usd", 0.0),
            "duration_s": duration,
            "status": result.get("status", "unknown"),
        }
        synthesis_runs.append(run_data)

        print(
            f"  Status: {result.get('status')} | Cost: ${result.get('cost_usd', 0):.3f} | "
            f"Memories: {memories_before}\u2192{memories_after} | Memex: {memex_length} chars | {duration}s"
        )

    # Benchmark runs
    benchmark_results = []
    benchmark_cost = 0.0
    benchmark_duration = 0.0

    if synthesis_only:
        print(f"\n{'=' * 40}")
        print("PHASE 2: BENCHMARK RUNS \u2014 SKIPPED (--synthesis-only)")
        print(f"{'=' * 40}")
    else:
        print(f"\n{'=' * 40}")
        print("PHASE 2: BENCHMARK RUNS")
        print(f"{'=' * 40}")

        # Create control DB (same events, no synthesis)
        control_db_path = db_path.replace(".db", "_control.db")
        print(f"\nCreating control DB (no memories): {control_db_path}")
        control_db = create_experiment_db(control_db_path, profile, user)
        for day in sorted(by_day.keys()):
            insert_day_events(control_db, by_day[day])
        print(
            f"Control DB: {control_db.count_events(user)} events, "
            f"{control_db.count_memories(user)} memories"
        )

        for q in questions:
            print(f"\nQuestion [{q['axis']}]: {q['question'][:60]}...")

            print("  With memory...")
            with_mem = run_benchmark_ask(exp_db, user, q["question"], use_memory=True)
            print(
                f"  \u2192 {with_mem['tool_calls_count']} tool calls, "
                f"${with_mem['cost_usd']:.3f}, {with_mem['duration_s']}s"
            )

            print("  Without memory...")
            without_mem = run_benchmark_ask(
                control_db, user, q["question"], use_memory=False
            )
            print(
                f"  \u2192 {without_mem['tool_calls_count']} tool calls, "
                f"${without_mem['cost_usd']:.3f}, {without_mem['duration_s']}s"
            )

            benchmark_results.append(
                {
                    "id": q["id"],
                    "question": q["question"],
                    "axis": q["axis"],
                    "with_memory": with_mem,
                    "without_memory": without_mem,
                }
            )

        benchmark_cost = sum(
            r["with_memory"]["cost_usd"] + r["without_memory"]["cost_usd"]
            for r in benchmark_results
        )
        benchmark_duration = sum(
            r["with_memory"]["duration_s"] + r["without_memory"]["duration_s"]
            for r in benchmark_results
        )

    # Compute totals
    synthesis_cost = sum(r["cost_usd"] for r in synthesis_runs)
    synthesis_duration = sum(r["duration_s"] for r in synthesis_runs)

    totals = {
        "synthesis_cost": round(synthesis_cost, 4),
        "benchmark_cost": round(benchmark_cost, 4),
        "total_cost": round(synthesis_cost + benchmark_cost, 4),
        "total_duration_s": round(synthesis_duration + benchmark_duration, 2),
    }

    # Build output
    output = {
        "experiment_id": experiment_id,
        "config": {"days": days, "user": user, "force": True},
        "profile_summary": (
            profile.get("identity_anchor", "")[:100] if profile else ""
        ),
        "synthesis_runs": synthesis_runs,
        "benchmark_results": benchmark_results,
        "totals": totals,
    }

    # Save JSON
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    output_path = results_dir / f"{experiment_id}.json"
    output_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to: {output_path}")

    # Human-readable summary
    print(f"\n{'=' * 60}")
    print("EXPERIMENT SUMMARY")
    print(f"{'=' * 60}")
    print(f"\nConsolidation ({len(synthesis_runs)} days):")
    for r in synthesis_runs:
        print(
            f"  {r['day']}: {r['events_total']} events \u2192 {r['memories_after']} memories, "
            f"{r['memex_length_chars']} char memex, ${r['cost_usd']:.3f}"
        )

    print(f"\nBenchmark ({len(benchmark_results)} questions):")
    print(f"  {'Question':<50} {'With':<20} {'Without':<20}")
    print(f"  {'-' * 90}")
    for r in benchmark_results:
        wm = r["with_memory"]
        wom = r["without_memory"]
        q_short = r["question"][:48]
        print(
            f"  {q_short:<50} {wm['tool_calls_count']} calls ${wm['cost_usd']:.3f}    "
            f"{wom['tool_calls_count']} calls ${wom['cost_usd']:.3f}"
        )

    print(f"\nTotals:")
    print(f"  Consolidation cost: ${totals['synthesis_cost']:.4f}")
    print(f"  Benchmark cost:     ${totals['benchmark_cost']:.4f}")
    print(f"  Total cost:         ${totals['total_cost']:.4f}")
    print(f"  Total duration:     {totals['total_duration_s']:.1f}s")
    print(f"\nEXPERIMENT COMPLETE")


def main() -> None:
    # Force unbuffered stdout so nohup/redirect captures output in real time
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, write_through=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, write_through=True)
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description="Memory Replay Experiment")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show plan without making API calls"
    )
    parser.add_argument(
        "--days", type=int, default=7, help="Number of days to replay (default: 7)"
    )
    parser.add_argument(
        "--user", default="saxenauts", help="User ID (default: saxenauts)"
    )
    parser.add_argument(
        "--synthesis-only",
        action="store_true",
        help="Run synthesis only, skip benchmark (fast)",
    )
    args = parser.parse_args()

    run_experiment(args)


if __name__ == "__main__":
    main()
