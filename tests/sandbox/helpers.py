"""Synthetic data builders for sandbox validation.

Each builder writes data in the exact format the real adapter expects,
so tests exercise the full adapter code path.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid_extensions import uuid7

from syke.db import SykeDB


def write_claude_code_session(
    base_dir: Path,
    session_id: str,
    turns: list[dict[str, Any]],
    *,
    start_time: datetime | None = None,
    parent_session_id: str | None = None,
    agent_id: str | None = None,
    agent_slug: str | None = None,
    project_dir: str = "test-project",
) -> Path:
    """Write a synthetic Claude Code JSONL session file.

    Args:
        base_dir: Root directory (becomes HOME). File is written to
            base_dir/.claude/projects/{project_dir}/{session_id}.jsonl
        session_id: Session identifier.
        turns: List of dicts: {"role": "user"|"assistant", "text": "...",
               "tools": [optional list of {"name", "input", "output"}]}
        start_time: Base timestamp (default: 2026-03-16T00:00:00Z).
        parent_session_id: For sub-agent sessions.
        agent_id: For sub-agent sessions.
        agent_slug: For sub-agent sessions.
        project_dir: Claude project directory name.

    Returns:
        Path to the written JSONL file.
    """
    ts = start_time or datetime(2026, 3, 16, tzinfo=UTC)
    project_path = base_dir / ".claude" / "projects" / project_dir
    project_path.mkdir(parents=True, exist_ok=True)
    fpath = project_path / f"{session_id}.jsonl"

    lines: list[str] = []
    for i, turn in enumerate(turns):
        turn_ts = (ts + timedelta(seconds=i)).isoformat()
        role = turn["role"]
        text = turn.get("text", "")

        record: dict[str, Any] = {
            "type": role,
            "sessionId": session_id,
            "timestamp": turn_ts,
            "message": {"content": [{"type": "text", "text": text}]},
        }
        if parent_session_id:
            record["parentSessionId"] = parent_session_id
        if agent_id:
            record["agentId"] = agent_id
        if agent_slug:
            record["agentSlug"] = agent_slug

        lines.append(json.dumps(record))

        for tool in turn.get("tools", []):
            tool_ts = (ts + timedelta(seconds=i, milliseconds=500)).isoformat()
            tool_use: dict[str, Any] = {
                "type": "tool_use",
                "sessionId": session_id,
                "timestamp": tool_ts,
                "tool_name": tool["name"],
                "input": tool.get("input", {}),
            }
            lines.append(json.dumps(tool_use))

    fpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return fpath


def write_codex_session(
    base_dir: Path,
    session_id: str,
    turns: list[dict[str, Any]],
    *,
    start_time: datetime | None = None,
) -> Path:
    """Write a synthetic Codex JSONL rollout file.

    Args:
        base_dir: Root directory (becomes HOME). File is written to
            base_dir/.codex/sessions/rollout-{session_id}.jsonl
        session_id: Session identifier (used in filename).
        turns: List of dicts: {"role": "user"|"assistant", "text": "...",
               "tools": [optional list of {"name", "call_id", "input", "output"}]}
        start_time: Base timestamp.

    Returns:
        Path to the written JSONL file.
    """
    ts = start_time or datetime(2026, 3, 16, tzinfo=UTC)
    sessions_dir = base_dir / ".codex" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    fpath = sessions_dir / f"rollout-{session_id}.jsonl"

    lines: list[str] = []

    meta: dict[str, Any] = {
        "type": "session_meta",
        "timestamp": ts.isoformat(),
        "payload": {"cwd": "/tmp/test-project", "git": {"branch": "main"}},
    }
    lines.append(json.dumps(meta))

    for i, turn in enumerate(turns):
        turn_ts = (ts + timedelta(seconds=i + 1)).isoformat()
        role = turn["role"]
        text = turn.get("text", "")

        content_type = "input_text" if role == "user" else "output_text"
        record: dict[str, Any] = {
            "type": "response_item",
            "timestamp": turn_ts,
            "payload": {
                "type": "message",
                "role": role,
                "content": [{"type": content_type, "text": text}],
            },
        }
        lines.append(json.dumps(record))

        for tool in turn.get("tools", []):
            call_id = tool.get("call_id", f"call_{i}_{uuid7()}")
            tool_use: dict[str, Any] = {
                "type": "response_item",
                "timestamp": turn_ts,
                "payload": {
                    "type": "function_call",
                    "name": tool["name"],
                    "call_id": call_id,
                    "arguments": json.dumps(tool.get("input", {})),
                },
            }
            lines.append(json.dumps(tool_use))

            tool_result: dict[str, Any] = {
                "type": "response_item",
                "timestamp": turn_ts,
                "payload": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": tool.get("output", "ok"),
                },
            }
            lines.append(json.dumps(tool_result))

    fpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return fpath


def write_opencode_db(
    path: Path,
    sessions: list[dict[str, Any]],
) -> Path:
    """Write a synthetic OpenCode SQLite database.

    Args:
        path: Full path for the .db file.
        sessions: List of dicts: {"id": str, "title": str, "parent_id": str|None,
                  "turns": [{"role": "user"|"assistant", "text": "...",
                             "tools": [optional]}],
                  "start_time": datetime|None}

    Returns:
        Path to the written database.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE IF NOT EXISTS session (
        id TEXT PRIMARY KEY, title TEXT, time_created INTEGER,
        time_updated INTEGER, directory TEXT, parent_id TEXT,
        project_id TEXT, workspace_id TEXT, slug TEXT, version TEXT,
        share_url TEXT, permission TEXT,
        summary_additions INTEGER DEFAULT 0,
        summary_deletions INTEGER DEFAULT 0,
        summary_files INTEGER DEFAULT 0,
        summary_diffs TEXT, revert TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS message (
        id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS part (
        id TEXT PRIMARY KEY, message_id TEXT, time_created INTEGER, data TEXT
    )""")

    for sess in sessions:
        base_ts = sess.get("start_time", datetime(2026, 3, 16, tzinfo=UTC))
        ts_ms = int(base_ts.timestamp() * 1000)
        conn.execute(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sess["id"],
                sess.get("title", "Test session"),
                ts_ms,
                ts_ms + 60000,
                "/tmp/test",
                sess.get("parent_id"),
                "proj-1",
                "ws-1",
                sess["id"][:8],
                "1",
                "",
                "",
                0,
                0,
                0,
                None,
                None,
            ),
        )

        for t_idx, turn in enumerate(sess.get("turns", [])):
            msg_id = str(uuid7())
            msg_ts = ts_ms + (t_idx * 1000)
            msg_data = json.dumps(
                {
                    "role": turn["role"],
                    "time": {"created": msg_ts},
                }
            )
            conn.execute(
                "INSERT INTO message VALUES (?,?,?,?)",
                (msg_id, sess["id"], msg_ts, msg_data),
            )

            part_id = str(uuid7())
            part_data = json.dumps(
                {
                    "type": "text",
                    "text": turn.get("text", ""),
                }
            )
            conn.execute(
                "INSERT INTO part VALUES (?,?,?,?)",
                (part_id, msg_id, msg_ts, part_data),
            )

            for tool in turn.get("tools", []):
                tool_part_id = str(uuid7())
                tool_data = json.dumps(
                    {
                        "type": "tool",
                        "tool": tool["name"],
                        "callID": f"call_{t_idx}",
                        "state": {
                            "input": tool.get("input", {}),
                            "output": tool.get("output", "ok"),
                            "status": "completed",
                        },
                    }
                )
                conn.execute(
                    "INSERT INTO part VALUES (?,?,?,?)",
                    (tool_part_id, msg_id, msg_ts, tool_data),
                )

    conn.commit()
    conn.close()
    return path


def count_events(db: SykeDB, **filters: str) -> int:
    """Count events with optional filters (source, session_id, event_type, role)."""
    where = ["user_id = ?"]
    params: list[str] = [filters.pop("user_id", "sandbox-user")]
    for col, val in filters.items():
        where.append(f"{col} = ?")
        params.append(val)
    sql = f"SELECT COUNT(*) FROM events WHERE {' AND '.join(where)}"
    return db.conn.execute(sql, params).fetchone()[0]


def query_events(db: SykeDB, user_id: str = "sandbox-user", **filters: str) -> list[sqlite3.Row]:
    """Query events with optional filters. Returns full rows."""
    where = ["user_id = ?"]
    params: list[str] = [user_id]
    for col, val in filters.items():
        where.append(f"{col} = ?")
        params.append(val)
    sql = f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY sequence_index ASC, id ASC"
    return db.conn.execute(sql, params).fetchall()
