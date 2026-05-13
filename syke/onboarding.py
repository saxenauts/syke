"""First-run onboarding state shared by setup and the local web UI."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke.config import user_data_dir

ONBOARDING_STATE_FILE = "onboarding.json"


def onboarding_state_path(user_id: str) -> Path:
    return user_data_dir(user_id) / ONBOARDING_STATE_FILE


def write_onboarding_state(
    user_id: str,
    *,
    selected_sources: list[str] | tuple[str, ...],
    total_files: int,
    estimated_minutes: int,
    estimate_method: str,
    mode: str,
    monitor: str | None = None,
    persistence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "waiting_first_synthesis",
        "created_at": now,
        "updated_at": now,
        "selected_sources": list(selected_sources),
        "total_files": int(total_files),
        "estimated_minutes": int(estimated_minutes),
        "estimate_method": estimate_method,
        "mode": mode,
        "monitor": monitor,
        "persistence": persistence or {},
    }
    path = onboarding_state_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.rename(path)
    return payload


def mark_first_synthesis_complete(
    user_id: str,
    *,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    payload = read_onboarding_state(user_id)
    if not payload:
        return None
    if payload.get("status") != "waiting_first_synthesis":
        return payload

    updated = dict(payload)
    updated["status"] = "first_synthesis_completed"
    updated["updated_at"] = datetime.now(UTC).isoformat()
    if trace_id:
        updated["first_synthesis_trace_id"] = trace_id

    path = onboarding_state_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(updated, indent=2), encoding="utf-8")
    tmp.rename(path)
    return updated


def read_onboarding_state(user_id: str) -> dict[str, Any] | None:
    path = onboarding_state_path(user_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload
