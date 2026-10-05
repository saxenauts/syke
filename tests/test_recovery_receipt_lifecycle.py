from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import syke.control as control_module
import syke.runtime as runtime_module
from syke import db_safety
from syke.control import get_receipt, list_receipts
from syke.db import SykeDB
from syke.db_access import (
    DatabaseLeaseUnavailable,
    acquire_database_lease,
    maintenance_marker_path,
)
from syke.llm import pi_client
from syke.llm.backends import pi_synthesis
from syke.memory import memex_history
from syke.memory.memex import update_memex

pytestmark = pytest.mark.usefixtures("isolated_synthesis_paths")


def _control_dir() -> Path:
    return pi_synthesis.SESSIONS_DIR.parent


def _pi_result(*, session_id: str = "session-native") -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        output="done",
        duration_ms=5,
        cost_usd=0.0,
        input_tokens=10,
        output_tokens=4,
        cache_read_tokens=0,
        cache_write_tokens=0,
        provider="kimi-coding",
        response_model="k2p5",
        response_id="response-1",
        stop_reason="stop",
        session_id=session_id,
        session_file="session.jsonl",
        session_name="syke synthesis",
        tool_calls=[],
        num_turns=1,
    )


def _install_runtime(monkeypatch, prompt_fn) -> None:
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="kimi-coding",
            model=model_override or "k2p5",
        ),
    )
    runtime = SimpleNamespace(
        is_alive=True,
        model="k2p5",
        prompt=prompt_fn,
        status=lambda: {
            "workspace": str(pi_synthesis.WORKSPACE_ROOT),
            "pid": 1,
            "uptime_s": 1,
            "session_count": 1,
        },
    )
    monkeypatch.setattr(
        runtime_module,
        "get_pi_runtime",
        lambda: (_ for _ in ()).throw(RuntimeError()),
    )
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)
    monkeypatch.setattr(
        pi_synthesis,
        "_validate_cycle_output",
        lambda *args: {"valid": True, "issues": [], "stats": {}},
    )


def test_completed_cycle_marks_before_runtime_and_publishes_version_before_receipt(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db", user_id=user_id)
    update_memex(db, user_id, "accepted memex")
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", tmp_path / "MEMEX.md")
    observed_marker: dict[str, str] = {}
    order: list[str] = []

    def _prompt(_prompt: str, **_kwargs) -> SimpleNamespace:
        marker = db_safety.load_recovery_in_progress(user_id)
        assert marker is not None
        observed_marker["cycle_id"] = marker.cycle_id
        update_memex(db, user_id, "accepted memex after cycle")
        return _pi_result()

    _install_runtime(monkeypatch, _prompt)
    real_write_version = memex_history.write_memex_version
    real_write_receipt = control_module.write_receipt

    def _write_version(*args, **kwargs):
        order.append("version")
        return real_write_version(*args, **kwargs)

    def _write_receipt(control_dir, receipt):
        order.append("receipt")
        assert db_safety.load_recovery_in_progress(user_id) is not None
        version = receipt.get("memex_version")
        assert isinstance(version, dict)
        assert (Path(control_dir) / version["path"]).is_file()
        return real_write_receipt(control_dir, receipt)

    monkeypatch.setattr(pi_synthesis, "write_memex_version", _write_version, raising=False)
    monkeypatch.setattr(pi_synthesis, "write_receipt", _write_receipt)

    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            skill_override="base synthesis prompt",
            workspace_root=tmp_path,
            first_run=False,
            now_override=None,
        )
    finally:
        db.close()

    assert result["status"] == "completed"
    assert "state_change" not in result
    assert observed_marker["cycle_id"] == result["cycle_id"]
    assert order == ["version", "receipt"]
    assert db_safety.load_recovery_in_progress(user_id) is None

    receipt = get_receipt(_control_dir(), str(result["cycle_id"]))
    assert receipt is not None
    assert "state_change" not in receipt
    assert set(receipt["acceptance"]) == {"accepted_attempt", "repair_prompts"}
    assert set(receipt["memex_version"]) == {"path", "sha256"}
    version_path = _control_dir() / receipt["memex_version"]["path"]
    version = json.loads(version_path.read_text(encoding="utf-8"))
    assert version["completed_at"] == receipt["completed_at"]
    assert version["session_id"] == receipt["session_id"] == "session-native"


def test_failed_cycle_restores_before_final_receipt_and_clears_marker(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db", user_id=user_id)
    update_memex(db, user_id, "accepted memex")
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", tmp_path / "MEMEX.md")

    def _prompt(_prompt: str, **_kwargs) -> SimpleNamespace:
        assert db_safety.load_recovery_in_progress(user_id) is not None
        (tmp_path / "scratch.txt").write_text("attempted residue", encoding="utf-8")
        db.conn.execute(
            """INSERT INTO memories (id, user_id, content, created_at, updated_at)
               VALUES ('syke-learned', ?, 'preserve exact session IDs', ?, NULL)""",
            (user_id, "2026-01-01T00:00:00+00:00"),
        )
        db.conn.commit()
        update_memex(db, user_id, "rejected memex")
        return _pi_result()

    _install_runtime(monkeypatch, _prompt)
    monkeypatch.setattr(
        pi_synthesis,
        "validate_state_after_cycle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("post-turn validation failed")
        ),
    )
    real_write_receipt = control_module.write_receipt

    def _write_receipt(control_dir, receipt):
        assert receipt["status"] == "failed"
        current_memex = db.get_memex(user_id)
        assert current_memex is not None
        assert current_memex["content"] == "accepted memex"
        # The old learned row is an ordinary memory now: a rejected attempt's
        # write to it is rolled back with everything else.
        assert (
            db.conn.execute("SELECT 1 FROM memories WHERE id = 'syke-learned'").fetchone() is None
        )
        assert db_safety.load_recovery_in_progress(user_id) is not None
        return real_write_receipt(control_dir, receipt)

    monkeypatch.setattr(pi_synthesis, "write_receipt", _write_receipt)

    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            skill_override="base synthesis prompt",
            workspace_root=tmp_path,
            first_run=False,
        )
        assert result["status"] == "failed"
        receipt = list_receipts(_control_dir(), limit=1)[0]
        assert receipt["status"] == "failed"
        assert "state_change" not in receipt
        assert "graph" not in receipt
        assert "workspace" not in receipt
        assert "memex_version" not in receipt
        assert receipt["recovery"] == {
            "restored": True,
            "recovery_point": result["recovery"]["recovery_point"],
        }
        assert (tmp_path / "scratch.txt").read_text(encoding="utf-8") == "attempted residue"
        assert db_safety.load_recovery_in_progress(user_id) is None
    finally:
        db.close()


def test_synthesis_reconciles_stale_marker_before_runtime_reads_state(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db", user_id=user_id)
    update_memex(db, user_id, "accepted memex")
    point = db_safety.create_recovery_point(
        db,
        user_id,
        run_id="stale-run",
        cycle_id="stale-cycle",
    )
    db_safety.rotate_recovery_points(user_id, keep_id=point.id)
    db_safety.mark_recovery_in_progress(
        point,
        started_at="2026-08-10T10:00:00+00:00",
    )
    update_memex(db, user_id, "interrupted memex")
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", tmp_path / "MEMEX.md")
    real_reconcile = pi_synthesis.reconcile_interrupted_synthesis

    def _reconcile(*args, **kwargs):
        marker = maintenance_marker_path(db.db_path)
        assert marker.is_file()
        with pytest.raises(DatabaseLeaseUnavailable):
            acquire_database_lease(db.db_path, blocking=False)
        return real_reconcile(*args, **kwargs)

    monkeypatch.setattr(pi_synthesis, "reconcile_interrupted_synthesis", _reconcile)

    def _prompt(_prompt: str, **_kwargs) -> SimpleNamespace:
        assert db.get_memex(user_id)["content"] == "accepted memex"
        return _pi_result()

    _install_runtime(monkeypatch, _prompt)

    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            skill_override="base synthesis prompt",
            workspace_root=tmp_path,
            first_run=False,
        )
        assert result["status"] == "completed"
        stale_receipt = get_receipt(_control_dir(), "stale-cycle")
        assert stale_receipt is not None
        assert stale_receipt["status"] == "incomplete"
        assert db_safety.load_recovery_in_progress(user_id) is None
        assert not maintenance_marker_path(db.db_path).exists()
    finally:
        db.close()


def test_receipt_failure_leaves_marker_and_does_not_expose_orphan_memex_version(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db", user_id=user_id)
    update_memex(db, user_id, "accepted memex")
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", tmp_path / "MEMEX.md")

    def _prompt(_prompt: str, **_kwargs) -> SimpleNamespace:
        update_memex(db, user_id, "unaccepted memex")
        return _pi_result()

    _install_runtime(monkeypatch, _prompt)
    attempted_statuses: list[str] = []

    def _fail_receipt(_control_dir: Path, receipt: dict[str, object]) -> None:
        attempted_statuses.append(str(receipt["status"]))
        assert db.conn.in_transaction is False
        with sqlite3.connect(db.db_path) as observer:
            committed = observer.execute(
                "SELECT content FROM current_memex WHERE singleton = 1"
            ).fetchone()
        expected = "unaccepted memex" if receipt["status"] == "completed" else "accepted memex"
        assert committed == (expected,)
        raise OSError("receipt unavailable")

    monkeypatch.setattr(pi_synthesis, "write_receipt", _fail_receipt)

    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            skill_override="base synthesis prompt",
            workspace_root=tmp_path,
            first_run=False,
        )
        assert result["status"] == "failed"
        assert "state_change" not in result
        assert db.get_memex(user_id)["content"] == "accepted memex"
        marker = db_safety.load_recovery_in_progress(user_id)
        assert marker is not None
        assert marker.cycle_id == result["cycle_id"]
        assert attempted_statuses == ["completed", "failed"]
        assert list_receipts(_control_dir()) == []
        assert memex_history.load_accepted_memex_versions(_control_dir(), []) == []
    finally:
        db.close()
