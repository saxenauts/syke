from __future__ import annotations

import json
from pathlib import Path

from syke.observe.registry import HarnessRegistry
from tests.observe_artifact_helpers import write_claude_code_session, write_opencode_db


def _registry(tmp_path: Path) -> HarnessRegistry:
    return HarnessRegistry(dynamic_adapters_dir=tmp_path / "adapters")


def test_claude_code_seed_ingests_project_artifact_tool_trace(
    db,
    user_id,
    tmp_path: Path,
) -> None:
    artifact = write_claude_code_session(
        tmp_path,
        "claude-session-1",
        [
            {"role": "user", "text": "Question 1"},
            {
                "role": "assistant",
                "text": "Answer 1",
                "tools": [
                    {
                        "id": "tool-1",
                        "name": "Read",
                        "input": {"path": "README.md"},
                    }
                ],
            },
            {
                "role": "user",
                "tool_results": [
                    {
                        "tool_use_id": "tool-1",
                        "content": "file contents",
                    }
                ],
            },
            {"role": "assistant", "text": "Final answer"},
        ],
    )
    adapter = _registry(tmp_path).get_adapter("claude-code", db, user_id)

    assert adapter is not None

    result = adapter.ingest(paths=[artifact])
    rows = db.conn.execute(
        """
        SELECT id, event_type, role, tool_name, tool_correlation_id, parent_event_id, content, extras
        FROM events
        WHERE user_id = ? AND source = 'claude-code'
        ORDER BY timestamp, sequence_index, id
        """,
        (user_id,),
    ).fetchall()

    assert result.events_count == 5
    assert [row["event_type"] for row in rows] == [
        "session.start",
        "turn",
        "tool_call",
        "tool_result",
        "turn",
    ]
    assert rows[1]["role"] == "assistant"
    assert rows[2]["tool_name"] == "Read"
    assert rows[2]["content"] == '{"path": "README.md"}'
    assert rows[3]["tool_correlation_id"] == "tool-1"
    assert rows[3]["parent_event_id"] == rows[2]["id"]
    assert rows[3]["content"] == "file contents"
    assert json.loads(rows[0]["extras"])["artifact_family"] == "project"


def test_claude_code_seed_records_subagent_parent_session_links(
    db,
    user_id,
    tmp_path: Path,
) -> None:
    parent = write_claude_code_session(
        tmp_path,
        "parent-001",
        [
            {"role": "user", "text": "Refactor auth"},
            {"role": "assistant", "text": "Delegating work"},
        ],
    )
    child = write_claude_code_session(
        tmp_path,
        "child-auth",
        [
            {"role": "user", "text": "Implement OAuth2"},
            {"role": "assistant", "text": "Done"},
        ],
        parent_session_id="parent-001",
        agent_id="auth-specialist",
        agent_slug="auth-specialist",
    )
    adapter = _registry(tmp_path).get_adapter("claude-code", db, user_id)

    assert adapter is not None

    result = adapter.ingest(paths=[parent, child])
    rows = db.conn.execute(
        """
        SELECT event_type, session_id, parent_session_id, extras
        FROM events
        WHERE user_id = ? AND source = 'claude-code'
        ORDER BY session_id, timestamp, sequence_index, id
        """,
        (user_id,),
    ).fetchall()

    assert result.events_count == 4
    assert {row["session_id"] for row in rows if row["parent_session_id"] == "parent-001"} == {
        "child-auth:agent:auth-specialist"
    }
    assert {row["session_id"] for row in rows if row["parent_session_id"] is None} == {"parent-001"}
    assert json.loads(rows[0]["extras"])["is_sidechain"] is True


def test_opencode_seed_ingests_sqlite_artifact_with_parent_session(
    db,
    user_id,
    tmp_path: Path,
) -> None:
    artifact = write_opencode_db(
        tmp_path / "opencode.db",
        [
            {
                "id": "opencode-session-1",
                "parent_id": "parent-1",
                "title": "Build feature (@planner subagent)",
                "turns": [
                    {"role": "user", "text": "hello"},
                    {
                        "role": "assistant",
                        "text": "world",
                        "tools": [
                            {
                                "name": "read",
                                "input": {"path": "README.md"},
                                "output": "ok",
                            }
                        ],
                    },
                ],
            }
        ],
    )
    adapter = _registry(tmp_path).get_adapter("opencode", db, user_id)

    assert adapter is not None

    result = adapter.ingest(paths=[artifact])
    rows = db.conn.execute(
        """
        SELECT id, event_type, parent_session_id, tool_name, tool_correlation_id, parent_event_id, content, extras
        FROM events
        WHERE user_id = ? AND source = 'opencode'
        ORDER BY timestamp, sequence_index, id
        """,
        (user_id,),
    ).fetchall()

    assert result.events_count == 4
    assert {row["parent_session_id"] for row in rows} == {"parent-1"}
    assert [row["event_type"] for row in rows] == [
        "session.start",
        "turn",
        "tool_call",
        "tool_result",
    ]
    assert rows[2]["tool_name"] == "read"
    assert rows[2]["tool_correlation_id"] == "call_1"
    assert rows[3]["tool_correlation_id"] == "call_1"
    assert rows[3]["parent_event_id"] == rows[2]["id"]
    assert rows[3]["content"] == "ok"
    assert json.loads(rows[0]["extras"])["artifact_family"] == "sqlite"
