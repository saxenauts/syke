"""5.3 — Sub-Agent Hive (O2, parent_session_id).

Proves: 1 parent session + 3 sub-agent sessions correctly record
parent_session_id links and agent metadata.
"""

from __future__ import annotations

from tests.sandbox.conftest import run_adapter
from tests.sandbox.helpers import write_claude_code_session


PARENT_TURNS = [
    {"role": "user", "text": "Refactor the authentication system to use OAuth2."},
    {"role": "assistant", "text": "I will delegate sub-tasks to specialized agents."},
]

CHILD_SPECS = [
    {
        "id": "child-auth",
        "slug": "auth-specialist",
        "turns": [
            {"role": "user", "text": "Implement OAuth2 token exchange."},
            {"role": "assistant", "text": "Token exchange implemented with PKCE flow."},
        ],
    },
    {
        "id": "child-db",
        "slug": "db-specialist",
        "turns": [
            {"role": "user", "text": "Add OAuth2 tables to the database schema."},
            {"role": "assistant", "text": "Migration added for oauth_tokens table."},
        ],
    },
    {
        "id": "child-test",
        "slug": "test-specialist",
        "turns": [
            {"role": "user", "text": "Write integration tests for the OAuth2 flow."},
            {"role": "assistant", "text": "Added 12 integration tests covering all grant types."},
        ],
    },
]


def _setup(claude_adapter):
    adapter, home = claude_adapter
    write_claude_code_session(home, "parent-001", PARENT_TURNS)
    for child in CHILD_SPECS:
        write_claude_code_session(
            home,
            child["id"],
            child["turns"],
            parent_session_id="parent-001",
            agent_id=child["id"],
            agent_slug=child["slug"],
        )
    run_adapter(adapter, home)
    return adapter


def test_four_distinct_sessions(claude_adapter, user_id):
    adapter = _setup(claude_adapter)
    rows = adapter.db.conn.execute(
        "SELECT DISTINCT session_id FROM events WHERE user_id = ?", (user_id,)
    ).fetchall()
    assert len(rows) == 4, f"Expected 4 sessions (1 parent + 3 children), got {len(rows)}"


def test_parent_session_links(claude_adapter, user_id):
    adapter = _setup(claude_adapter)
    children = adapter.db.conn.execute(
        """SELECT DISTINCT session_id FROM events
           WHERE user_id = ? AND parent_session_id = 'parent-001'""",
        (user_id,),
    ).fetchall()
    child_ids = {r[0] for r in children}
    expected = {"child-auth", "child-db", "child-test"}
    assert child_ids == expected, f"Expected {expected}, got {child_ids}"


def test_parent_has_no_parent(claude_adapter, user_id):
    adapter = _setup(claude_adapter)
    parent_events = adapter.db.conn.execute(
        """SELECT parent_session_id FROM events
           WHERE user_id = ? AND session_id = 'parent-001'""",
        (user_id,),
    ).fetchall()
    for row in parent_events:
        assert row[0] is None, f"Parent session should have no parent_session_id, got {row[0]}"


def test_child_sessions_have_events(claude_adapter, user_id):
    adapter = _setup(claude_adapter)
    for child in CHILD_SPECS:
        count = adapter.db.conn.execute(
            "SELECT COUNT(*) FROM events WHERE user_id = ? AND session_id = ?",
            (user_id, child["id"]),
        ).fetchone()[0]
        assert count > 0, f"No events for child session {child['id']}"
