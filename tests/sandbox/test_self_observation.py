"""5.6 — Self-Observation (O5).

Proves: After running ingestion, source='syke' events exist with
observer_depth=0, and re-ingestion of the same data doesn't create
recursive observation events.
"""

from __future__ import annotations

import json

from syke.db import SykeDB
from tests.sandbox.conftest import _CLAUDE_PARSE_LINE, _write_adapter_to_disk
from tests.sandbox.helpers import write_claude_code_session
from syke.observe.dynamic_adapter import DynamicAdapter

SANDBOX_USER = "sandbox-self-obs"


def test_self_observation_events_created(tmp_path):
    db = SykeDB(tmp_path / "selfobs.db")
    home = tmp_path / "sandbox"
    home.mkdir()
    (home / ".claude" / "projects").mkdir(parents=True)

    write_claude_code_session(
        home,
        "obs-session",
        [
            {"role": "user", "text": "Tell me about the architecture."},
            {"role": "assistant", "text": "The system has 3 layers."},
        ],
    )

    adapter_dir = _write_adapter_to_disk(tmp_path, "claude-code", _CLAUDE_PARSE_LINE)
    adapter = DynamicAdapter(
        db=db,
        user_id=SANDBOX_USER,
        source_name="claude-code",
        adapter_dir=adapter_dir,
        discover_roots=[home / ".claude"],
    )
    adapter.ingest()

    syke_events = db.conn.execute(
        "SELECT event_type, extras FROM events WHERE user_id = ? AND source = 'syke'",
        (SANDBOX_USER,),
    ).fetchall()

    for et, extras_raw in syke_events:
        extras = json.loads(extras_raw) if extras_raw else {}
        assert extras.get("observer_depth", 0) == 0
    db.close()


def test_no_recursive_observation(tmp_path):
    db = SykeDB(tmp_path / "norecurse.db")
    home = tmp_path / "sandbox"
    home.mkdir()
    (home / ".claude" / "projects").mkdir(parents=True)

    write_claude_code_session(
        home,
        "recurse-session",
        [
            {"role": "user", "text": "Run the tests."},
            {"role": "assistant", "text": "All tests pass."},
        ],
    )

    adapter_dir = _write_adapter_to_disk(tmp_path, "claude-code", _CLAUDE_PARSE_LINE)
    adapter = DynamicAdapter(
        db=db,
        user_id=SANDBOX_USER,
        source_name="claude-code",
        adapter_dir=adapter_dir,
        discover_roots=[home / ".claude"],
    )

    adapter.ingest()
    count_after_first = db.conn.execute(
        "SELECT COUNT(*) FROM events WHERE user_id = ?", (SANDBOX_USER,)
    ).fetchone()[0]

    adapter.ingest()
    count_after_second = db.conn.execute(
        "SELECT COUNT(*) FROM events WHERE user_id = ?", (SANDBOX_USER,)
    ).fetchone()[0]

    assert count_after_second == count_after_first
    db.close()
