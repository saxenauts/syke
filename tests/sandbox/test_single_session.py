"""5.1 — Single Harness, Single Session (O1 timeline, O3 taxonomy).

Proves: One Claude Code session with 3 user turns, 3 assistant turns
(2 with tool_use) produces the correct event count, types, roles,
and sequence through the real adapter code path.
"""

from __future__ import annotations

from tests.sandbox.conftest import run_adapter
from tests.sandbox.helpers import count_events, write_claude_code_session

TURNS = [
    {"role": "user", "text": "Explain the codebase structure."},
    {
        "role": "assistant",
        "text": "The codebase has 3 main modules.",
        "tools": [
            {"name": "Read", "input": {"path": "src/main.py"}},
        ],
    },
    {"role": "user", "text": "Show me the config."},
    {
        "role": "assistant",
        "text": "Here is the config file.",
        "tools": [
            {"name": "Read", "input": {"path": "config.toml"}},
        ],
    },
    {"role": "user", "text": "Looks good, thanks."},
    {"role": "assistant", "text": "Happy to help!"},
]


def test_event_count(claude_adapter, user_id):
    adapter, home = claude_adapter
    write_claude_code_session(home, "sess-001", TURNS)
    result = run_adapter(adapter, home)

    total = count_events(adapter.db, user_id=user_id)
    # 1 session.start envelope + 5 turns (first user message absorbed
    # into envelope as title/content, remaining 2 user + 3 assistant = 5)
    assert total == 6, f"Expected 6 events, got {total}"
    assert result.events_count == 6


def test_event_types(claude_adapter, user_id):
    adapter, home = claude_adapter
    write_claude_code_session(home, "sess-002", TURNS)
    run_adapter(adapter, home)

    types = set(
        row[0]
        for row in adapter.db.conn.execute(
            "SELECT DISTINCT event_type FROM events WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    )
    assert "session.start" in types or "session" in types
    assert "turn" in types


def test_roles(claude_adapter, user_id):
    adapter, home = claude_adapter
    write_claude_code_session(home, "sess-003", TURNS)
    run_adapter(adapter, home)

    roles = adapter.db.conn.execute(
        "SELECT role, COUNT(*) FROM events WHERE user_id = ? AND event_type = 'turn' GROUP BY role",
        (user_id,),
    ).fetchall()
    role_map = {r: c for r, c in roles}
    assert role_map.get("user") == 2, f"Expected 2 user turns, got {role_map}"
    assert role_map.get("assistant") == 3, f"Expected 3 assistant turns, got {role_map}"


def test_sequence_index_increasing(claude_adapter, user_id):
    adapter, home = claude_adapter
    write_claude_code_session(home, "sess-004", TURNS)
    run_adapter(adapter, home)

    indices = [
        row[0]
        for row in adapter.db.conn.execute(
            "SELECT sequence_index FROM events WHERE user_id = ? AND sequence_index IS NOT NULL ORDER BY sequence_index",
            (user_id,),
        ).fetchall()
    ]
    assert len(indices) >= 5
    assert indices == sorted(indices), "sequence_index not monotonically increasing"
    assert len(set(indices)) == len(indices), "duplicate sequence_index values"


def test_required_fields(claude_adapter, user_id):
    adapter, home = claude_adapter
    write_claude_code_session(home, "sess-005", TURNS)
    run_adapter(adapter, home)

    rows = adapter.db.conn.execute(
        "SELECT source, session_id, external_id, timestamp FROM events WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    for row in rows:
        source, session_id, external_id, timestamp = row
        assert source == "claude-code", f"Wrong source: {source}"
        assert session_id is not None, "session_id is NULL"
        assert external_id is not None, "external_id is NULL"
        assert timestamp is not None, "timestamp is NULL"


def test_idempotent_reingest(claude_adapter, user_id):
    adapter, home = claude_adapter
    write_claude_code_session(home, "sess-006", TURNS)

    result1 = run_adapter(adapter, home)
    count_after_first = count_events(adapter.db, user_id=user_id)

    result2 = run_adapter(adapter, home)
    count_after_second = count_events(adapter.db, user_id=user_id)

    assert count_after_second == count_after_first, (
        f"Re-ingest changed count: {count_after_first} -> {count_after_second}"
    )
    assert result2.events_count == 0, f"Re-ingest inserted {result2.events_count} events"
