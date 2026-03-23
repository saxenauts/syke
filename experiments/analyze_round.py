#!/usr/bin/env python3
"""Analyze a round of replay experiments — extract diagnostic signals from traces."""

import json
import sys
from pathlib import Path

def analyze_run(results_path: Path) -> dict:
    """Extract diagnostic metrics from a single run."""
    data = json.loads(results_path.read_text())
    meta = data.get("metadata", {})
    timeline = data.get("timeline", [])

    run_name = results_path.parent.name
    skill = meta.get("skill_content", "")[:200]

    # Per-cycle metrics
    chars_over_time = []
    costs = []
    turns_per_cycle = []
    tool_calls_per_cycle = []
    commit_failures = 0
    total_bash_queries = 0
    total_thinking_chars = 0
    thinking_cycles = 0

    # Behavioral patterns
    query_patterns = []  # What SQL the agent runs
    rewrite_deltas = []  # How much the doc changes per cycle
    agent_questions = 0  # How often agent asks instead of acts

    prev_chars = 0
    for cycle in timeline:
        chars = cycle.get("chars", 0)
        chars_over_time.append(chars)
        costs.append(cycle.get("cost_usd", 0))
        turns_per_cycle.append(cycle.get("turns", 0))

        # Analyze transcript
        transcript = cycle.get("transcript", [])
        cycle_tools = 0
        cycle_bash = 0
        cycle_thinking = 0

        for turn in transcript:
            for block in turn.get("blocks", []):
                btype = block.get("type", "")
                if btype == "tool_use":
                    cycle_tools += 1
                    if block.get("name") == "Bash":
                        cycle_bash += 1
                        cmd = block.get("input", {}).get("command", "")
                        if "sqlite3" in cmd or "SELECT" in cmd.upper():
                            query_patterns.append(cmd[:200])
                elif btype == "thinking":
                    thinking_text = block.get("text", "")
                    cycle_thinking += len(thinking_text)
                elif btype == "text":
                    text = block.get("text", "")
                    if "?" in text and len(text) < 500:
                        agent_questions += 1

        tool_calls_per_cycle.append(cycle_tools)
        total_bash_queries += cycle_bash
        total_thinking_chars += cycle_thinking
        if cycle_thinking > 0:
            thinking_cycles += 1

        if cycle.get("status") != "completed" and cycle.get("status") != "dry_run":
            if chars == 0:
                commit_failures += 1

        delta = chars - prev_chars
        rewrite_deltas.append(delta)
        prev_chars = chars

    total_cost = sum(costs)
    total_cycles = len(timeline)

    # Extract unique SQL patterns (deduplicated)
    unique_queries = list(set(q[:100] for q in query_patterns))[:20]

    # Document evolution analysis
    final_chars = chars_over_time[-1] if chars_over_time else 0
    max_chars = max(chars_over_time) if chars_over_time else 0
    growth_pattern = "stable"
    if len(chars_over_time) > 5:
        first_half = sum(chars_over_time[:len(chars_over_time)//2])
        second_half = sum(chars_over_time[len(chars_over_time)//2:])
        if second_half > first_half * 1.5:
            growth_pattern = "growing"
        elif second_half < first_half * 0.5:
            growth_pattern = "shrinking"

    return {
        "run_name": run_name,
        "skill_preview": skill,
        "total_cycles": total_cycles,
        "commit_failures": commit_failures,
        "commit_rate": f"{(total_cycles - commit_failures) / total_cycles * 100:.0f}%" if total_cycles else "N/A",
        "total_cost": round(total_cost, 2),
        "avg_cost_per_cycle": round(total_cost / total_cycles, 4) if total_cycles else 0,
        "avg_turns": round(sum(turns_per_cycle) / len(turns_per_cycle), 1) if turns_per_cycle else 0,
        "avg_tools": round(sum(tool_calls_per_cycle) / len(tool_calls_per_cycle), 1) if tool_calls_per_cycle else 0,
        "total_bash_queries": total_bash_queries,
        "final_doc_chars": final_chars,
        "max_doc_chars": max_chars,
        "growth_pattern": growth_pattern,
        "thinking_cycles": thinking_cycles,
        "total_thinking_chars": total_thinking_chars,
        "agent_questions": agent_questions,
        "unique_query_count": len(unique_queries),
        "sample_queries": unique_queries[:5],
        "chars_trajectory": chars_over_time,
        "cost_trajectory": costs,
    }


def compare_runs(analyses: list[dict]) -> str:
    """Generate comparison report."""
    lines = []
    lines.append("=" * 80)
    lines.append("ROUND ANALYSIS — Diagnostic Comparison")
    lines.append("=" * 80)

    # Summary table
    lines.append(f"\n{'Run':<25} {'Commit%':>8} {'Cost':>7} {'$/cyc':>7} {'Turns':>6} {'Tools':>6} {'Final':>7} {'Think':>6}")
    lines.append("-" * 80)
    for a in analyses:
        lines.append(
            f"{a['run_name']:<25} {a['commit_rate']:>8} ${a['total_cost']:>5.2f} "
            f"${a['avg_cost_per_cycle']:>5.4f} {a['avg_turns']:>6.1f} {a['avg_tools']:>6.1f} "
            f"{a['final_doc_chars']:>6}c {a['thinking_cycles']:>5}c"
        )

    # Behavioral diagnosis
    lines.append("\n" + "=" * 80)
    lines.append("BEHAVIORAL DIAGNOSIS")
    lines.append("=" * 80)

    for a in analyses:
        lines.append(f"\n### {a['run_name']}")
        lines.append(f"  Prompt: {a['skill_preview'][:100] or '[empty]'}")
        lines.append(f"  Growth: {a['growth_pattern']} (max={a['max_doc_chars']}c → final={a['final_doc_chars']}c)")
        lines.append(f"  Bash queries: {a['total_bash_queries']} total, {a['unique_query_count']} unique patterns")
        lines.append(f"  Questions asked: {a['agent_questions']} (clarification-seeking behavior)")
        lines.append(f"  Thinking: {a['thinking_cycles']}/{a['total_cycles']} cycles with thinking traces")
        if a['sample_queries']:
            lines.append(f"  Sample queries:")
            for q in a['sample_queries'][:3]:
                lines.append(f"    - {q[:120]}")

    # Document trajectory comparison
    lines.append("\n" + "=" * 80)
    lines.append("DOCUMENT SIZE TRAJECTORY (chars over cycles)")
    lines.append("=" * 80)
    max_cycles = max(len(a['chars_trajectory']) for a in analyses)
    header = f"{'Cycle':>6}"
    for a in analyses:
        name = a['run_name'].replace('r1_', '')[:12]
        header += f" {name:>12}"
    lines.append(header)
    for i in range(max_cycles):
        row = f"{i+1:>6}"
        for a in analyses:
            if i < len(a['chars_trajectory']):
                row += f" {a['chars_trajectory'][i]:>12,}"
            else:
                row += f" {'—':>12}"
        lines.append(row)

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        # Auto-discover runs matching a pattern
        runs_dir = Path("experiments/runs")
        pattern = sys.argv[1] if len(sys.argv) > 1 else "r1_"
        results = sorted(runs_dir.glob(f"{pattern}*/replay_results.json"))
    else:
        pattern = sys.argv[1]
        runs_dir = Path("experiments/runs")
        results = sorted(runs_dir.glob(f"{pattern}*/replay_results.json"))

    if not results:
        print(f"No results found matching pattern in experiments/runs/")
        return

    print(f"Found {len(results)} runs")
    analyses = []
    for r in results:
        print(f"  Analyzing {r.parent.name}...")
        analyses.append(analyze_run(r))

    report = compare_runs(analyses)
    print(report)

    # Save report
    report_path = runs_dir / f"{pattern.rstrip('_')}_analysis.txt"
    report_path.write_text(report)
    print(f"\nReport saved to {report_path}")


if __name__ == "__main__":
    main()
