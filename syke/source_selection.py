"""Persist and resolve the selected source contract for setup/sync."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from syke.config import user_data_dir
from syke.observe.catalog import get_source

SOURCE_SELECTION_FILE = "source_selection.json"


def _selection_path(user_id: str) -> Path:
    return user_data_dir(user_id) / SOURCE_SELECTION_FILE


def _normalize_sources(sources: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for source in sources:
        source_id = str(source).strip()
        if not source_id or source_id in seen:
            continue
        if get_source(source_id) is None:
            raise ValueError(f"Unknown source: {source_id}")
        seen.add(source_id)
        normalized.append(source_id)
    return normalized


def set_selected_sources(user_id: str, sources: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    selected = _normalize_sources(sources)
    payload = {
        "schema_version": 1,
        "selected_sources": selected,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    path = _selection_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.rename(path)
    return tuple(selected)


def get_selected_sources(user_id: str) -> tuple[str, ...] | None:
    """Return persisted selected sources.

    Returns:
    - None when no persisted selection exists (treat as unrestricted/all)
    - tuple (possibly empty) when a selection has been explicitly saved
    """
    path = _selection_path(user_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = payload.get("selected_sources")
    if not isinstance(raw, list):
        return None
    try:
        return tuple(_normalize_sources(raw))
    except ValueError:
        return None
