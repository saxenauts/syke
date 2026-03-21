#!/usr/bin/env python3
"""Replay sandbox grader — post-hoc LLM judge for checkpoint hypotheses.

Reads hypotheses.jsonl (written by memory_replay.py --eval) and dataset.json
(frozen gold answers), scores each hypothesis against its rubric via LLM judge,
writes scores.jsonl.

Invariants:
- Never called during a replay run (post-hoc only)
- Dataset is read-only — gold answers are never modified
- Hypotheses and scores are separate files so you can re-grade without re-running

Usage:
    python experiments/eval/grader.py \
        /tmp/replay_output/hypotheses.jsonl \
        --dataset experiments/eval/dataset.json \
        --output /tmp/replay_output/scores.jsonl

    # Print summary table
    python experiments/eval/grader.py \
        /tmp/replay_output/hypotheses.jsonl \
        --dataset experiments/eval/dataset.json \
        --summary
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_dataset(dataset_path: Path) -> dict[str, dict]:
    """Load dataset.json and index by question id."""
    data = json.loads(dataset_path.read_text())
    return {q["id"]: q for q in data["questions"]}


def load_hypotheses(hypotheses_path: Path) -> list[dict]:
    """Load hypotheses.jsonl — one JSON object per line."""
    hypotheses = []
    for line in hypotheses_path.read_text().splitlines():
        line = line.strip()
        if line:
            hypotheses.append(json.loads(line))
    return hypotheses


def grade_hypothesis(question: dict, hypothesis: str) -> dict:
    """Score a single hypothesis against gold answer using LLM judge.

    Returns {score: 'CORRECT'|'INCORRECT'|'PARTIAL', reasoning: str}.
    """
    import anthropic

    client = anthropic.Anthropic()

    prompt = f"""You are grading a memory system evaluation.

Question: {question['question']}
Gold answer: {question['answer']}
Rubric: {question['rubric']}
System hypothesis: {hypothesis}

Score the hypothesis as CORRECT, PARTIAL, or INCORRECT based on the rubric.
Respond with JSON: {{"score": "CORRECT"|"PARTIAL"|"INCORRECT", "reasoning": "one sentence"}}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:-1])
    return json.loads(text)


def grade_all(
    hypotheses: list[dict],
    dataset: dict[str, dict],
    output_path: Path | None,
) -> list[dict]:
    """Grade all hypotheses and write scores.jsonl."""
    scores = []

    for h in hypotheses:
        qid = h["id"]
        if qid not in dataset:
            print(f"Warning: no question found for id={qid}, skipping", file=sys.stderr)
            continue

        question = dataset[qid]

        # Skip questions with pending gold answers
        if question["answer"] == "PENDING":
            print(f"Skipping {qid} — gold answer not yet authored", file=sys.stderr)
            continue

        print(f"Grading {qid} (day {h['day']})...", file=sys.stderr)
        try:
            result = grade_hypothesis(question, h["hypothesis"])
        except Exception as e:
            result = {"score": "ERROR", "reasoning": str(e)}

        score_record = {
            "id": qid,
            "day": h["day"],
            "question": question["question"],
            "hypothesis": h["hypothesis"],
            "gold_answer": question["answer"],
            "score": result.get("score", "ERROR"),
            "reasoning": result.get("reasoning", ""),
            "timestamp": h.get("timestamp", ""),
        }
        scores.append(score_record)

        if output_path:
            with output_path.open("a") as f:
                f.write(json.dumps(score_record) + "\n")

    return scores


def print_summary(scores: list[dict]) -> None:
    """Print a summary table of scores."""
    if not scores:
        print("No scores to summarize.")
        return

    correct = sum(1 for s in scores if s["score"] == "CORRECT")
    partial = sum(1 for s in scores if s["score"] == "PARTIAL")
    incorrect = sum(1 for s in scores if s["score"] == "INCORRECT")
    total = len(scores)

    print(f"\n{'─' * 60}")
    print(f"Eval Summary: {total} questions graded")
    print(f"  CORRECT:   {correct}/{total} ({100*correct//total if total else 0}%)")
    print(f"  PARTIAL:   {partial}/{total}")
    print(f"  INCORRECT: {incorrect}/{total}")
    print(f"{'─' * 60}")

    for s in scores:
        mark = "✓" if s["score"] == "CORRECT" else ("~" if s["score"] == "PARTIAL" else "✗")
        print(f"  {mark} day {s['day']:3d} | {s['id']}")
        print(f"       Q: {s['question'][:70]}")
        print(f"       A: {s['reasoning']}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade replay sandbox hypotheses")
    parser.add_argument("hypotheses", help="Path to hypotheses.jsonl")
    parser.add_argument(
        "--dataset",
        default="experiments/eval/dataset.json",
        help="Path to dataset.json (default: experiments/eval/dataset.json)",
    )
    parser.add_argument("--output", help="Path to write scores.jsonl (appends)")
    parser.add_argument("--summary", action="store_true", help="Print summary table and exit")
    args = parser.parse_args()

    hypotheses_path = Path(args.hypotheses)
    dataset_path = Path(args.dataset)
    output_path = Path(args.output) if args.output else None

    if not hypotheses_path.exists():
        raise SystemExit(f"hypotheses file not found: {hypotheses_path}")
    if not dataset_path.exists():
        raise SystemExit(f"dataset not found: {dataset_path}")

    dataset = load_dataset(dataset_path)
    hypotheses = load_hypotheses(hypotheses_path)

    if args.summary and not args.output:
        # Just print existing scores if hypotheses already look like scores
        scores = hypotheses  # assume it's a scores.jsonl
        print_summary(scores)
        return

    scores = grade_all(hypotheses, dataset, output_path)
    print_summary(scores)

    if output_path:
        print(f"Scores written to: {output_path}")


if __name__ == "__main__":
    main()
