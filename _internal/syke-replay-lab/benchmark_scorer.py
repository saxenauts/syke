#!/usr/bin/env python3
"""Score benchmark results and produce reporting tables.

Usage:
  # Single system report
  python benchmark_scorer.py results/syke/benchmark_results.json

  # Compare two systems (McNemar's test)
  python benchmark_scorer.py results/syke/benchmark_results.json results/native/benchmark_results.json
"""

import json
import sys
from collections import Counter
from pathlib import Path


_HARD_REFUSAL_PATTERNS = (
    "i'm sorry",
    "i’m sorry",
    "i cannot assist",
    "i can't assist",
    "cannot assist with that request",
    "unable to assist",
    "unable to help",
)


def load_results(path: Path):
    return json.loads(path.read_text())


def _items(results) -> list[dict]:
    if isinstance(results, list):
        return results
    if isinstance(results, dict):
        if isinstance(results.get("items"), list):
            return results["items"]
        if isinstance(results.get("results"), list):
            return results["results"]
    return []


def _verdict(item: dict) -> str:
    return item.get("verdict") or item.get("final_verdict") or "unknown"


def _key(item: dict) -> tuple[str, str]:
    return (str(item.get("condition", "")), str(item.get("probe_id", "")))


def _answer_text(item: dict) -> str:
    return str(item.get("answer_text") or item.get("output_text") or "")


def _is_hard_refusal(item: dict) -> bool:
    text = _answer_text(item).lower()
    return any(pattern in text for pattern in _HARD_REFUSAL_PATTERNS)


def verdict_table(results, label: str) -> None:
    """Table 1: Verdict summary."""
    items = _items(results)
    counts = Counter(_verdict(item) for item in items)

    total = len(items)
    excluded = counts.get("invalid", 0)
    judged = total - excluded
    passes = counts.get("pass", 0)
    partials = counts.get("partial", 0)
    fails = counts.get("fail", 0)
    successes = passes
    rate = f"{successes}/{judged} ({100*successes/judged:.0f}%)" if judged > 0 else "0/0"
    total_tool_calls = sum(int(item.get("tool_calls") or 0) for item in items)
    total_cost_usd = sum(float(item.get("cost_usd") or 0.0) for item in items)
    hard_refusals = sum(1 for item in items if _is_hard_refusal(item))
    zero_search_success = sum(
        1 for item in items if _verdict(item) == "pass" and bool(item.get("zero_search"))
    )
    zero_search_success_rate = f"{zero_search_success/judged:.2f}" if judged else "0.00"
    tools_per_success = f"{total_tool_calls/successes:.1f}" if successes else "0.0"
    cost_per_success = f"{total_cost_usd/successes:.4f}" if successes else "0.0000"

    print(f"\n## {label}")
    print(f"| Verdict | Count |")
    print(f"|---------|-------|")
    print(f"| Pass    | {passes} |")
    print(f"| Partial | {partials} |")
    print(f"| Fail    | {fails} |")
    print(f"| Excluded| {excluded} |")
    print(f"| Hard refusals | {hard_refusals} |")
    print(f"| **Success Rate** | **{rate}** |")
    print(f"| Zero-search success rate | {zero_search_success_rate} |")
    print(f"| Tool calls / success | {tools_per_success} |")
    print(f"| Cost / success | {cost_per_success} |")


def failure_gallery(results, label: str) -> None:
    """Table 3: Failure gallery."""
    items = _items(results)
    fails = [i for i in items if _verdict(i) == "fail"]

    if not fails:
        print(f"\n## Failure Gallery ({label}): No failures")
        return

    print(f"\n## Failure Gallery ({label})")
    print(f"| Probe | Family | Summary |")
    print(f"|-------|--------|---------|")
    for item in fails:
        probe_id = item.get("probe_id", "?")
        family = item.get("family", "?")
        judge = item.get("judge_result", {})
        summary = judge.get("summary", "no judge summary")
        print(f"| {probe_id} | {family} | {summary} |")


def probe_level(results, label: str) -> None:
    """Table 4: Probe-level results."""
    items = _items(results)

    print(f"\n## Probe-Level Results ({label})")
    print(f"| Probe | Family | Dataset | Verdict | FG | C | H | Tools | Cost |")
    print(f"|-------|--------|---------|---------|----|---|---|-------|------|")
    for item in items:
        probe_id = item.get("probe_id", "?")
        family = item.get("family", "?")
        dataset = item.get("dataset_id", "?")
        verdict = _verdict(item)
        judge = item.get("judge_result", {})
        fg = _dim_short(judge, "factual_grounding")
        continuity = _dim_short(judge, "continuity")
        coherence = _dim_short(judge, "coherence")
        efficiency = item.get("efficiency") or {}
        tool_calls = efficiency.get("tool_calls", item.get("tool_calls", "?"))
        cost_usd = efficiency.get("cost_usd", item.get("cost_usd", "?"))
        print(f"| {probe_id} | {family} | {dataset} | {verdict} | {fg} | {continuity} | {coherence} | {tool_calls} | {cost_usd} |")


def _dim_short(judge: dict, dim: str) -> str:
    val = judge.get(dim, {})
    if isinstance(val, dict):
        score = val.get("score", "?")
    elif isinstance(val, str):
        score = val
    else:
        score = "?"
    return {"strong": "S", "partial": "P", "missed": "M"}.get(score, "?")


def mcnemar_test(results_a, results_b, label_a: str, label_b: str) -> None:
    """Table 2: McNemar's test comparing two systems."""
    items_a = {_key(i): i for i in _items(results_a)}
    items_b = {_key(i): i for i in _items(results_b)}

    common = set(items_a.keys()) & set(items_b.keys())

    # Collapse to binary success for paired comparison. Invalid rows are excluded.
    def is_success(item):
        return _verdict(item) == "pass"

    # Build 2x2 table
    a_success_b_success = 0
    a_success_b_not = 0
    a_not_b_success = 0
    a_not_b_not = 0

    for pair_key in sorted(common):
        a_item = items_a[pair_key]
        b_item = items_b[pair_key]
        if _verdict(a_item) == "invalid" or _verdict(b_item) == "invalid":
            continue
        a_u = is_success(a_item)
        b_u = is_success(b_item)
        if a_u and b_u:
            a_success_b_success += 1
        elif a_u and not b_u:
            a_success_b_not += 1
        elif not a_u and b_u:
            a_not_b_success += 1
        else:
            a_not_b_not += 1

    discordant = a_success_b_not + a_not_b_success

    print(f"\n## McNemar's Test: {label_a} vs {label_b}")
    print(f"| | {label_b} success | {label_b} not success |")
    print(f"|---|---|---|")
    print(f"| {label_a} success | {a_success_b_success} | {a_success_b_not} |")
    print(f"| {label_a} not success | {a_not_b_success} | {a_not_b_not} |")

    if discordant < 2:
        print(f"\nDiscordant pairs: {discordant} (insufficient for test)")
        return

    # McNemar's exact test (binomial)
    try:
        from scipy.stats import binom_test
        p = binom_test(a_success_b_not, discordant, 0.5)
    except ImportError:
        # Fallback: manual binomial
        from math import comb
        n = discordant
        k = min(a_success_b_not, a_not_b_success)
        p = 2 * sum(comb(n, i) * 0.5**n for i in range(k + 1))

    direction = f"{label_a} better" if a_success_b_not > a_not_b_success else f"{label_b} better"
    print(f"\nDiscordant pairs: {discordant}")
    print(f"Direction: {direction}")
    print(f"McNemar's exact p = {p:.4f}")
    if discordant < 10:
        print(f"Note: fewer than 10 discordant pairs — insufficient power to distinguish systems")


def paired_delta_report(results_a, results_b, label_a: str, label_b: str) -> None:
    """Show exact paired changes for overlapping condition×probe rows."""
    items_a = {_key(i): i for i in _items(results_a)}
    items_b = {_key(i): i for i in _items(results_b)}
    common = sorted(set(items_a.keys()) & set(items_b.keys()))

    if not common:
        print(f"\n## Paired Delta: {label_a} vs {label_b}")
        print("No overlapping condition×probe rows.")
        return

    verdict_transitions = Counter()
    hard_refusal_before = 0
    hard_refusal_after = 0
    hard_refusal_cleared = 0
    invalid_cleared = 0
    improved = 0

    def rank(item: dict) -> int:
        return {"invalid": 0, "fail": 1, "partial": 2, "pass": 3}.get(_verdict(item), -1)

    for pair_key in common:
        before = items_a[pair_key]
        after = items_b[pair_key]
        verdict_transitions[(_verdict(before), _verdict(after))] += 1

        before_refusal = _is_hard_refusal(before)
        after_refusal = _is_hard_refusal(after)
        hard_refusal_before += int(before_refusal)
        hard_refusal_after += int(after_refusal)
        if before_refusal and not after_refusal:
            hard_refusal_cleared += 1
        if _verdict(before) == "invalid" and _verdict(after) != "invalid":
            invalid_cleared += 1
        if rank(after) > rank(before):
            improved += 1

    print(f"\n## Paired Delta: {label_a} vs {label_b}")
    print(f"| Metric | Value |")
    print(f"|--------|-------|")
    print(f"| Overlapping rows | {len(common)} |")
    print(f"| Hard refusals in {label_a} | {hard_refusal_before} |")
    print(f"| Hard refusals in {label_b} | {hard_refusal_after} |")
    print(f"| Hard refusals cleared | {hard_refusal_cleared} |")
    print(f"| Invalid rows cleared | {invalid_cleared} |")
    print(f"| Rows improved (invalid→fail→partial→pass) | {improved} |")

    print("\n| Before | After | Count |")
    print("|--------|-------|-------|")
    for (before, after), count in sorted(verdict_transitions.items()):
        print(f"| {before} | {after} | {count} |")


def diagnostic_dimensions(results, label: str) -> None:
    """Diagnostic dimension breakdown (for prompt debugging, not headline)."""
    items = _items(results)
    dims = ["factual_grounding", "continuity", "coherence"]

    print(f"\n## Diagnostic Dimensions ({label})")
    print(f"| Dimension | Strong | Partial | Missed |")
    print(f"|-----------|--------|---------|--------|")
    for dim in dims:
        scores = []
        for item in items:
            judge = item.get("judge_result", {})
            val = judge.get(dim, {})
            if isinstance(val, dict):
                scores.append(val.get("score", "?"))
            elif isinstance(val, str):
                scores.append(val)
        c = Counter(scores)
        print(f"| {dim} | {c.get('strong', 0)} | {c.get('partial', 0)} | {c.get('missed', 0)} |")


def main():
    if len(sys.argv) < 2:
        print("Usage: benchmark_scorer.py <results.json> [results_b.json]")
        sys.exit(1)

    path_a = Path(sys.argv[1])
    results_a = load_results(path_a)
    label_a = path_a.parent.name or "System A"

    # Table 1: Verdict summary
    verdict_table(results_a, label_a)

    # Table 3: Failure gallery
    failure_gallery(results_a, label_a)

    # Table 4: Probe-level results
    probe_level(results_a, label_a)

    # Diagnostic dimensions
    diagnostic_dimensions(results_a, label_a)

    # Table 2: McNemar's test (if two files provided)
    if len(sys.argv) >= 3:
        path_b = Path(sys.argv[2])
        results_b = load_results(path_b)
        label_b = path_b.parent.name or "System B"

        verdict_table(results_b, label_b)
        failure_gallery(results_b, label_b)
        probe_level(results_b, label_b)
        diagnostic_dimensions(results_b, label_b)
        mcnemar_test(results_a, results_b, label_a, label_b)
        paired_delta_report(results_a, results_b, label_a, label_b)


if __name__ == "__main__":
    main()
