"""Synthetic data builders for observe validation.

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
    """Write a synthetic Claude Code project JSONL session file.

    Args:
        base_dir: Root directory (becomes HOME). File is written to
            base_dir/.claude/projects/{project_dir}/{session_id}.jsonl
            or, for subagents,
            base_dir/.claude/projects/{project_dir}/{parent_session_id}/subagents/{agent_id}/{session_id}.jsonl
        session_id: Session identifier.
        turns: List of dicts: {"role": "user"|"assistant", "text": "...",
               "tools": [optional list of {"name", "input"}],
               "tool_results": [optional list of {"tool_use_id", "content", "is_error"}]}
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
    if parent_session_id and agent_id:
        project_path = project_path / parent_session_id / "subagents" / agent_id
    project_path.mkdir(parents=True, exist_ok=True)
    fpath = project_path / f"{session_id}.jsonl"

    lines: list[str] = []
    for i, turn in enumerate(turns):
        turn_ts = (ts + timedelta(seconds=i)).isoformat()
        role = turn["role"]
        text = turn.get("text", "")
        content: str | list[dict[str, Any]]

        if role == "assistant":
            assistant_blocks: list[dict[str, Any]] = []
            if text:
                assistant_blocks.append({"type": "text", "text": text})
            for tool_idx, tool in enumerate(turn.get("tools", [])):
                assistant_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tool.get("id", f"call_{session_id}_{i}_{tool_idx}"),
                        "name": tool["name"],
                        "input": tool.get("input", {}),
                    }
                )
            content = assistant_blocks
        else:
            user_blocks: list[dict[str, Any]] = []
            if text:
                user_blocks.append({"type": "text", "text": text})
            for tool_result in turn.get("tool_results", []):
                user_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_result["tool_use_id"],
                        "content": tool_result.get("content", ""),
                        "is_error": bool(tool_result.get("is_error", False)),
                    }
                )
            content = text if text and not turn.get("tool_results") else user_blocks

        record: dict[str, Any] = {
            "type": role,
            "sessionId": session_id,
            "timestamp": turn_ts,
            "cwd": f"/tmp/{project_dir}",
            "gitBranch": "main",
            "message": {"role": role, "content": content},
        }
        if agent_id:
            record["agentId"] = agent_id
        if agent_slug:
            record["slug"] = agent_slug
        if parent_session_id:
            record["isSidechain"] = True

        lines.append(json.dumps(record))

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
        summary_diffs TEXT, revert TEXT,
        time_compacting INTEGER, time_archived INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS message (
        id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS part (
        id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS project (
        id TEXT PRIMARY KEY, worktree TEXT, vcs TEXT, name TEXT,
        commands TEXT, time_created INTEGER, time_updated INTEGER
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS workspace (
        id TEXT PRIMARY KEY, type TEXT, name TEXT, directory TEXT, extra TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS session_share (
        id TEXT PRIMARY KEY, session_id TEXT, secret TEXT, url TEXT
    )""")
    conn.execute(
        "INSERT OR REPLACE INTO project VALUES (?,?,?,?,?,?,?)",
        ("proj-1", "/tmp/test", "git", "fixture-project", "[]", 0, 0),
    )
    conn.execute(
        "INSERT OR REPLACE INTO workspace VALUES (?,?,?,?,?)",
        ("ws-1", "local", "fixture-workspace", "/tmp/test", "{}"),
    )

    for sess in sessions:
        base_ts = sess.get("start_time", datetime(2026, 3, 16, tzinfo=UTC))
        ts_ms = int(base_ts.timestamp() * 1000)
        conn.execute(
            "INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                "INSERT INTO message VALUES (?,?,?,?,?)",
                (msg_id, sess["id"], msg_ts, msg_ts, msg_data),
            )

            part_id = str(uuid7())
            part_data = json.dumps(
                {
                    "type": "text",
                    "text": turn.get("text", ""),
                }
            )
            conn.execute(
                "INSERT INTO part VALUES (?,?,?,?,?,?)",
                (part_id, msg_id, sess["id"], msg_ts, msg_ts, part_data),
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
                    "INSERT INTO part VALUES (?,?,?,?,?,?)",
                    (tool_part_id, msg_id, sess["id"], msg_ts, msg_ts, tool_data),
                )

    conn.commit()
    conn.close()
    return path


def write_hermes_session(
    base_dir: Path,
    session_id: str,
    turns: list[dict[str, Any]],
    *,
    start_time: datetime | None = None,
    model: str = "minimax-m2.5",
) -> Path:
    """Write a synthetic Hermes session JSON plus state.db rows."""
    ts = start_time or datetime(2026, 3, 16, tzinfo=UTC)
    hermes_dir = base_dir / ".hermes"
    sessions_dir = hermes_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    payload_messages: list[dict[str, Any]] = []
    for turn in turns:
        message: dict[str, Any] = {
            "role": turn["role"],
            "content": turn.get("text", ""),
        }
        if turn.get("reasoning"):
            message["reasoning"] = turn["reasoning"]
            message["reasoning_details"] = [
                {
                    "type": "reasoning.text",
                    "text": turn["reasoning"],
                    "signature": None,
                }
            ]
        if turn.get("tools"):
            message["tool_calls"] = turn["tools"]
        payload_messages.append(message)

    session_path = sessions_dir / f"session_{session_id}.json"
    session_path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "model": model,
                "base_url": "https://example.com/v1",
                "platform": "cli",
                "session_start": ts.isoformat(),
                "last_updated": (ts + timedelta(seconds=max(len(turns) - 1, 0))).isoformat(),
                "message_count": len(payload_messages),
                "messages": payload_messages,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    db_path = hermes_dir / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        user_id TEXT,
        model TEXT,
        model_config TEXT,
        system_prompt TEXT,
        parent_session_id TEXT,
        started_at REAL NOT NULL,
        ended_at REAL,
        end_reason TEXT,
        message_count INTEGER DEFAULT 0,
        tool_call_count INTEGER DEFAULT 0,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        title TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT,
        tool_call_id TEXT,
        tool_calls TEXT,
        tool_name TEXT,
        timestamp REAL NOT NULL,
        token_count INTEGER,
        finish_reason TEXT,
        reasoning TEXT,
        reasoning_details TEXT,
        codex_reasoning_items TEXT
    )""")
    conn.execute(
        """INSERT OR REPLACE INTO sessions (
            id, source, user_id, model, model_config, system_prompt, parent_session_id,
            started_at, ended_at, end_reason, message_count, tool_call_count,
            input_tokens, output_tokens, title
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            "cli",
            None,
            model,
            json.dumps({"max_iterations": 60}),
            "Hermes system prompt",
            None,
            ts.timestamp(),
            (ts + timedelta(seconds=max(len(turns) - 1, 0))).timestamp(),
            "completed",
            len(turns),
            sum(len(turn.get("tools", [])) for turn in turns),
            0,
            0,
            None,
        ),
    )
    for idx, turn in enumerate(turns):
        turn_ts = ts + timedelta(seconds=idx)
        tool_calls = turn.get("tools")
        conn.execute(
            """INSERT INTO messages (
                session_id, role, content, tool_call_id, tool_calls, tool_name,
                timestamp, token_count, finish_reason, reasoning, reasoning_details, codex_reasoning_items
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                turn["role"],
                turn.get("text", ""),
                None,
                json.dumps(tool_calls) if tool_calls else None,
                None,
                turn_ts.timestamp(),
                None,
                "tool_calls" if tool_calls else "stop",
                turn.get("reasoning"),
                json.dumps(
                    [{"type": "reasoning.text", "text": turn["reasoning"], "signature": None}]
                )
                if turn.get("reasoning")
                else None,
                None,
            ),
        )
        if tool_calls:
            for tool in tool_calls:
                call_id = tool["id"]
                conn.execute(
                    """INSERT INTO messages (
                        session_id, role, content, tool_call_id, tool_calls, tool_name,
                        timestamp, token_count, finish_reason, reasoning, reasoning_details, codex_reasoning_items
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id,
                        "tool",
                        json.dumps(tool.get("result", {"output": "ok"})),
                        call_id,
                        None,
                        tool.get("function", {}).get("name"),
                        (turn_ts + timedelta(milliseconds=500)).timestamp(),
                        None,
                        None,
                        None,
                        None,
                        None,
                    ),
                )
    conn.commit()
    conn.close()
    return session_path


def write_gemini_cli_session(
    base_dir: Path,
    project_hash: str,
    session_id: str,
    turns: list[dict[str, Any]],
    *,
    start_time: datetime | None = None,
) -> Path:
    """Write a synthetic Gemini CLI chat recording JSON."""
    ts = start_time or datetime(2026, 3, 16, tzinfo=UTC)
    chats_dir = base_dir / ".gemini" / "tmp" / project_hash / "chats"
    chats_dir.mkdir(parents=True, exist_ok=True)
    session_path = chats_dir / f"session-2026-03-16T00-00-{session_id[:8]}.json"

    messages: list[dict[str, Any]] = []
    for idx, turn in enumerate(turns):
        msg: dict[str, Any] = {
            "id": f"msg-{idx}",
            "timestamp": (ts + timedelta(seconds=idx)).isoformat(),
            "type": turn["type"],
            "content": turn.get("content", ""),
        }
        if turn.get("display_content") is not None:
            msg["displayContent"] = turn["display_content"]
        if turn["type"] == "gemini":
            msg["model"] = turn.get("model", "gemini-2.5-pro")
            if turn.get("tool_calls"):
                msg["toolCalls"] = turn["tool_calls"]
            if turn.get("thoughts"):
                msg["thoughts"] = turn["thoughts"]
            if turn.get("tokens"):
                msg["tokens"] = turn["tokens"]
        messages.append(msg)

    session_path.write_text(
        json.dumps(
            {
                "sessionId": session_id,
                "projectHash": project_hash,
                "startTime": ts.isoformat(),
                "lastUpdated": (ts + timedelta(seconds=max(len(messages) - 1, 0))).isoformat(),
                "messages": messages,
                "kind": "main",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return session_path


def write_copilot_cli_session(
    base_dir: Path,
    session_id: str,
    turns: list[dict[str, Any]],
    *,
    start_time: datetime | None = None,
) -> Path:
    """Write a synthetic GitHub Copilot CLI session-state directory."""
    ts = start_time or datetime(2026, 3, 16, tzinfo=UTC)
    session_dir = base_dir / ".copilot" / "session-state" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    events_path = session_dir / "events.jsonl"
    workspace_path = session_dir / "workspace.yaml"

    lines = [
        json.dumps(
            {
                "type": "session.start",
                "data": {"sessionId": session_id, "context": {"cwd": "/tmp/project"}},
                "id": str(uuid7()),
                "timestamp": ts.isoformat(),
                "parentId": None,
            }
        )
    ]

    previous_id = None
    for idx, turn in enumerate(turns):
        user_id = str(uuid7())
        user_ts = (ts + timedelta(seconds=idx * 2 + 1)).isoformat()
        lines.append(
            json.dumps(
                {
                    "type": "user.message",
                    "data": {"text": turn["user"]},
                    "id": user_id,
                    "timestamp": user_ts,
                    "parentId": previous_id,
                }
            )
        )
        previous_id = user_id

        for tool in turn.get("tools", []):
            tool_id = str(uuid7())
            lines.append(
                json.dumps(
                    {
                        "type": "tool.call",
                        "data": {
                            "toolName": tool["name"],
                            "arguments": tool.get("input", {}),
                            "callId": tool.get("call_id", tool_id),
                        },
                        "id": tool_id,
                        "timestamp": (ts + timedelta(seconds=idx * 2 + 1, milliseconds=500)).isoformat(),
                        "parentId": previous_id,
                    }
                )
            )
            previous_id = tool_id

            result_id = str(uuid7())
            lines.append(
                json.dumps(
                    {
                        "type": "tool.result",
                        "data": {
                            "toolName": tool["name"],
                            "callId": tool.get("call_id", tool_id),
                            "result": tool.get("output", "ok"),
                        },
                        "id": result_id,
                        "timestamp": (ts + timedelta(seconds=idx * 2 + 1, milliseconds=750)).isoformat(),
                        "parentId": previous_id,
                    }
                )
            )
            previous_id = result_id

        assistant_id = str(uuid7())
        lines.append(
            json.dumps(
                {
                    "type": "assistant.message",
                    "data": {"text": turn["assistant"]},
                    "id": assistant_id,
                    "timestamp": (ts + timedelta(seconds=idx * 2 + 2)).isoformat(),
                    "parentId": previous_id,
                }
            )
        )
        previous_id = assistant_id

        lines.append(
            json.dumps(
                {
                    "type": "assistant.turn_end",
                    "data": {},
                    "id": str(uuid7()),
                    "timestamp": (ts + timedelta(seconds=idx * 2 + 2, milliseconds=200)).isoformat(),
                    "parentId": previous_id,
                }
            )
        )
        previous_id = None

    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    workspace_path.write_text(
        "\n".join(
            [
                f"id: {session_id}",
                "cwd: /tmp/project",
                "summary_count: 0",
                "summary: synthetic session",
                f"updated_at: {(ts + timedelta(seconds=max(len(turns) * 2, 0))).isoformat()}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return events_path


def write_antigravity_workflow(
    base_dir: Path,
    workflow_id: str,
    *,
    task: str,
    implementation_plan: str,
    walkthrough: str,
    updated_at: datetime | None = None,
) -> Path:
    """Write a synthetic Antigravity workflow artifact set."""
    ts = updated_at or datetime(2026, 3, 16, tzinfo=UTC)
    brain_dir = base_dir / ".gemini" / "antigravity" / "brain" / workflow_id
    recording_dir = base_dir / ".gemini" / "antigravity" / "browser_recordings" / workflow_id
    brain_dir.mkdir(parents=True, exist_ok=True)
    recording_dir.mkdir(parents=True, exist_ok=True)

    artifacts = {
        "task.md": ("ARTIFACT_TYPE_TASK", task),
        "implementation_plan.md": ("ARTIFACT_TYPE_IMPLEMENTATION_PLAN", implementation_plan),
        "walkthrough.md": ("ARTIFACT_TYPE_WALKTHROUGH", walkthrough),
    }
    for idx, (name, (artifact_type, content)) in enumerate(artifacts.items()):
        artifact_path = brain_dir / name
        artifact_path.write_text(content + "\n", encoding="utf-8")
        (brain_dir / f"{name}.metadata.json").write_text(
            json.dumps(
                {
                    "artifactType": artifact_type,
                    "summary": content.splitlines()[0],
                    "updatedAt": (ts + timedelta(seconds=idx)).isoformat().replace("+00:00", "Z"),
                    "version": idx + 1,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    (recording_dir / "metadata.json").write_text(
        json.dumps(
            {
                "workflowId": workflow_id,
                "summary": "Browser recording captured",
                "updatedAt": (ts + timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return brain_dir


def write_cursor_state_db(
    path: Path,
    session_id: str,
    turns: list[dict[str, Any]],
) -> Path:
    """Write a synthetic Cursor state.vscdb with composerData-like JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE IF NOT EXISTS ItemTable (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    payload_messages: list[dict[str, Any]] = []
    for turn in turns:
        payload_messages.append(
            {
                "role": "user",
                "text": turn["user"],
                "timestamp": datetime(2026, 3, 16, tzinfo=UTC).isoformat(),
            }
        )
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "text": turn["assistant"],
            "timestamp": datetime(2026, 3, 16, tzinfo=UTC).isoformat(),
        }
        if turn.get("tools"):
            assistant_message["toolCalls"] = turn["tools"]
        payload_messages.append(assistant_message)

    payload = {
        "composerId": session_id,
        "messages": payload_messages,
    }
    conn.execute(
        "INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)",
        (f"composerData:{session_id}", json.dumps(payload)),
    )
    conn.commit()
    conn.close()
    return path
