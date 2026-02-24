from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = "/tmp/syke_experiment_20260224_200531.db"
DEFAULT_RESULTS_PATH = "experiments/results/replay_20260224_200531.json"
DEFAULT_OUTPUT_PATH = "viz/src/data/synthesis-replay.json"

DEFAULT_PII_MAP: dict[str, str] = {
    "Utkarsh Saxena": "Alex Chen",
    "Utkarsh": "Alex",
    "saxenauts": "alex_chen",
    "InnerNets": "Acme Labs",
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(
    r"(?<![\dT])(?:\+?\d{1,3}[-.\s])?(?:\(?\d{3}\)?[-.\s])\d{3}[-.\s]\d{4}(?!\d)"
)
ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z0-9.'\-\s]+\s(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Court|Ct)\b(?:,\s*[A-Za-z .'-]+)?",
    flags=re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate synthesis replay JSON")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--results-path", default=DEFAULT_RESULTS_PATH)
    parser.add_argument("--output-path", default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--pii-map",
        default="",
        help="Path to JSON file with string replacements map",
    )
    return parser.parse_args()


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def load_pii_map(path: str) -> dict[str, str]:
    pii_map = dict(DEFAULT_PII_MAP)
    if path:
        custom = json.loads(Path(path).read_text())
        if not isinstance(custom, dict):
            raise ValueError("--pii-map must point to a JSON object")
        for key, value in custom.items():
            pii_map[str(key)] = str(value)
    return pii_map


def sanitize_text(text: str, pii_map: dict[str, str]) -> str:
    sanitized = text
    for src, dst in pii_map.items():
        sanitized = sanitized.replace(src, dst)
    sanitized = EMAIL_RE.sub("alex.chen@example.com", sanitized)
    sanitized = PHONE_RE.sub("+1-555-010-0199", sanitized)
    sanitized = ADDRESS_RE.sub("123 Maple Street, Springfield", sanitized)
    return sanitized


def sanitize_obj(obj: Any, pii_map: dict[str, str]) -> Any:
    if isinstance(obj, dict):
        return {k: sanitize_obj(v, pii_map) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_obj(v, pii_map) for v in obj]
    if isinstance(obj, str):
        return sanitize_text(obj, pii_map)
    return obj


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def first_nonempty_line(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            return line
    return ""


def summarize_memory_content(content: str) -> str:
    first = first_nonempty_line(content)
    if not first:
        return ""
    if first.startswith("#"):
        return first.lstrip("# ").strip()
    return first


def op_tool_name(op_type: str) -> str:
    mapping = {
        "add": "create_memory",
        "update": "update_memory",
        "supersede": "supersede_memory",
        "link": "create_link",
        "synthesize": "get_memex",
    }
    return mapping.get(op_type, op_type)


def build_arc(days: list[dict[str, Any]]) -> dict[str, Any]:
    total_days = len(days)
    if total_days == 0:
        return {
            "description": "No replay days available",
            "phases": [],
        }

    labels = [
        ("Bootstrap", "Agent establishes foundational identity and project memories."),
        (
            "Deepening",
            "Agent expands project detail and starts linking related memory units.",
        ),
        (
            "Integration",
            "Agent consolidates structure with supersessions and memex refinement.",
        ),
        (
            "Consolidation",
            "Agent continues incremental updates with lower structural churn.",
        ),
    ]

    phase_count = 4 if total_days >= 8 else 3 if total_days >= 5 else 2
    chunk = (total_days + phase_count - 1) // phase_count
    phases: list[dict[str, Any]] = []
    for idx in range(phase_count):
        start = idx * chunk
        if start >= total_days:
            break
        end = min(total_days, start + chunk)
        day_numbers = [d["day_number"] for d in days[start:end]]
        label, description = labels[min(idx, len(labels) - 1)]
        phases.append({"days": day_numbers, "label": label, "description": description})

    return {
        "description": f"{total_days}-day memory replay showing day-by-day synthesis evolution.",
        "phases": phases,
    }


def main() -> None:
    args = parse_args()
    pii_map = load_pii_map(args.pii_map)
    results = load_json(args.results_path)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row

    memory_rows = conn.execute(
        "SELECT id, content, created_at FROM memories ORDER BY created_at"
    ).fetchall()
    memories_by_id = {row["id"]: dict(row) for row in memory_rows}

    memex_rows = [
        dict(row)
        for row in conn.execute(
            "SELECT id, content, created_at FROM memories WHERE content LIKE '# Memex%' ORDER BY created_at"
        ).fetchall()
    ]
    memex_by_id = {row["id"]: row for row in memex_rows}

    op_rows = [
        dict(row)
        for row in conn.execute(
            "SELECT id, operation, input_summary, output_summary, memory_ids, created_at FROM memory_ops ORDER BY created_at"
        ).fetchall()
    ]

    synth_cost_rows = [
        row
        for row in op_rows
        if row["operation"] == "synthesize"
        and "cost=$" in (row.get("output_summary") or "")
    ]

    runs = results.get("synthesis_runs", [])
    if len(synth_cost_rows) < len(runs):
        raise RuntimeError(
            f"Not enough synth runs in DB ({len(synth_cost_rows)}) for results days ({len(runs)})"
        )

    days: list[dict[str, Any]] = []
    prev_end_dt: datetime | None = None

    for idx, run in enumerate(runs):
        day_number = idx + 1
        day_end_dt = parse_iso(synth_cost_rows[idx]["created_at"])

        window_ops = []
        for op in op_rows:
            created_dt = parse_iso(op["created_at"])
            if created_dt <= day_end_dt and (
                prev_end_dt is None or created_dt > prev_end_dt
            ):
                window_ops.append(op)

        memories_added: list[str] = []
        memories_updated: list[str] = []
        memories_superseded: list[str] = []
        links_created: list[str] = []

        operations: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []

        memex_snapshot: str | None = None

        for op in window_ops:
            op_type = op["operation"]
            op_memory_ids: list[str] = []
            try:
                parsed_ids = json.loads(op.get("memory_ids") or "[]")
                if isinstance(parsed_ids, list):
                    op_memory_ids = [str(x) for x in parsed_ids]
            except Exception:
                op_memory_ids = []

            summary = first_nonempty_line(op.get("input_summary") or "")
            if not summary:
                summary = first_nonempty_line(op.get("output_summary") or "")

            op_record: dict[str, Any] = {
                "type": op_type,
                "summary": summary,
                "timestamp": op["created_at"],
            }

            if op_type == "add":
                memory_id = op_memory_ids[0] if op_memory_ids else None
                if memory_id:
                    op_record["memory_id"] = memory_id
                    memory = memories_by_id.get(memory_id)
                    if memory:
                        memories_added.append(
                            summarize_memory_content(memory.get("content", ""))
                        )
            elif op_type == "update":
                memory_id = op_memory_ids[0] if op_memory_ids else None
                if memory_id:
                    op_record["memory_id"] = memory_id
                    memory = memories_by_id.get(memory_id)
                    if memory:
                        memories_updated.append(
                            summarize_memory_content(memory.get("content", ""))
                        )
            elif op_type == "supersede":
                old_id = op_memory_ids[0] if len(op_memory_ids) >= 1 else None
                new_id = op_memory_ids[1] if len(op_memory_ids) >= 2 else None
                if old_id:
                    op_record["from_memory_id"] = old_id
                if new_id:
                    op_record["to_memory_id"] = new_id
                if new_id:
                    new_memory = memories_by_id.get(new_id)
                    if new_memory:
                        memories_superseded.append(
                            summarize_memory_content(new_memory.get("content", ""))
                        )
            elif op_type == "link":
                if len(op_memory_ids) >= 2:
                    op_record["source_memory_id"] = op_memory_ids[0]
                    op_record["target_memory_id"] = op_memory_ids[1]
                links_created.append(summary)
            elif op_type == "synthesize" and "new memex" in (
                op.get("output_summary") or ""
            ):
                memex_id = op_memory_ids[0] if op_memory_ids else None
                if memex_id:
                    op_record["memex_id"] = memex_id
                    memex = memex_by_id.get(memex_id)
                    if memex:
                        memex_snapshot = memex.get("content", "")

            operations.append(op_record)

            if op_type in {"add", "update", "supersede", "link", "synthesize"}:
                tool_calls.append(
                    {
                        "tool_name": op_tool_name(op_type),
                        "at": op["created_at"],
                    }
                )

        delta = {
            "memories_before": run.get("memories_before", 0),
            "memories_after": run.get("memories_after", 0),
            "memories_added": memories_added,
            "memories_updated": memories_updated,
            "memories_superseded": memories_superseded,
            "links_created": links_created,
            "memex_updated": bool(run.get("memex_updated", False)),
            "memex_length_chars": run.get("memex_length_chars", 0),
        }

        days.append(
            {
                "day": run["day"],
                "day_number": day_number,
                "events_ingested": run.get("events_total", 0),
                "events_by_source": run.get("events_by_source", {}),
                "cost_usd": round(float(run.get("cost_usd", 0.0)), 6),
                "duration_s": run.get("duration_s", 0),
                "reasoning_trace": {
                    "core_tools": ["get_memex", "search_evidence"],
                    "tool_calls": tool_calls,
                    "notes": "Write-side calls inferred from memory_ops; read-side tools reflect synthesis loop defaults.",
                },
                "delta": delta,
                "operations": operations,
                "memex_snapshot": memex_snapshot,
            }
        )

        prev_end_dt = day_end_dt

    total_links = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    conn.close()

    total_cost = float(results.get("totals", {}).get("synthesis_cost", 0.0))
    total_memories = runs[-1].get("memories_after", 0) if runs else 0

    replay = {
        "experiment_id": results.get("experiment_id", ""),
        "total_days": len(days),
        "total_memories": total_memories,
        "total_links": total_links,
        "memex_versions": len(memex_rows),
        "total_cost_usd": round(total_cost, 4),
        "days": days,
        "arc": build_arc(days),
    }

    replay = sanitize_obj(replay, pii_map)

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(replay, indent=2) + "\n")

    print(f"Wrote synthesis replay: {output_path}")


if __name__ == "__main__":
    main()
