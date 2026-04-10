"""Canonical rollout trace persistence in syke.db."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from syke.config import user_syke_db_path
from syke.db import SykeDB


def trace_store_status(user_id: str) -> dict[str, object]:
    path = user_syke_db_path(user_id)
    probe_dir = path.parent if path.parent.exists() else path.parent.parent
    writable = probe_dir.exists() and os.access(probe_dir, os.W_OK)
    detail = (
        f"Rollout trace store writable in {path}"
        if writable
        else f"Rollout trace store not writable in {path}"
    )
    return {"ok": writable, "path": str(path), "detail": detail}


@dataclass
class TraceRecord:
    """Normalized per-rollout trace artifact."""

    version: int
    run_id: str
    kind: str
    user_id: str
    started_at: str
    completed_at: str
    status: str
    error: str | None
    input_text: str | None
    output_text: str
    thinking: list[str] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


def persist_rollout_trace(
    db: SykeDB,
    *,
    user_id: str,
    run_id: str,
    kind: str,
    started_at: datetime,
    completed_at: datetime,
    status: str,
    error: str | None = None,
    input_text: str | None = None,
    output_text: str = "",
    thinking: list[str] | None = None,
    transcript: list[dict[str, Any]] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    metrics: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    extras: dict[str, Any] | None = None,
) -> str:
    """Persist a canonical rollout trace row and return its run id."""
    record = TraceRecord(
        version=1,
        run_id=run_id,
        kind=kind,
        user_id=user_id,
        started_at=started_at.astimezone(UTC).isoformat(),
        completed_at=completed_at.astimezone(UTC).isoformat(),
        status=status,
        error=error,
        input_text=input_text,
        output_text=output_text,
        thinking=list(thinking or []),
        transcript=list(transcript or []),
        tool_calls=list(tool_calls or []),
        metrics=dict(metrics or {}),
        runtime=dict(runtime or {}),
        extras=dict(extras or {}),
    )
    db.insert_rollout_trace(
        trace_id=record.run_id,
        user_id=record.user_id,
        kind=record.kind,
        started_at=record.started_at,
        completed_at=record.completed_at,
        status=record.status,
        error=record.error,
        input_text=record.input_text,
        output_text=record.output_text,
        thinking=record.thinking,
        transcript=record.transcript,
        tool_calls=record.tool_calls,
        duration_ms=int(record.metrics.get("duration_ms") or 0),
        cost_usd=float(record.metrics.get("cost_usd") or 0.0),
        input_tokens=int(record.metrics.get("input_tokens") or 0),
        output_tokens=int(record.metrics.get("output_tokens") or 0),
        cache_read_tokens=int(record.metrics.get("cache_read_tokens") or 0),
        cache_write_tokens=int(record.metrics.get("cache_write_tokens") or 0),
        num_turns=int(record.runtime.get("num_turns") or 0),
        tool_calls_count=len(record.tool_calls),
        tool_name_counts={
            name: sum(
                1
                for call in record.tool_calls
                if str(call.get("name") or call.get("tool") or "tool") == name
            )
            for name in {
                str(call.get("name") or call.get("tool") or "tool") for call in record.tool_calls
            }
        },
        provider=record.runtime.get("provider"),
        model=record.runtime.get("model"),
        response_id=record.runtime.get("response_id"),
        stop_reason=record.runtime.get("stop_reason"),
        transport=record.runtime.get("transport"),
        runtime_reused=record.runtime.get("runtime_reused"),
        runtime=record.runtime,
        extras={"version": 1, **record.extras},
    )
    return record.run_id


def load_rollout_traces(
    db: SykeDB,
    user_id: str,
    *,
    kind: str | None = None,
    limit: int | None = 50,
) -> list[dict[str, Any]]:
    """Load normalized rollout traces from syke.db."""
    rows = db.get_rollout_traces(user_id, kind=kind, limit=limit or 1000000)
    decoded: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key, default in (
            ("thinking", []),
            ("transcript", []),
            ("tool_calls", []),
            ("tool_name_counts", {}),
            ("runtime", {}),
            ("extras", {}),
        ):
            raw = item.get(key)
            try:
                item[key] = json.loads(raw) if isinstance(raw, str) and raw else default
            except (json.JSONDecodeError, TypeError):
                item[key] = default
        item["metrics"] = {
            "duration_ms": int(item.get("duration_ms") or 0),
            "cost_usd": float(item.get("cost_usd") or 0.0),
            "input_tokens": int(item.get("input_tokens") or 0),
            "output_tokens": int(item.get("output_tokens") or 0),
            "cache_read_tokens": int(item.get("cache_read_tokens") or 0),
            "cache_write_tokens": int(item.get("cache_write_tokens") or 0),
        }
        decoded.append(item)
    return decoded
