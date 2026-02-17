"""Meta-learning runner — full recording harness for live meta-learning runs.

Wraps MetaLearningPerceiver.run_cycle() with a recording on_discovery callback,
dumps per-run artifacts (profile, trace, eval, strategy), and writes summary files.

Event log persists structured metadata only — no raw user content from tool results.
Thinking blocks contain agent analysis (not user data) and are safe to persist.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from syke.config import user_data_dir
from syke.db import SykeDB

console = Console()


def run_recorded_cycle(
    user_id: str,
    max_runs: int = 12,
    max_budget: float = 15.0,
    save: bool = True,
) -> Path:
    """Run meta-learning cycle with full recording. Returns path to output directory."""
    from experiments.perception.meta_perceiver import MetaLearningPerceiver

    db_path = user_data_dir(user_id) / "syke.db"
    db = SykeDB(db_path)
    db.initialize()

    try:
        events_count = db.count_events(user_id)
        sources = db.get_sources(user_id)
        if events_count == 0:
            console.print("[yellow]No events found. Run: syke setup first.[/yellow]")
            return Path()

        # Create output directory
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = user_data_dir(user_id) / "meta_runs" / timestamp
        output_dir.mkdir(parents=True, exist_ok=True)

        event_log_path = output_dir / "event_log.jsonl"

        console.print(f"\n[bold]Meta-Learning Live Run[/bold] — user: [cyan]{user_id}[/cyan]")
        console.print(f"  Events: {events_count} across {', '.join(sources)}")
        console.print(f"  Max runs: {max_runs}")
        console.print(f"  Budget cap: ${max_budget:.2f}")
        console.print(f"  Output: {output_dir}")
        console.print()

        # State for recording callback
        state: dict[str, Any] = {
            "seq": 0,
            "current_run": 0,
            "cumulative_cost": 0.0,
            "last_eval": None,
        }

        def on_discovery(event_type: str, detail: str) -> None:
            """Recording callback — logs every event to JSONL and displays to console."""
            state["seq"] += 1
            entry = {
                "seq": state["seq"],
                "ts": datetime.now(timezone.utc).isoformat(),
                "run": state["current_run"],
                "event": event_type,
                "content": detail,
                "cumulative_cost": state["cumulative_cost"],
            }

            # Write to event log
            with open(event_log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")

            # Capture eval_result for per-run artifact
            if event_type == "eval_result":
                try:
                    state["last_eval"] = json.loads(detail)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Console display
            if event_type == "meta_cycle":
                if "===" in detail:
                    # Extract run number from cycle header
                    try:
                        run_num = int(detail.split("run ")[1].split("/")[0])
                        state["current_run"] = run_num
                    except (IndexError, ValueError):
                        pass
                console.print(f"\n[bold yellow]{detail}[/bold yellow]")
            elif event_type == "tool_call":
                console.print(f"  [cyan]>[/cyan] {detail}")
            elif event_type == "thinking":
                # Show first 120 chars of thinking
                preview = detail[:120].replace("\n", " ")
                if len(detail) > 120:
                    preview += "..."
                console.print(f"  [dim]think:[/dim] {preview}")
            elif event_type == "reasoning":
                preview = detail[:150].replace("\n", " ")
                if len(detail) > 150:
                    preview += "..."
                console.print(f"  [blue]reason:[/blue] {preview}")
            elif event_type == "tool_result_meta":
                try:
                    meta = json.loads(detail)
                    tool = meta.get("tool", "?")
                    size = meta.get("result_size", 0)
                    empty = meta.get("was_empty", False)
                    count = meta.get("count")
                    label = f"[red]EMPTY[/red]" if empty else f"{count} results" if count else f"{size}B"
                    console.print(f"  [dim]  <- {tool}: {label}[/dim]")
                except (json.JSONDecodeError, TypeError):
                    pass
            elif event_type == "result":
                console.print(f"  [green]{detail}[/green]")
            elif event_type == "reflection":
                console.print(f"  [magenta]REFLECT:[/magenta] {detail}")
            elif event_type == "evolution":
                console.print(f"  [bold yellow]EVOLVE:[/bold yellow] {detail}")
            elif event_type == "eval_result":
                try:
                    er = json.loads(detail)
                    console.print(f"  [green]EVAL:[/green] {er.get('total_pct', 0):.1f}%")
                except (json.JSONDecodeError, TypeError):
                    pass
            elif event_type == "budget_stop":
                console.print(f"\n[bold red]{detail}[/bold red]")
            elif event_type == "hook_gate":
                console.print(f"  [red]GATE:[/red] {detail}")
            elif event_type == "hook_correction":
                console.print(f"  [yellow]CORRECTED:[/yellow] {detail}")

        # Run the cycle
        started_at = datetime.now(timezone.utc).isoformat()
        start_time = time.monotonic()

        perceiver = MetaLearningPerceiver(db, user_id)
        results = perceiver.run_cycle(
            n_runs=max_runs,
            on_discovery=on_discovery,
            save=save,
            max_budget_usd=max_budget,
        )

        completed_at = datetime.now(timezone.utc).isoformat()
        total_duration = time.monotonic() - start_time

        # Dump per-run artifacts
        for i, r in enumerate(results, 1):
            run_dir = output_dir / f"run_{i}"
            run_dir.mkdir(parents=True, exist_ok=True)

            # Profile
            (run_dir / "profile.json").write_text(
                r.profile.model_dump_json(indent=2)
            )

            # Trace
            (run_dir / "trace.json").write_text(
                json.dumps(r.trace.to_dict(), indent=2)
            )

            # Eval — read from event log (the last eval_result before this run's result)
            # Search backwards through event log for this run's eval
            eval_data = _extract_eval_for_run(event_log_path, i)
            if eval_data:
                (run_dir / "eval.json").write_text(
                    json.dumps(eval_data, indent=2)
                )

            # Strategy snapshot
            strategy = perceiver.archive.get_latest_strategy()
            if strategy and strategy.version > 0:
                (run_dir / "strategy.json").write_text(
                    json.dumps(strategy.to_dict(), indent=2)
                )

            state["cumulative_cost"] += r.metrics.cost_usd

        # Build summary
        total_cost = sum(r.metrics.cost_usd for r in results)
        score_trajectory = [r.trace.profile_score for r in results]
        cost_per_run = [r.metrics.cost_usd for r in results]
        strategy_versions = [r.strategy_version for r in results]

        summary = {
            "user_id": user_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "total_runs": len(results),
            "total_cost_usd": round(total_cost, 4),
            "total_duration_s": round(total_duration, 1),
            "max_budget_usd": max_budget,
            "budget_exhausted": total_cost >= max_budget,
            "score_trajectory": [round(s, 4) for s in score_trajectory],
            "cost_per_run": [round(c, 4) for c in cost_per_run],
            "strategy_versions": strategy_versions,
            "useful_searches_per_run": [len(r.trace.useful_searches) for r in results],
            "wasted_searches_per_run": [len(r.trace.wasted_searches) for r in results],
            "connections_per_run": [len(r.trace.discovered_connections) for r in results],
            "final_strategy": (
                perceiver.archive.get_latest_strategy().to_dict()
                if perceiver.archive.get_latest_strategy()
                else None
            ),
            "per_run": [
                {
                    "run": i + 1,
                    "score": round(r.trace.profile_score, 4),
                    "cost": round(r.trace.cost_usd, 4),
                    "turns": r.metrics.num_turns,
                    "useful": len(r.trace.useful_searches),
                    "wasted": len(r.trace.wasted_searches),
                    "connections": len(r.trace.discovered_connections),
                    "strategy_v": r.strategy_version,
                }
                for i, r in enumerate(results)
            ],
        }

        (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))

        # Generate markdown report
        md = _build_summary_md(summary, results, perceiver)
        (output_dir / "summary.md").write_text(md)

        # Print summary table
        _print_summary_table(results, total_cost, perceiver)

        console.print(f"\n[bold green]Recording complete.[/bold green]")
        console.print(f"  Output: {output_dir}")
        console.print(f"  Events logged: {state['seq']}")
        console.print(f"  Total cost: ${total_cost:.4f}")

        return output_dir

    finally:
        db.close()


def _extract_eval_for_run(event_log_path: Path, run_number: int) -> dict | None:
    """Extract the eval_result event for a specific run from the event log."""
    last_eval = None
    try:
        with open(event_log_path) as f:
            for line in f:
                entry = json.loads(line)
                if entry.get("run") == run_number and entry.get("event") == "eval_result":
                    try:
                        last_eval = json.loads(entry["content"])
                    except (json.JSONDecodeError, TypeError):
                        pass
    except FileNotFoundError:
        pass
    return last_eval


def _build_summary_md(summary: dict, results: list, perceiver) -> str:
    """Build a human-readable markdown summary report."""
    lines = [
        f"# Meta-Learning Run — {summary['user_id']}",
        "",
        f"**Started:** {summary['started_at']}",
        f"**Completed:** {summary['completed_at']}",
        f"**Runs:** {summary['total_runs']}",
        f"**Total cost:** ${summary['total_cost_usd']:.4f}",
        f"**Duration:** {summary['total_duration_s']:.0f}s",
        f"**Budget:** ${summary['max_budget_usd']:.2f} ({'exhausted' if summary['budget_exhausted'] else 'within limit'})",
        "",
        "## Score Trajectory",
        "",
        "| Run | Score | Cost | Turns | Useful | Wasted | Connections | Strategy |",
        "|-----|-------|------|-------|--------|--------|-------------|----------|",
    ]

    for pr in summary["per_run"]:
        lines.append(
            f"| {pr['run']} | {pr['score']:.0%} | ${pr['cost']:.4f} | "
            f"{pr['turns']} | {pr['useful']} | {pr['wasted']} | "
            f"{pr['connections']} | v{pr['strategy_v']} |"
        )

    scores = summary["score_trajectory"]
    if len(scores) >= 2:
        delta = scores[-1] - scores[0]
        direction = "improved" if delta > 0 else "declined" if delta < 0 else "unchanged"
        lines.extend([
            "",
            f"**Trend:** {direction} by {abs(delta):.0%} "
            f"({scores[0]:.0%} -> {scores[-1]:.0%})",
        ])

    # Strategy evolution
    strat = summary.get("final_strategy")
    if strat:
        lines.extend([
            "",
            "## Final Strategy",
            "",
            f"**Version:** v{strat.get('version', 0)} "
            f"(from {strat.get('derived_from_runs', 0)} runs)",
            "",
        ])
        ps = strat.get("productive_searches", [])
        if ps:
            lines.append("### Productive Searches")
            for s in ps[:8]:
                lines.append(
                    f"- `{s['query']}` — hit rate: {s['hit_rate']:.0%}, "
                    f"relevance: {s['relevance_score']:.0%}"
                )
            lines.append("")

        dead = strat.get("dead_end_searches", [])
        if dead:
            lines.append("### Dead Ends")
            for d in dead[:8]:
                lines.append(f"- `{d}`")
            lines.append("")

        xp = strat.get("cross_platform_topics", [])
        if xp:
            lines.append("### Cross-Platform Topics")
            for t in xp[:5]:
                lines.append(
                    f"- **{t['topic']}** — {', '.join(t['sources'])} "
                    f"(strength: {t['strength']:.0%})"
                )
            lines.append("")

        seq = strat.get("recommended_tool_sequence", [])
        if seq:
            lines.append(f"### Recommended Tool Sequence")
            lines.append(f"`{' -> '.join(seq)}`")
            lines.append("")

    # Cost breakdown
    lines.extend([
        "## Cost Breakdown",
        "",
        f"- Total: ${summary['total_cost_usd']:.4f}",
        f"- Average per run: ${summary['total_cost_usd'] / max(summary['total_runs'], 1):.4f}",
        f"- Min: ${min(summary['cost_per_run']):.4f}" if summary['cost_per_run'] else "",
        f"- Max: ${max(summary['cost_per_run']):.4f}" if summary['cost_per_run'] else "",
    ])

    return "\n".join(lines) + "\n"


def _print_summary_table(results: list, total_cost: float, perceiver) -> None:
    """Print Rich summary table to console."""
    table = Table(title="Meta-Learning Evolution Summary")
    table.add_column("Run", style="cyan", justify="right", width=5)
    table.add_column("Score", justify="right", style="green", width=8)
    table.add_column("Cost", justify="right", style="yellow", width=10)
    table.add_column("Cumul $", justify="right", style="yellow", width=10)
    table.add_column("Useful", justify="right", width=8)
    table.add_column("Wasted", justify="right", width=8)
    table.add_column("Connections", justify="right", width=12)
    table.add_column("Strategy", justify="right", width=10)

    cumulative = 0.0
    for i, r in enumerate(results):
        t = r.trace
        cumulative += r.metrics.cost_usd
        table.add_row(
            str(i + 1),
            f"{t.profile_score:.0%}",
            f"${t.cost_usd:.4f}",
            f"${cumulative:.2f}",
            str(len(t.useful_searches)),
            str(len(t.wasted_searches)),
            str(len(t.discovered_connections)),
            f"v{r.strategy_version}",
        )

    console.print(table)

    # Score trajectory
    scores = [r.trace.profile_score for r in results]
    if len(scores) >= 2:
        delta = scores[-1] - scores[0]
        direction = "[green]improved[/green]" if delta > 0 else "[red]declined[/red]" if delta < 0 else "[dim]unchanged[/dim]"
        console.print(f"\n  Score trajectory: {' -> '.join(f'{s:.0%}' for s in scores)}")
        console.print(f"  Overall: {direction} by {abs(delta):.0%}")

    # Strategy status
    strategy = perceiver.archive.get_latest_strategy()
    if strategy:
        console.print(f"\n  Strategy v{strategy.version}:")
        if strategy.productive_searches:
            queries = [ps.query for ps in strategy.productive_searches[:5]]
            console.print(f"    Productive: {', '.join(queries)}")
        if strategy.dead_end_searches:
            console.print(f"    Dead ends: {', '.join(strategy.dead_end_searches[:5])}")
        if strategy.cross_platform_topics:
            topics = [ct.topic for ct in strategy.cross_platform_topics[:3]]
            console.print(f"    Cross-platform: {', '.join(topics)}")

    console.print(f"\n  Total cost: ${total_cost:.4f}")
    console.print(f"  Archive: {perceiver.archive.run_count} traces")
