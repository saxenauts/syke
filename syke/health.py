"""Observe — the system watching itself.

Reads from SQLite + metrics.jsonl, returns structured dicts with raw numbers
and qualitative assessments. One format, both audiences (human + agent).
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

from syke.config import user_data_dir

STALENESS_HALF_LIFE_DAYS = 30


def _hours_ago(iso_timestamp: str | None) -> float | None:
    if not iso_timestamp:
        return None
    try:
        then = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return round((now - then).total_seconds() / 3600, 1)
    except (ValueError, TypeError):
        return None


def _human_ago(hours: float | None) -> str:
    if hours is None:
        return "never"
    if hours < 1:
        mins = int(hours * 60)
        return f"{mins}m ago" if mins > 0 else "just now"
    if hours < 24:
        return f"{hours:.0f}h ago"
    days = hours / 24
    if days < 7:
        return f"{days:.0f}d ago"
    weeks = days / 7
    return f"{weeks:.0f}w ago"


def _staleness_score(age_days: float) -> float:
    return 1 - math.exp(-math.log(2) * age_days / STALENESS_HALF_LIFE_DAYS)


def _assess_staleness(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 2:
        return "fresh"
    if hours < 12:
        return "healthy"
    if hours < 24:
        return "ok"
    if hours < 48:
        return "stale"
    return "dead"


def memory_health(db, user_id: str) -> dict:
    stats = db.get_graph_stats(user_id)
    orphan_pct = round(stats["orphan_rate"] * 100, 1)

    if stats["active"] == 0:
        assessment = "empty"
    elif stats["orphan_rate"] > 0.5:
        assessment = "fragmented"
    elif stats["density"] < 0.1:
        assessment = "sparse"
    elif stats["density"] > 2.0:
        assessment = "dense"
    else:
        assessment = "healthy"

    return {**stats, "orphan_pct": orphan_pct, "assessment": assessment}


def _parse_iso_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _recent_cycle_records(db, user_id: str, *, limit: int = 20) -> list[dict]:
    try:
        rows = db.get_cycle_records(user_id, limit=limit)
    except Exception:
        return []
    return [
        row for row in rows if isinstance(row, dict) and row.get("status") not in ("running", None)
    ]


def _cycle_rollup(db, user_id: str) -> dict[str, float | int]:
    empty = {
        "total_runs": 0,
        "completed_runs": 0,
        "failed_runs": 0,
        "incomplete_runs": 0,
        "events_processed": 0,
        "total_cost_usd": 0.0,
    }
    try:
        row = db.conn.execute(
            """
            SELECT
                COALESCE(
                    SUM(CASE WHEN status != 'running' THEN 1 ELSE 0 END),
                    0
                ) AS total_runs,
                COALESCE(
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END),
                    0
                ) AS completed_runs,
                COALESCE(
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END),
                    0
                ) AS failed_runs,
                COALESCE(
                    SUM(CASE WHEN status = 'incomplete' THEN 1 ELSE 0 END),
                    0
                ) AS incomplete_runs,
                COALESCE(
                    SUM(CASE WHEN status != 'running' THEN events_processed ELSE 0 END),
                    0
                ) AS events_processed,
                COALESCE(
                    SUM(CASE WHEN status != 'running' THEN cost_usd ELSE 0 END),
                    0
                ) AS total_cost_usd
            FROM cycle_records
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    except Exception:
        return empty
    if row is None:
        return empty
    return {
        "total_runs": int(row["total_runs"] or 0),
        "completed_runs": int(row["completed_runs"] or 0),
        "failed_runs": int(row["failed_runs"] or 0),
        "incomplete_runs": int(row["incomplete_runs"] or 0),
        "events_processed": int(row["events_processed"] or 0),
        "total_cost_usd": round(float(row["total_cost_usd"] or 0.0), 4),
    }


def synthesis_health(db, user_id: str, metrics_dir: Path | None = None) -> dict:
    cycles = _recent_cycle_records(db, user_id, limit=5)
    cycle_rollup = _cycle_rollup(db, user_id)

    if cycles:
        last_run = cycles[0]
        last_ts = last_run.get("completed_at") or last_run.get("started_at")
        recent_costs = [float(c.get("cost_usd", 0) or 0) for c in cycles if c.get("cost_usd")]
        avg_cost = round(sum(recent_costs) / len(recent_costs), 4) if recent_costs else 0
        total_cost = float(cycle_rollup["total_cost_usd"])
        events_processed = int(last_run.get("events_processed") or 0)
        created = int(last_run.get("memories_created") or 0)
        superseded = int(last_run.get("memories_updated") or 0)
        linked = int(last_run.get("links_created") or 0)
        deactivated = 0
        memex_updated = bool(last_run.get("memex_updated"))
        duration_ms = int(last_run.get("duration_ms") or 0)
        cost_usd = round(float(last_run.get("cost_usd") or 0), 4)
        recent_runs = len(cycles)
        last_status = str(last_run.get("status") or "unknown")
    else:
        stats = db.get_synthesis_stats(user_id, limit=5)
        last_run = stats[0] if stats else {}
        last_ts = db.get_last_synthesis_timestamp(user_id)
        recent_costs = [float(s.get("cost_usd", 0) or 0) for s in stats if s.get("cost_usd")]
        avg_cost = round(sum(recent_costs) / len(recent_costs), 4) if recent_costs else 0
        total_cost = _total_cost_from_metrics(
            metrics_dir or user_data_dir(user_id), operations={"synthesis"}
        )
        events_processed = int(last_run.get("events_processed", 0) or 0)
        created = int(last_run.get("created", 0) or 0)
        superseded = int(last_run.get("superseded", 0) or 0)
        linked = int(last_run.get("linked", 0) or 0)
        deactivated = int(last_run.get("deactivated", 0) or 0)
        memex_updated = bool(last_run.get("memex_updated", False))
        duration_ms = last_run.get("duration_ms")
        cost_usd = round(float(last_run.get("cost_usd", 0) or 0), 4)
        recent_runs = len(stats)
        last_status = str(last_run.get("status") or "unknown") if last_run else "unknown"

    hours = _hours_ago(last_ts if isinstance(last_ts, str) else None)
    if hours is None:
        assessment = "never_run"
    elif last_status in {"failed", "incomplete"} and hours < 24:
        assessment = "degraded"
    elif hours < 1:
        assessment = "active"
    elif hours < 6:
        assessment = "recent"
    elif hours < 24:
        assessment = "idle"
    else:
        assessment = "stale"

    return {
        "last_run_iso": last_ts,
        "last_run_ago": _human_ago(hours),
        "last_run_hours": hours,
        "last_status": last_status,
        "events_processed": events_processed,
        "created": created,
        "superseded": superseded,
        "linked": linked,
        "deactivated": deactivated,
        "memex_updated": memex_updated,
        "duration_ms": duration_ms,
        "cost_usd": cost_usd,
        "avg_cost_usd": avg_cost,
        "total_cost_usd": round(float(total_cost), 4),
        "recent_runs": recent_runs,
        "completed_runs": int(cycle_rollup["completed_runs"]),
        "failed_runs": int(cycle_rollup["failed_runs"]),
        "incomplete_runs": int(cycle_rollup["incomplete_runs"]),
        "assessment": assessment,
    }


def ingestion_health(db, user_id: str) -> dict:
    staleness = db.get_ingestion_staleness(user_id)
    total = sum(s["count"] for s in staleness)

    sources = []
    for s in staleness:
        hours = _hours_ago(s["last_sync"])
        sources.append(
            {
                "name": s["source"],
                "count": s["count"],
                "last_sync_ago": _human_ago(hours),
                "last_sync_hours": hours,
                "status": _assess_staleness(hours),
            }
        )

    return {"total": total, "sources": sources}


def evolution_trends(db, user_id: str, days: int = 7) -> dict:
    trends = db.get_memory_trends(user_id, days)

    if trends["created"] == 0 and trends["superseded"] == 0:
        assessment = "dormant"
    elif trends["superseded"] > trends["created"]:
        assessment = "consolidating"
    elif trends["net"] > 0 and trends["links_created"] > 0:
        assessment = "growing"
    elif trends["net"] > 0:
        assessment = "accumulating"
    else:
        assessment = "stable"

    supersession_rate = (
        round(trends["superseded"] / trends["created"], 2) if trends["created"] > 0 else 0
    )

    return {**trends, "supersession_rate": supersession_rate, "assessment": assessment}


def signals(db, user_id: str) -> list[dict]:
    from syke.daemon.daemon import is_running
    from syke.daemon.ipc import daemon_ipc_status
    from syke.metrics import runtime_metrics_status
    from syke.observe.trace import self_observation_status

    result = []

    orphans = db.get_orphan_memories(user_id, limit=3)
    for o in orphans:
        age_hours = _hours_ago(o["created_at"])
        age_str = _human_ago(age_hours)
        preview = o["preview"].strip().split("\n")[0][:50]
        result.append(
            {
                "type": "decay_candidate",
                "detail": f'"{preview}" \u2014 {age_str}, 0 links',
            }
        )

    staleness = db.get_ingestion_staleness(user_id)
    for s in staleness:
        hours = _hours_ago(s["last_sync"])
        if hours and hours > 24:
            sync_age = _human_ago(hours).replace(" ago", "")
            result.append(
                {
                    "type": "stale_source",
                    "detail": f"{s['source']} hasn't synced in {sync_age}",
                }
            )

    memex = db.get_memex(user_id)
    if memex:
        memex_hours = _hours_ago(memex.get("created_at"))
        if memex_hours and memex_hours > 24:
            result.append(
                {
                    "type": "stale_memex",
                    "detail": f"memex last updated {_human_ago(memex_hours)}",
                }
            )

    self_obs = self_observation_status()
    if not bool(self_obs["enabled"]):
        result.append(
            {
                "type": "self_observation_disabled",
                "detail": str(self_obs["detail"]),
            }
        )

    visibility = runtime_metrics_status(user_id)
    file_logging = visibility["file_logging"]
    if not bool(file_logging["ok"]):
        result.append(
            {
                "type": "file_logging_disabled",
                "detail": str(file_logging["detail"]),
            }
        )
    metrics_store = visibility["metrics_store"]
    if not bool(metrics_store["ok"]):
        result.append(
            {
                "type": "metrics_persist_disabled",
                "detail": str(metrics_store["detail"]),
            }
        )

    daemon_running, _ = is_running()
    daemon_ipc = daemon_ipc_status(user_id)
    if daemon_running and not bool(daemon_ipc["ok"]):
        result.append(
            {
                "type": "daemon_ipc_unavailable",
                "detail": f"daemon IPC unavailable: {daemon_ipc['detail']}",
            }
        )

    return result


def memex_health(db, user_id: str) -> dict:
    memex = db.get_memex(user_id)
    if not memex:
        return {
            "exists": False,
            "lines": 0,
            "updated_ago": "never",
            "assessment": "missing",
        }

    content = memex.get("content", "")
    lines = len(content.strip().split("\n")) if content else 0
    hours = _hours_ago(memex.get("created_at"))

    active_count = db.count_memories(user_id, active_only=True)

    return {
        "exists": True,
        "lines": lines,
        "chars": len(content),
        "updated_ago": _human_ago(hours),
        "updated_hours": hours,
        "active_memories": active_count,
        "assessment": _assess_staleness(hours),
    }


def full_observe(db, user_id: str) -> dict:
    return {
        "user_id": user_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "memory": memory_health(db, user_id),
        "synthesis": synthesis_health(db, user_id),
        "runtime": runtime_health(db, user_id),
        "ingestion": ingestion_health(db, user_id),
        "memex": memex_health(db, user_id),
        "evolution": evolution_trends(db, user_id),
        "signals": signals(db, user_id),
    }


def _load_metrics_entries(data_dir: Path) -> list[dict]:
    metrics_file = data_dir / "metrics.jsonl"
    if not metrics_file.exists():
        return []

    entries: list[dict] = []
    for line in metrics_file.read_text().splitlines():
        if line.strip():
            try:
                entry = json.loads(line)
                if isinstance(entry, dict):
                    entries.append(entry)
            except (json.JSONDecodeError, TypeError):
                continue
    return entries


def runtime_health(db, user_id: str, metrics_dir: Path | None = None) -> dict:
    from syke.daemon.daemon import is_running
    from syke.daemon.ipc import daemon_ipc_status
    from syke.metrics import runtime_metrics_status
    from syke.observe.trace import self_observation_status

    data_dir = metrics_dir or user_data_dir(user_id)
    entries = _load_metrics_entries(data_dir)
    runtime_entries = [entry for entry in entries if entry.get("operation") in {"ask", "synthesis"}]
    ask_entries = [entry for entry in runtime_entries if entry.get("operation") == "ask"]
    synthesis_entries = [
        entry for entry in runtime_entries if entry.get("operation") == "synthesis"
    ]
    cycle_rollup = _cycle_rollup(db, user_id)
    cycle_runs = _recent_cycle_records(db, user_id, limit=20)
    cycle_failed_runs = int(cycle_rollup["failed_runs"]) + int(cycle_rollup["incomplete_runs"])

    total_tool_calls = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    warm_reuse_runs = 0
    cold_start_runs = 0
    failures = 0
    daemon_ipc_runs = 0
    direct_runs = 0
    ipc_fallbacks = 0
    tool_name_counts: dict[str, int] = {}

    for entry in runtime_entries:
        details = entry.get("details", {})
        if not isinstance(details, dict):
            continue

        total_tool_calls += int(details.get("tool_calls", 0) or 0)
        cache_read_tokens += int(details.get("cache_read_tokens", 0) or 0)
        cache_write_tokens += int(details.get("cache_write_tokens", 0) or 0)
        if details.get("runtime_reused") is True:
            warm_reuse_runs += 1
        elif details.get("runtime_reused") is False:
            cold_start_runs += 1
        if details.get("status") == "failed" or not entry.get("success", True):
            failures += 1
        transport = details.get("transport")
        if transport == "daemon_ipc":
            daemon_ipc_runs += 1
        elif transport == "direct":
            direct_runs += 1
        if details.get("ipc_fallback") is True:
            ipc_fallbacks += 1

        raw_counts = details.get("tool_name_counts", {})
        if isinstance(raw_counts, dict):
            for name, count in raw_counts.items():
                if isinstance(name, str):
                    tool_name_counts[name] = tool_name_counts.get(name, 0) + int(count or 0)

    def _avg_duration_ms(rows: list[dict]) -> int | None:
        durations = [
            int(row.get("duration_api_ms", 0) or 0) for row in rows if row.get("duration_api_ms")
        ]
        if not durations:
            return None
        return int(sum(durations) / len(durations))

    last_entry = runtime_entries[-1] if runtime_entries else None
    last_details = last_entry.get("details", {}) if isinstance(last_entry, dict) else {}
    metric_ts = None
    if isinstance(last_entry, dict):
        metric_ts = last_entry.get("completed_at") or last_entry.get("started_at")
    cycle_last = cycle_runs[0] if cycle_runs else None
    cycle_ts = (
        cycle_last.get("completed_at") or cycle_last.get("started_at") if cycle_last else None
    )
    metric_dt = _parse_iso_timestamp(metric_ts)
    cycle_dt = _parse_iso_timestamp(cycle_ts)
    use_cycle_as_last = bool(cycle_dt and (metric_dt is None or cycle_dt > metric_dt))
    last_ts = cycle_ts if use_cycle_as_last else metric_ts
    hours = _hours_ago(last_ts if isinstance(last_ts, str) else None)

    self_obs = self_observation_status()
    visibility = runtime_metrics_status(user_id)
    daemon_running, _ = is_running()
    daemon_ipc = daemon_ipc_status(user_id)
    top_tools = sorted(tool_name_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    synthesis_runs = int(cycle_rollup["total_runs"]) or len(synthesis_entries)

    if not runtime_entries and synthesis_runs == 0:
        assessment = "no_telemetry"
    elif failures > 0 or cycle_failed_runs > 0:
        assessment = "degraded"
    elif runtime_entries and cold_start_runs > warm_reuse_runs:
        assessment = "cold"
    elif not runtime_entries:
        assessment = "cycle_only"
    else:
        assessment = "warm"

    return {
        "recent_runs": len(runtime_entries),
        "ask_runs": len(ask_entries),
        "synthesis_runs": synthesis_runs,
        "cycle_completed_runs": int(cycle_rollup["completed_runs"]),
        "cycle_failed_runs": int(cycle_rollup["failed_runs"]),
        "cycle_incomplete_runs": int(cycle_rollup["incomplete_runs"]),
        "cycle_events_processed": int(cycle_rollup["events_processed"]),
        "cycle_total_cost_usd": float(cycle_rollup["total_cost_usd"]),
        "last_synthesis_status": cycle_last.get("status") if cycle_last else None,
        "last_run_ago": _human_ago(hours),
        "last_run_hours": hours,
        "last_operation": (
            "synthesis_cycle"
            if use_cycle_as_last
            else last_entry.get("operation")
            if isinstance(last_entry, dict)
            else None
        ),
        "last_provider": last_details.get("provider") if isinstance(last_details, dict) else None,
        "last_model": last_details.get("model") if isinstance(last_details, dict) else None,
        "last_response_id": last_details.get("response_id")
        if isinstance(last_details, dict)
        else None,
        "avg_ask_ms": _avg_duration_ms(ask_entries),
        "avg_synthesis_ms": _avg_duration_ms(synthesis_entries),
        "total_tool_calls": total_tool_calls,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "warm_reuse_runs": warm_reuse_runs,
        "cold_start_runs": cold_start_runs,
        "daemon_ipc_runs": daemon_ipc_runs,
        "direct_runs": direct_runs,
        "ipc_fallbacks": ipc_fallbacks,
        "failures": failures + cycle_failed_runs,
        "top_tools": top_tools,
        "session_count": 0,
        "scripts_count": 0,
        "self_observation_enabled": bool(self_obs["enabled"]),
        "self_observation_detail": self_obs["detail"],
        "file_logging_enabled": bool(visibility["file_logging"]["ok"]),
        "file_logging_error": visibility["file_logging"]["detail"],
        "metrics_persist_enabled": bool(visibility["metrics_store"]["ok"]),
        "metrics_persist_error": visibility["metrics_store"]["detail"],
        "daemon_running": daemon_running,
        "daemon_ipc_available": bool(daemon_ipc["ok"]),
        "daemon_ipc_detail": daemon_ipc["detail"],
        "assessment": assessment,
    }


def _total_cost_from_metrics(data_dir: Path, operations: set[str] | None = None) -> float:
    total = 0.0
    for entry in _load_metrics_entries(data_dir):
        if operations and entry.get("operation") not in operations:
            continue
        total += float(entry.get("cost_usd", 0) or 0)
    return round(total, 4)


def format_observe(data: dict) -> str:
    lines: list[str] = []
    mem = data["memory"]
    syn = data["synthesis"]
    rt = data["runtime"]
    ing = data["ingestion"]
    mx = data["memex"]
    evo = data["evolution"]
    sigs = data["signals"]

    lines.append(f"Syke \u2014 {data['user_id']}")
    lines.append("")

    lines.append("## Memory")
    lines.append(
        f"{mem['active']} active memories, {mem['retired']} retired. "
        f"{mem['links']} links across {mem['active']} nodes "
        f"({mem['density']} connections/memory"
        f"{', ' + mem['assessment'] if mem['assessment'] != 'healthy' else ''})."
    )
    if mem["hubs"]:
        hub_strs = [f'"{h["preview"]}" ({h["links"]})' for h in mem["hubs"][:3]]
        lines.append(f"Densest hubs: {', '.join(hub_strs)}.")
    if mem["supersession_max_depth"] > 0:
        lines.append(
            f"Supersession depth: avg {mem['supersession_avg_depth']}, "
            f"max {mem['supersession_max_depth']} "
            f"({mem['chains_with_history']} memories have evolved)."
        )
    if mem["orphan_count"] > 0:
        lines.append(f"{mem['orphan_count']} orphaned ({mem['orphan_pct']}% unlinked).")
    lines.append("")

    lines.append("## Synthesis")
    if syn["assessment"] == "never_run":
        lines.append("Synthesis has never run.")
    else:
        parts = [f"Last run {syn['last_run_ago']}"]
        if syn["events_processed"]:
            parts.append(f"{syn['events_processed']} events")
        outcomes = []
        if syn["created"]:
            outcomes.append(f"{syn['created']} created")
        if syn["superseded"]:
            outcomes.append(f"{syn['superseded']} superseded")
        if syn["linked"]:
            outcomes.append(f"{syn['linked']} linked")
        if syn["deactivated"]:
            outcomes.append(f"{syn['deactivated']} deactivated")
        if outcomes:
            parts.append(", ".join(outcomes))
        if syn["duration_ms"]:
            parts.append(f"{syn['duration_ms'] / 1000:.0f}s")
        if syn["cost_usd"]:
            parts.append(f"${syn['cost_usd']:.2f}")
        if syn["memex_updated"]:
            parts.append("memex updated")
        lines.append(". ".join(parts) + ".")
    if syn["total_cost_usd"] > 0:
        lines.append(f"Lifetime cost: ${syn['total_cost_usd']:.2f}.")
    lines.append("")

    lines.append("## Runtime")
    if rt["recent_runs"] == 0:
        lines.append("No Pi runtime telemetry yet.")
    else:
        parts = [f"Last run {rt['last_run_ago']}"]
        if rt["last_operation"]:
            parts.append(str(rt["last_operation"]))
        if rt["last_provider"] and rt["last_model"]:
            parts.append(f"{rt['last_provider']} / {rt['last_model']}")
        if rt["avg_ask_ms"]:
            parts.append(f"ask avg {rt['avg_ask_ms'] / 1000:.1f}s")
        if rt["avg_synthesis_ms"]:
            parts.append(f"synthesis avg {rt['avg_synthesis_ms'] / 1000:.1f}s")
        lines.append(". ".join(parts) + ".")
        lines.append(
            f"{rt['total_tool_calls']} tool calls. Cache read {rt['cache_read_tokens']}, "
            f"cache write {rt['cache_write_tokens']}."
        )
        lines.append(
            f"Warm reuse {rt['warm_reuse_runs']}, cold starts {rt['cold_start_runs']}."
        )
        lines.append(
            f"Daemon IPC asks {rt['daemon_ipc_runs']}, direct asks {rt['direct_runs']}, "
            f"IPC fallbacks {rt['ipc_fallbacks']}."
        )
        lines.append(
            f"Workspace sessions {rt['session_count']}, scripts {rt['scripts_count']}."
        )
        if rt["top_tools"]:
            tools = ", ".join(f"{name} ({count})" for name, count in rt["top_tools"])
            lines.append(f"Top tools: {tools}.")
    lines.append("")

    lines.append("## The Map")
    if not mx["exists"]:
        lines.append("No memex yet.")
    else:
        lines.append(f"{mx['lines']} lines, {mx['chars']} chars. Last updated {mx['updated_ago']}.")
        if mx["active_memories"]:
            lines.append(f"{mx['active_memories']} active memories backing the map.")
    lines.append("")

    lines.append("## Ingestion")
    lines.append(f"{ing['total']} events across {len(ing['sources'])} sources.")
    for s in ing["sources"]:
        status_str = f"  {s['status']}" if s["status"] not in ("healthy", "fresh") else ""
        lines.append(f"  {s['name']:<14} {s['count']:>5}  {s['last_sync_ago']:<10}{status_str}")
    lines.append("")

    lines.append(f"## Evolution ({evo['days']}d)")
    lines.append(
        f"+{evo['created']} created, "
        f"-{evo['superseded']} superseded, "
        f"-{evo['deactivated']} deactivated. "
        f"Net {'+' if evo['net'] >= 0 else ''}{evo['net']}."
    )
    if evo["links_per_day"] > 0:
        lines.append(f"Links: {evo['links_per_day']}/day.")
    if evo["supersession_rate"] > 0:
        lines.append(f"Supersession rate: {evo['supersession_rate']:.0%} ({evo['assessment']}).")
    elif evo["assessment"] != "dormant":
        lines.append(f"Graph is {evo['assessment']}.")
    lines.append("")

    if sigs:
        lines.append("## Signals")
        for s in sigs:
            lines.append(f"  {s['detail']}")
        lines.append("")

    return "\n".join(lines)
