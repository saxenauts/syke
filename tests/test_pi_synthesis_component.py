from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import pytest

import syke.runtime as runtime_module
from syke.control import get_receipt
from syke.db import SykeDB
from syke.llm import pi_client
from syke.llm.backends import pi_synthesis
from syke.memory.memex import update_memex

pytestmark = pytest.mark.usefixtures("isolated_synthesis_paths")


def _configure_rpc_child(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scenario: str,
    db_path: Path,
    user_id: str,
) -> None:
    shim_path = Path(__file__).with_name("pi_rpc_shim.py")
    workspace = pi_synthesis.WORKSPACE_ROOT
    control_root = pi_synthesis.SESSIONS_DIR.parent
    monkeypatch.setenv("SYKE_DISABLE_SANDBOX", "1")
    monkeypatch.setattr(pi_synthesis, "resolve_pi_model", lambda _override=None: "shim-model")
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda _override=None: pi_client.PiLaunchBinding(provider=None, model="shim-model"),
    )
    monkeypatch.setattr(
        pi_client,
        "_build_rpc_launch_command",
        lambda **_kwargs: (
            [
                sys.executable,
                str(shim_path),
                scenario,
                str(db_path),
                str(workspace),
                str(control_root),
                user_id,
            ],
            {},
        ),
    )


def _insert_memory(db: SykeDB, memory_id: str, user_id: str, content: str) -> None:
    db.conn.execute(
        """INSERT INTO memories (id, user_id, content, created_at, updated_at)
           VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00', NULL)""",
        (memory_id, user_id, content),
    )
    db.conn.commit()


def test_synthesis_accepts_real_child_mutations_after_rpc_settlement(
    user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_events: list[dict[str, object]] = []
    runtime_module.stop_pi_runtime()
    db = SykeDB(pi_synthesis.SYKE_DB, user_id=user_id)
    retained_lease = db._lease
    memex_id = update_memex(db, user_id, "canonical memex")
    _insert_memory(db, "memory-stable", user_id, "accepted before component")
    _configure_rpc_child(
        monkeypatch,
        scenario="accepted",
        db_path=Path(str(db.db_path)),
        user_id=user_id,
    )
    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            first_run=False,
            timeout_override=5.0,
            on_runtime_event=runtime_events.append,
        )

        assert result["status"] == "completed", (
            result.get("error"),
            result.get("recovery_error"),
        )
        assert result["output"] == "accepted"
        assert result["response_id"] == "shim-final-1"
        assert result["session_id"] == "shim-session"
        assert result["session_name"] == f"syke:synthesis:{result['cycle_id']}"
        assert db._lease is retained_lease
        assert [event["type"] for event in runtime_events] == [
            "agent_end",
            "auto_retry_start",
            "auto_retry_end",
            "message_end",
            "agent_end",
            "agent_settled",
            "response",
        ]
        current_memex = db.get_memex(user_id)
        assert current_memex is not None
        assert current_memex["id"] == memex_id
        assert current_memex["content"] == "accepted component memex"
        assert (
            db.conn.execute("SELECT content FROM memories WHERE id = 'memory-stable'").fetchone()[
                "content"
            ]
            == "revised by component"
        )
        semantic_gate = cast(dict[str, Any], result["semantic_gate"])
        assert semantic_gate["stats"]["graph_change"] == {
            "created_memory_ids": ["memory-new"],
            "revised_memory_ids": ["memory-stable"],
            "created_link_ids": ["link-new"],
            "revised_link_ids": [],
            "removed_link_ids": [],
        }
        assert (pi_synthesis.WORKSPACE_ROOT / "artifacts" / "component.txt").read_text(
            encoding="utf-8"
        ) == "accepted workspace effect\n"
        receipt = get_receipt(pi_synthesis.SESSIONS_DIR.parent, str(result["cycle_id"]))
        assert receipt is not None
        assert receipt["status"] == "completed"
        assert receipt["session_id"] == "shim-session"
        memex_version = receipt.get("memex_version")
        assert isinstance(memex_version, dict)
        assert set(memex_version) == {"path", "sha256"}
        assert "state_change" not in receipt
        assert "graph" not in receipt
        assert "workspace" not in receipt
    finally:
        db.close()
        runtime_module.stop_pi_runtime()


def test_synthesis_restores_before_repairing_in_the_same_rpc_session(
    user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_module.stop_pi_runtime()
    db = SykeDB(pi_synthesis.SYKE_DB, user_id=user_id)
    update_memex(db, user_id, "canonical memex")
    _configure_rpc_child(
        monkeypatch,
        scenario="repair",
        db_path=Path(str(db.db_path)),
        user_id=user_id,
    )
    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            first_run=False,
            timeout_override=5.0,
        )

        assert result["status"] == "completed", (
            result.get("error"),
            result.get("recovery_error"),
        )
        assert result["output"] == "repaired"
        assert result["session_id"] == "shim-session"
        assert result["num_turns"] == 2
        assert result["input_tokens"] == 20
        assert result["output_tokens"] == 8
        acceptance = cast(dict[str, Any], result["acceptance"])
        assert acceptance["accepted_attempt"] == 2
        assert acceptance["repair_prompts"] == 1
        assert acceptance["rejections"][0]["stage"] == "semantic_gate"
        assert acceptance["restorations"][0]["after_attempt"] == 1
        current_memex = db.get_memex(user_id)
        assert current_memex is not None
        assert current_memex["content"] == "repaired component memex"
        receipt = get_receipt(pi_synthesis.SESSIONS_DIR.parent, str(result["cycle_id"]))
        assert receipt is not None
        assert receipt["status"] == "completed"
        assert receipt["session_id"] == "shim-session"
        assert receipt["acceptance"] == {"accepted_attempt": 2, "repair_prompts": 1}
    finally:
        db.close()
        runtime_module.stop_pi_runtime()
