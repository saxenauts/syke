"""Helpers for memory IDs mentioned by synthesis traces and ops."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

MEMEX_TOUCH_IDS = {"MEMEX.md", "__memex__"}
_UUIDISH_RE = re.compile(r"^[0-9a-fA-F]{8,}(?:-[0-9a-fA-F]{4,}){0,4}$")


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def looks_like_memory_id(value: str) -> bool:
    if value in MEMEX_TOUCH_IDS:
        return True
    if value.startswith("memex_"):
        return True
    return value.startswith("mem_") or bool(_UUIDISH_RE.match(value))


def trace_memory_ids(text: str | None) -> list[str]:
    """Extract backtick-quoted memory IDs from retained synthesis output."""
    if not text:
        return []
    ids: list[str] = []
    for token in re.findall(r"`([^`]+)`", text):
        token = token.strip()
        if looks_like_memory_id(token):
            ids.append(token)
    return ordered_unique(ids)


def non_memex_memory_ids(ids: list[str]) -> list[str]:
    return [
        memory_id
        for memory_id in ids
        if memory_id not in MEMEX_TOUCH_IDS and not memory_id.startswith("memex_")
    ]


def json_memory_ids(raw: Any) -> list[str]:
    if isinstance(raw, list):
        parsed = raw
    else:
        try:
            parsed = json.loads(raw or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str) and item]


def exclude_memex_memory_rows(
    conn: sqlite3.Connection,
    user_id: str,
    ids: list[str],
) -> list[str]:
    """Remove physical MEMEX row IDs from a touch-ID list."""
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""SELECT id, source_event_ids
            FROM memories
            WHERE user_id = ? AND id IN ({placeholders})""",
        (user_id, *ids),
    ).fetchall()
    memex_row_ids = {
        row["id"] for row in rows if "__memex__" in json_memory_ids(row["source_event_ids"])
    }
    return [memory_id for memory_id in ids if memory_id not in memex_row_ids]
