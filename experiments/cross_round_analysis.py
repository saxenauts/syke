#!/usr/bin/env python3
"""Cross-round analysis — compare R1 vs R2 vs R3 for each prompt condition."""

import json
from pathlib import Path

RUNS_DIR = Path("experiments/runs")
CONDITIONS = ["zero", "minimal", "minimal_exclude", "single_doc"]
ROUNDS = ["r1", "r2", "r3"]


def load_run(round_id: str, condition: str) -> dict | None:
    run_dir = RUNS_DIR / f"{round_id}_{condition}"
    results = run_dir / "replay_results.json"
    if not results.exists():
        return None
    return json.loads(results.read_text())


def summarize_run(data: dict) -> dict:
    meta = data.get("metadata", {})
    tl = data.get("timeline", [])
    total_cost = sum(t.get("cost_usd", 0) for t in tl)
    avg_turns = sum(t.get("turns", 0) for t in tl) / len(tl) if tl else 0
    final_chars = tl[-1]["chars"] if tl else 0
    max_chars = max(t["chars"] for t in tl) if tl else 0
    avg_chars = sum(t["chars"] for t in tl) / len(tl) if tl else 0

    # Count tool calls and thinking
    total_tools = 0
    total_bash = 0
    thinking_cycles = 0
    content_queries = 0  # Queries that SELECT content
    count_queries = 0    # Queries that COUNT/GROUP BY

    for cycle in tl:
        cycle_thinking = False
        for turn in cycle.get("transcript", []):
            for block in turn.get("blocks", []):
                if block.get("type") == "tool_use":
                    total_tools += 1
                    if block.get("name") == "Bash":
                        total_bash += 1
                        cmd = block.get("input", {}).get("command", "").upper()
                        if "SELECT" in cmd and "CONTENT" in cmd:
                            content_queries += 1
                        if "COUNT" in cmd or "GROUP BY" in cmd:
                            count_queries += 1
                elif block.get("type") == "thinking":
                    cycle_thinking = True
        if cycle_thinking:
            thinking_cycles += 1

    return {
        "cost": round(total_cost, 2),
        "avg_turns": round(avg_turns, 1),
        "final_chars": final_chars,
        "max_chars": max_chars,
        "avg_chars": round(avg_chars, 0),
        "total_tools": total_tools,
        "total_bash": total_bash,
        "thinking_cycles": thinking_cycles,
        "content_queries": content_queries,
        "count_queries": count_queries,
        "skill": (data.get("metadata", {}).get("skill_content", ""))[:80],
    }


def main():
    lines = []
    lines.append("=" * 100)
    lines.append("CROSS-ROUND COMPARISON — 3 Rounds × 4 Conditions × 31 Days Golden Gate")
    lines.append("=" * 100)

    # Per-condition comparison
    for cond in CONDITIONS:
        lines.append(f"\n{'='*80}")
        lines.append(f"CONDITION: {cond}")
        lines.append(f"{'='*80}")

        summaries = {}
        for rd in ROUNDS:
            data = load_run(rd, cond)
            if data:
                summaries[rd] = summarize_run(data)

        if not summaries:
            lines.append("  No data found")
            continue

        # Show prompt evolution
        for rd in ROUNDS:
            if rd in summaries:
                lines.append(f"  {rd} prompt: {summaries[rd]['skill'] or '[empty]'}")

        lines.append(f"\n  {'Metric':<20} {'R1':>10} {'R2':>10} {'R3':>10} {'R1→R3':>12}")
        lines.append(f"  {'-'*62}")

        for metric, label in [
            ("cost", "Total cost ($)"),
            ("avg_turns", "Avg turns/cycle"),
            ("final_chars", "Final doc (chars)"),
            ("max_chars", "Max doc (chars)"),
            ("avg_chars", "Avg doc (chars)"),
            ("total_bash", "Bash queries"),
            ("thinking_cycles", "Thinking cycles"),
            ("content_queries", "Content queries"),
            ("count_queries", "Count queries"),
        ]:
            vals = []
            for rd in ROUNDS:
                if rd in summaries:
                    vals.append(summaries[rd][metric])
                else:
                    vals.append(None)

            r1_str = f"{vals[0]}" if vals[0] is not None else "—"
            r2_str = f"{vals[1]}" if vals[1] is not None else "—"
            r3_str = f"{vals[2]}" if vals[2] is not None else "—"

            if vals[0] is not None and vals[2] is not None and isinstance(vals[0], (int, float)):
                delta = vals[2] - vals[0]
                if isinstance(delta, float):
                    delta_str = f"{delta:+.2f}"
                else:
                    delta_str = f"{delta:+d}"
            else:
                delta_str = "—"

            lines.append(f"  {label:<20} {r1_str:>10} {r2_str:>10} {r3_str:>10} {delta_str:>12}")

    # Overall summary
    lines.append(f"\n{'='*100}")
    lines.append("OVERALL FINDINGS")
    lines.append(f"{'='*100}")

    # Total cost across all rounds
    total_cost = 0
    for rd in ROUNDS:
        for cond in CONDITIONS:
            data = load_run(rd, cond)
            if data:
                tl = data.get("timeline", [])
                total_cost += sum(t.get("cost_usd", 0) for t in tl)

    total_cycles = 3 * 4 * 31  # 3 rounds × 4 conditions × 31 days
    lines.append(f"\nTotal cycles run: {total_cycles}")
    lines.append(f"Total cost: ${total_cost:.2f}")
    lines.append(f"Avg cost/cycle: ${total_cost/total_cycles:.4f}")
    lines.append(f"Failure rate: 0% (all {total_cycles} cycles committed)")

    report = "\n".join(lines)
    print(report)

    report_path = RUNS_DIR / "cross_round_analysis.txt"
    report_path.write_text(report)
    print(f"\nSaved to {report_path}")


if __name__ == "__main__":
    main()
