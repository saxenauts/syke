"""Tests for the local read-only timeline web server."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from syke.daemon import web as web_mod
from syke.daemon.web import (
    SykeWebServer,
    _extract_host,
    _iso_to_dt,
    query_ask,
    query_cycle,
    query_health,
    query_log_tail,
    query_timeline,
)
from syke.db import SykeDB


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed_db(tmp_path: Path) -> tuple[Path, str]:
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        # One memex baseline + one updated memex (memex chain)
        from syke.memory.memex import update_memex

        update_memex(db, user_id, "# MEMEX\n\n## Active Routes\n\n- baseline\n")
        update_memex(db, user_id, "# MEMEX\n\n## Active Routes\n\n- baseline\n- new route\n")

        # One completed cycle
        cid = db.insert_cycle_record(user_id, model="gpt-5.4")
        db.complete_cycle_record(
            cid,
            status="completed",
            memex_updated=1,
            memories_created=2,
            memories_updated=0,
            duration_ms=180000,
            cost_usd=0.02,
            input_tokens=1000,
            output_tokens=200,
        )

        # Backdate the cycle record to ensure it falls inside the timeline window
        now_iso = datetime.now(UTC).isoformat()
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ? WHERE id = ?",
            (now_iso, now_iso, cid),
        )
        db._conn.commit()

        # One ask trace
        from uuid_extensions import uuid7

        from syke.trace_store import persist_rollout_trace

        ask_id = str(uuid7())
        persist_rollout_trace(
            db=db,
            user_id=user_id,
            run_id=ask_id,
            kind="ask",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            status="completed",
            input_text="What is syke?",
            output_text="A memory system.",
            transcript=[{"role": "user", "blocks": [{"type": "text", "text": "ctx"}]}],
            metrics={"duration_ms": 1500, "cost_usd": 0.001},
            runtime={"model": "gpt-5.4", "num_turns": 2},
        )
    return db_path, user_id


@contextmanager
def _running_server(tmp_path: Path, monkeypatch, *, html: str = "<!doctype html><h1>ok</h1>"):
    db_path, user_id = _seed_db(tmp_path)
    monkeypatch.setenv("SYKE_DB", str(db_path))
    html_path = tmp_path / "index.html"
    html_path.write_text(html)
    port = _free_port()
    srv = SykeWebServer(user_id, port, html_path)
    assert srv.start()
    try:
        yield db_path, user_id, port
    finally:
        srv.stop()


# ─── Host validation (DNS rebinding defense) ────────────────────────────────


def test_extract_host_strips_port():
    assert _extract_host("localhost:8765") == "localhost"
    assert _extract_host("127.0.0.1:9999") == "127.0.0.1"
    assert _extract_host("[::1]:8765") == "[::1]"
    assert _extract_host("evil.com") == "evil.com"
    assert _extract_host("") == ""
    assert _extract_host(None) == ""


def test_server_rejects_non_localhost_host(tmp_path, monkeypatch):
    with _running_server(tmp_path, monkeypatch) as (_, _, port):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"Host": "evil.com"})
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req, timeout=2)
        assert excinfo.value.code == 403


def test_server_accepts_localhost_host(tmp_path, monkeypatch):
    with _running_server(tmp_path, monkeypatch) as (_, _, port):
        for host in ("localhost", "127.0.0.1", "[::1]"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/", headers={"Host": f"{host}:{port}"}
            )
            with urllib.request.urlopen(req, timeout=2) as r:
                assert r.status == 200
                assert b"ok" in r.read()


def test_server_writes_security_headers(tmp_path, monkeypatch):
    with _running_server(tmp_path, monkeypatch) as (_, _, port):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            headers = {k.lower(): v for k, v in r.headers.items()}
            assert "no-store" in headers["cache-control"]
            assert headers["x-content-type-options"] == "nosniff"
            assert "default-src 'self'" in headers["content-security-policy"]


def test_server_returns_empty_favicon_without_console_noise(tmp_path, monkeypatch):
    with _running_server(tmp_path, monkeypatch) as (_, _, port):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/favicon.ico", timeout=2) as r:
            assert r.status == 204
            assert r.read() == b""


# ─── Query layer ─────────────────────────────────────────────────────────────


def test_query_health_with_seeded_db(tmp_path, monkeypatch):
    db_path, user_id = _seed_db(tmp_path)
    from syke.cli_support import daemon_state
    from syke.onboarding import write_onboarding_state

    monkeypatch.setattr(
        daemon_state,
        "daemon_payload",
        lambda: pytest.fail("resident onboarding receipts should not read live daemon state"),
    )
    write_onboarding_state(
        user_id,
        selected_sources=("codex",),
        total_files=42,
        estimated_minutes=3,
        estimate_method="test",
        mode="daemon",
        monitor="/tmp/onboarding.log",
        persistence={"manager": "launchd", "keeps_daemon_alive": True},
    )
    h = query_health(str(db_path), user_id)
    assert h["db_present"] is True
    assert h["last_cycle"] is not None
    assert h["last_completed_cycle"] is not None
    assert h["memex_updated_at"] is not None
    assert h["onboarding"]["selected_sources"] == ["codex"]
    assert h["onboarding"]["total_files"] == 42
    assert h["onboarding"]["estimated_minutes"] == 3
    assert h["onboarding"]["mode"] == "daemon"
    assert h["onboarding"]["monitor"] == "/tmp/onboarding.log"
    assert h["onboarding"]["persistence"]["manager"] == "launchd"
    assert h["onboarding"]["persistence"]["keeps_daemon_alive"] is True


def test_query_health_replaces_legacy_onboarding_persistence_with_live_service(
    tmp_path, monkeypatch
):
    db_path, user_id = _seed_db(tmp_path)
    from syke.cli_support import daemon_state
    from syke.onboarding import write_onboarding_state

    live_persistence = {
        "manager": "systemd",
        "manager_scope": "user",
        "keeps_syncing": True,
        "keeps_daemon_alive": True,
        "serves_timeline_while_idle": True,
        "restart_policy": "Restart=always",
    }
    monkeypatch.setattr(
        daemon_state,
        "daemon_payload",
        lambda: {
            "running": True,
            "registered": True,
            "persistence": live_persistence,
            "service": {
                "manager": "systemd",
                "registered": True,
                "scheduled_only": False,
                "running": True,
            },
        },
    )
    write_onboarding_state(
        user_id,
        selected_sources=("codex",),
        total_files=42,
        estimated_minutes=3,
        estimate_method="test",
        mode="daemon",
        monitor="/tmp/onboarding.log",
        persistence={
            "manager": "cron",
            "keeps_daemon_alive": False,
            "serves_timeline_while_idle": False,
            "restart_policy": "periodic sync only",
        },
    )

    h = query_health(str(db_path), user_id)

    assert h["onboarding"]["persistence"]["manager"] == "systemd"
    assert h["onboarding"]["persistence"]["keeps_daemon_alive"] is True
    assert h["onboarding"]["persistence"]["serves_timeline_while_idle"] is True
    assert h["onboarding"]["persistence_source"] == "daemon_status"
    assert h["onboarding"]["stored_persistence"]["manager"] == "cron"


def test_query_health_reports_setup_blocker_before_db_exists(tmp_path, monkeypatch):
    from syke.llm import pi_client

    def _fail_model_resolution(_model_override=None):
        raise AssertionError("/api/health must not invoke Pi model resolution")

    monkeypatch.delenv("SYKE_PROVIDER", raising=False)
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr(pi_client, "resolve_pi_model", _fail_model_resolution)

    h = query_health(str(tmp_path / "missing.db"), "fresh")

    assert h["db_present"] is False
    assert h["last_cycle"] is None
    assert h["setup_blocker"]["kind"] == "provider"
    assert "No provider configured" in h["setup_blocker"]["reason"]
    assert "syke auth status" in h["setup_blocker"]["next_steps"]
    assert (
        "syke auth set <provider> --api-key <KEY> --model <model> --use"
        in h["setup_blocker"]["next_steps"]
    )
    assert "syke setup --agent" in h["setup_blocker"]["next_steps"]


def test_query_health_uses_default_provider_hint_without_pi_catalog(tmp_path, monkeypatch):
    from syke.llm import pi_client

    def _fail_model_resolution(_model_override=None):
        raise AssertionError("/api/health must not invoke Pi model resolution")

    pi_agent = tmp_path / "pi-agent"
    pi_agent.mkdir()
    (pi_agent / "settings.json").write_text('{"defaultProvider": "openai-codex"}\n')
    monkeypatch.delenv("SYKE_PROVIDER", raising=False)
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(pi_agent))
    monkeypatch.setattr(pi_client, "resolve_pi_model", _fail_model_resolution)

    h = query_health(str(tmp_path / "missing.db"), "fresh")

    assert h["db_present"] is False
    assert h["setup_blocker"] is None


def test_first_run_html_stays_inside_timeline_shell():
    html_path = Path(web_mod.__file__).resolve().parent.parent / "runtime" / "web" / "index.html"
    html = html_path.read_text(encoding="utf-8")

    assert "function renderFirstRunMemexState()" in html
    assert "renderOnboardingPanel" not in html
    assert 'class="onboard' not in html
    assert "<h2>Next CLI Step</h2>" in html


def test_query_timeline_returns_cycles_and_asks(tmp_path):
    db_path, user_id = _seed_db(tmp_path)
    end_iso = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    t = query_timeline(str(db_path), user_id, end_iso, minutes=7 * 24 * 60)
    kinds = {e["kind"] for e in t["events"]}
    assert "cycle" in kinds
    assert "ask" in kinds
    assert t["count"] == len(t["events"]) >= 2


def test_query_timeline_returns_empty_window_before_db_exists(tmp_path):
    missing_db = tmp_path / "missing.db"
    end_iso = datetime.now(UTC).isoformat()

    t = query_timeline(str(missing_db), "fresh", end_iso, minutes=7 * 24 * 60)

    assert t["user_id"] == "fresh"
    assert t["count"] == 0
    assert t["events"] == []
    assert t["window"]["days"] == 7


def test_iso_to_dt_restores_unencoded_plus_timezone():
    assert _iso_to_dt("2026-05-12T22:00:00+00:00") == datetime(2026, 5, 12, 22, 0, 0, tzinfo=UTC)
    assert _iso_to_dt("2026-05-12T22:00:00 00:00") == datetime(2026, 5, 12, 22, 0, 0, tzinfo=UTC)


def test_query_timeline_uses_display_time_and_memex_timestamp(tmp_path):
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        early = datetime(2026, 4, 8, 7, 0, tzinfo=UTC)
        memex_anchor = datetime(2026, 4, 8, 7, 30, tzinfo=UTC)
        later = datetime(2026, 4, 8, 8, 0, tzinfo=UTC)

        c0 = db.insert_cycle_record(user_id, model="pi")
        c1 = db.insert_cycle_record(user_id, model="pi")
        c2 = db.insert_cycle_record(user_id, model="pi")

        c0_started = early
        c1_started = memex_anchor - timedelta(minutes=10)
        c1_completed = memex_anchor
        c2_started = later
        c2_completed = later + timedelta(minutes=5)

        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed', memex_updated = 0 WHERE id = ?",
            (c0_started.isoformat(), (early + timedelta(minutes=1)).isoformat(), c0),
        )
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed', memex_updated = 1 WHERE id = ?",
            (c1_started.isoformat(), c1_completed.isoformat(), c1),
        )
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed', memex_updated = 1 WHERE id = ?",
            (c2_started.isoformat(), c2_completed.isoformat(), c2),
        )

        from uuid_extensions import uuid7

        db._conn.execute(
            "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
            "VALUES (?, ?, ?, '[\"__memex__\"]', ?, 1)",
            (str(uuid7()), user_id, "# memex\n", c1_completed.isoformat()),
        )
        db._conn.commit()

    t = query_timeline(str(db_path), user_id, (later + timedelta(hours=1)).isoformat(), minutes=180)
    events = [e for e in t["events"] if e["kind"] == "cycle"]
    assert len(events) >= 3
    by_id = {e["id"]: e for e in events}

    assert by_id[c0]["memex_created_at"] is None
    assert by_id[c0]["memex_moved"] is False
    assert by_id[c1]["memex_created_at"] == c1_completed.isoformat()
    assert by_id[c1]["memex_moved"] is True
    assert by_id[c2]["memex_created_at"] == c1_completed.isoformat()
    assert by_id[c2]["memex_updated"] == 1
    assert by_id[c2]["memex_moved"] is False
    for c in [c0, c1, c2]:
        row = by_id[c]
        expected = row["completed_at"] or row["started_at"]
        assert row["display_at"] == expected

    # Timeline ordering should follow display time descending.
    assert events[0]["display_at"] >= events[1]["display_at"] >= events[2]["display_at"]


def test_query_timeline_compares_memex_content_not_recovered_row_ids(tmp_path):
    from uuid_extensions import uuid7

    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        first = datetime(2026, 4, 8, 7, 30, tzinfo=UTC)
        second = datetime(2026, 4, 8, 7, 45, tzinfo=UTC)
        c1 = db.insert_cycle_record(user_id, model="pi")
        c2 = db.insert_cycle_record(user_id, model="pi")
        for cycle_id, completed_at in [(c1, first), (c2, second)]:
            db._conn.execute(
                "UPDATE cycle_records SET started_at = ?, completed_at = ?, "
                "status = 'completed', memex_updated = 1 WHERE id = ?",
                (
                    (completed_at - timedelta(minutes=1)).isoformat(),
                    completed_at.isoformat(),
                    cycle_id,
                ),
            )
        for completed_at in [first, second]:
            db._conn.execute(
                "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
                "VALUES (?, ?, ?, '[\"__memex__\"]', ?, 0)",
                (str(uuid7()), user_id, "# MEMEX\n\nsame content\n", completed_at.isoformat()),
            )
        db._conn.commit()

    t = query_timeline(
        str(db_path),
        user_id,
        (second + timedelta(minutes=10)).isoformat(),
        minutes=60,
    )
    by_id = {e["id"]: e for e in t["events"] if e["kind"] == "cycle"}

    assert by_id[c1]["memex_moved"] is True
    assert by_id[c1]["memex_content_moved"] is True
    assert by_id[c2]["memex_row_changed"] is True
    assert by_id[c2]["memex_content_moved"] is False
    assert by_id[c2]["memex_moved"] is False
    assert by_id[c2]["memex_written"] is False


def test_query_timeline_ignores_reconstruction_memex_artifact_ids(tmp_path):
    from uuid_extensions import uuid7

    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        baseline = datetime(2026, 4, 8, 7, 30, tzinfo=UTC)
        artifact_at = datetime(2026, 4, 8, 7, 45, tzinfo=UTC)
        cycle_at = datetime(2026, 4, 8, 8, 0, tzinfo=UTC)
        db._conn.execute(
            "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
            "VALUES (?, ?, ?, '[\"__memex__\"]', ?, 0)",
            (str(uuid7()), user_id, "# MEMEX\n\nreal baseline\n", baseline.isoformat()),
        )
        db._conn.execute(
            "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
            "VALUES (?, ?, ?, '[\"__memex__\"]', ?, 0)",
            (
                "memex_fullchain_00001",
                user_id,
                "# MEMEX\n\nsynthetic artifact\n",
                artifact_at.isoformat(),
            ),
        )
        cycle_id = db.insert_cycle_record(user_id, model="pi")
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, "
            "status = 'completed' WHERE id = ?",
            ((cycle_at - timedelta(minutes=1)).isoformat(), cycle_at.isoformat(), cycle_id),
        )
        db._conn.commit()

    t = query_timeline(
        str(db_path),
        user_id,
        (cycle_at + timedelta(minutes=10)).isoformat(),
        minutes=60,
    )
    cycle = next(e for e in t["events"] if e["kind"] == "cycle")

    assert cycle["id"] == cycle_id
    assert cycle["memex_created_at"] == baseline.isoformat()
    assert cycle["memex_id"] != "memex_fullchain_00001"
    assert cycle["memex_moved"] is True


def test_query_timeline_uses_trace_tool_calls_for_memex_write_truth(tmp_path):
    from uuid_extensions import uuid7

    from syke.trace_store import persist_rollout_trace

    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        baseline = datetime(2026, 4, 8, 7, 30, tzinfo=UTC)
        cycle_at = datetime(2026, 4, 8, 7, 45, tzinfo=UTC)
        db._conn.execute(
            "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
            "VALUES (?, ?, ?, '[\"__memex__\"]', ?, 1)",
            (str(uuid7()), user_id, "# MEMEX\n\nsame content\n", baseline.isoformat()),
        )
        cycle_id = db.insert_cycle_record(user_id, model="pi")
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, "
            "status = 'completed' WHERE id = ?",
            ((cycle_at - timedelta(minutes=1)).isoformat(), cycle_at.isoformat(), cycle_id),
        )
        persist_rollout_trace(
            db=db,
            user_id=user_id,
            run_id=str(uuid7()),
            kind="synthesis",
            started_at=cycle_at,
            completed_at=cycle_at,
            status="completed",
            output_text="No row changed, but the trace wrote MEMEX.md.",
            tool_calls=[
                {
                    "name": "write",
                    "input": {"path": "MEMEX.md", "content": "# MEMEX\n\nsame content\n"},
                }
            ],
            runtime={"model": "gpt-5.4"},
        )
        db._conn.commit()

    t = query_timeline(
        str(db_path),
        user_id,
        (cycle_at + timedelta(minutes=10)).isoformat(),
        minutes=20,
    )
    cycle = next(e for e in t["events"] if e["kind"] == "cycle")

    assert cycle["id"] == cycle_id
    assert cycle["memex_trace_written"] is True
    assert cycle["memex_row_changed"] is False
    assert cycle["memex_content_moved"] is False
    assert cycle["memex_moved"] is False
    assert cycle["memex_written"] is True

    detail = query_cycle(str(db_path), user_id, cycle_id)
    assert detail is not None
    assert detail["cycle"]["memex_trace_written"] is True
    assert detail["cycle"]["memex_written"] is True
    assert detail["cycle"]["memex_moved"] is False


def test_query_timeline_detects_shell_and_sql_memex_writes(tmp_path):
    from uuid_extensions import uuid7

    from syke.trace_store import persist_rollout_trace

    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        baseline = datetime(2026, 4, 8, 7, 30, tzinfo=UTC)
        memex_id = str(uuid7())
        db._conn.execute(
            "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
            "VALUES (?, ?, ?, '[\"__memex__\"]', ?, 1)",
            (memex_id, user_id, "# MEMEX\n\nsame content\n", baseline.isoformat()),
        )
        scenarios = [
            (
                "file-open",
                datetime(2026, 4, 8, 7, 45, tzinfo=UTC),
                "python3 - <<'PY'\nopen('MEMEX.md','w').write('# MEMEX\\n\\nsame content\\n')\nPY",
                0,
            ),
            (
                "sql-known-memex-id",
                datetime(2026, 4, 8, 8, 0, tzinfo=UTC),
                "python3 - <<'PY'\n"
                "import sqlite3\n"
                "memex = '# MEMEX\\n\\nsame content\\n'\n"
                "conn = sqlite3.connect('syke.db')\n"
                f"conn.execute(\"update memories set content=? where id=?\", (memex, '{memex_id}'))\n"
                "PY",
                0,
            ),
            (
                "drifted-cp",
                datetime(2026, 4, 8, 8, 15, tzinfo=UTC),
                "cp tmp/current_memex.md MEMEX.md",
                4,
            ),
        ]
        cycle_ids: list[str] = []
        for label, cycle_at, command, trace_drift_seconds in scenarios:
            cycle_id = db.insert_cycle_record(user_id, model="pi")
            cycle_ids.append(cycle_id)
            db._conn.execute(
                "UPDATE cycle_records SET started_at = ?, completed_at = ?, "
                "status = 'completed' WHERE id = ?",
                ((cycle_at - timedelta(minutes=1)).isoformat(), cycle_at.isoformat(), cycle_id),
            )
            persist_rollout_trace(
                db=db,
                user_id=user_id,
                run_id=str(uuid7()),
                kind="synthesis",
                started_at=cycle_at,
                completed_at=cycle_at + timedelta(seconds=trace_drift_seconds),
                status="completed",
                output_text=f"Updated MEMEX through {label}.",
                tool_calls=[{"name": "bash", "input": {"command": command}}],
                runtime={"model": "gpt-5.4"},
            )
        db._conn.commit()

    t = query_timeline(
        str(db_path),
        user_id,
        (datetime(2026, 4, 8, 8, 15, tzinfo=UTC)).isoformat(),
        minutes=40,
    )
    by_id = {e["id"]: e for e in t["events"] if e["kind"] == "cycle"}
    for cycle_id in cycle_ids:
        assert by_id[cycle_id]["memex_trace_written"] is True
        assert by_id[cycle_id]["memex_written"] is True
        assert by_id[cycle_id]["memex_moved"] is False

    drift_detail = query_cycle(str(db_path), user_id, cycle_ids[-1])
    assert drift_detail is not None
    assert drift_detail["cycle"]["memex_trace_written"] is True


def test_query_timeline_sorts_by_display_time(tmp_path):
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        c1 = db.insert_cycle_record(user_id, model="pi")
        c2 = db.insert_cycle_record(user_id, model="pi")

        base = datetime(2026, 4, 10, 10, 0, tzinfo=UTC)
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed' WHERE id = ?",
            (base.isoformat(), (base + timedelta(minutes=10)).isoformat(), c1),
        )
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed' WHERE id = ?",
            (
                (base + timedelta(minutes=5)).isoformat(),
                (base + timedelta(minutes=1)).isoformat(),
                c2,
            ),
        )
        db._conn.commit()

    end_iso = (base + timedelta(minutes=20)).isoformat()
    t = query_timeline(str(db_path), user_id, end_iso, minutes=60)
    events = [e for e in t["events"] if e["kind"] == "cycle"]
    assert len(events) >= 2
    assert events[0]["id"] == c1
    assert events[1]["id"] == c2


def test_query_timeline_sorts_mixed_offsets_by_instant(tmp_path):
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        earlier = db.insert_cycle_record(user_id, model="pi")
        later = db.insert_cycle_record(user_id, model="pi")
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed' WHERE id = ?",
            ("2026-05-12T10:00:00+01:00", "2026-05-12T10:00:00+01:00", earlier),
        )
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed' WHERE id = ?",
            ("2026-05-12T09:30:00+00:00", "2026-05-12T09:30:00+00:00", later),
        )
        db._conn.commit()

    t = query_timeline(str(db_path), user_id, "2026-05-12T11:00:00+00:00", minutes=180)
    events = [e for e in t["events"] if e["kind"] == "cycle"]
    assert len(events) >= 2
    assert events[0]["id"] == later
    assert events[1]["id"] == earlier


def test_query_timeline_memex_selection_handles_mixed_timestamp_formats(tmp_path):
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        cycle_id = db.insert_cycle_record(user_id, model="pi")
        boundary = datetime(2026, 4, 10, 12, 0, 0, tzinfo=UTC)
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed' WHERE id = ?",
            (boundary.isoformat(), boundary.isoformat(), cycle_id),
        )

        from uuid_extensions import uuid7

        db._conn.execute(
            "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
            "VALUES (?, ?, ?, '[\"__memex__\"]', ?, 1)",
            (str(uuid7()), user_id, "# MEMEX\n\nmixed format", "2026-04-10T12:00:00.000000Z"),
        )
        db._conn.commit()

    t = query_timeline(
        str(db_path), user_id, (boundary + timedelta(minutes=10)).isoformat(), minutes=60
    )
    cycle_events = [e for e in t["events"] if e["kind"] == "cycle"]
    assert cycle_events
    assert cycle_events[0]["memex_created_at"] == "2026-04-10T12:00:00.000000Z"


def test_cycle_detail_trace_matches_timeline_for_same_second_cycles(tmp_path):
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    now_iso = datetime.now(UTC).replace(microsecond=0).isoformat()

    with SykeDB(db_path) as db:
        first_cycle = db.insert_cycle_record(user_id, model="pi")
        second_cycle = db.insert_cycle_record(user_id, model="pi")
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed' WHERE id = ?",
            (now_iso, now_iso, first_cycle),
        )
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed' WHERE id = ?",
            (now_iso, now_iso, second_cycle),
        )

        from uuid_extensions import uuid7

        from syke.trace_store import persist_rollout_trace

        now_dt = datetime.fromisoformat(now_iso)
        persist_rollout_trace(
            db=db,
            user_id=user_id,
            run_id=str(uuid7()),
            kind="synthesis",
            started_at=now_dt,
            completed_at=now_dt,
            status="completed",
            output_text="a",
            runtime={"model": "model-A"},
        )
        persist_rollout_trace(
            db=db,
            user_id=user_id,
            run_id=str(uuid7()),
            kind="synthesis",
            started_at=now_dt,
            completed_at=now_dt,
            status="completed",
            output_text="b",
            runtime={"model": "model-B"},
        )

    end_iso = (datetime.fromisoformat(now_iso) + timedelta(minutes=1)).isoformat()
    t = query_timeline(str(db_path), user_id, end_iso, minutes=60)
    cycle_events = [e for e in t["events"] if e["kind"] == "cycle"]
    assert {e["model"] for e in cycle_events} == {"model-A", "model-B"}
    for event in cycle_events:
        detail = query_cycle(str(db_path), user_id, event["id"])
        assert detail is not None
        assert detail["trace"] is not None
        assert detail["trace"]["model"] == event["model"]


def test_query_cycle_includes_memex_diff_base_and_memories(tmp_path):
    db_path, user_id = _seed_db(tmp_path)
    end_iso = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    t = query_timeline(str(db_path), user_id, end_iso, minutes=7 * 24 * 60)
    cycle = next(e for e in t["events"] if e["kind"] == "cycle")
    detail = query_cycle(str(db_path), user_id, cycle["id"])
    assert detail is not None
    assert detail["memex"]["content"].strip().startswith("# MEMEX")
    assert detail["cycle"]["memex_updated"] == 1
    assert detail["cycle"]["memex_moved"] is False
    # The cycle flag was set, but the selected MEMEX row already existed at
    # cycle start; this cycle's diff base should therefore be unchanged.
    assert "new route" in detail["memex"]["content"]
    assert detail["prev_memex"]["content"] == detail["memex"]["content"]


def test_query_cycle_diff_base_uses_cycle_start_when_memex_moves(tmp_path):
    from uuid_extensions import uuid7

    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        baseline = datetime(2026, 4, 10, 10, 0, tzinfo=UTC)
        cycle_start = datetime(2026, 4, 10, 10, 4, tzinfo=UTC)
        moved_at = datetime(2026, 4, 10, 10, 5, tzinfo=UTC)
        cycle_end = datetime(2026, 4, 10, 10, 6, tzinfo=UTC)
        db._conn.execute(
            "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
            "VALUES (?, ?, ?, '[\"__memex__\"]', ?, 1)",
            (str(uuid7()), user_id, "# MEMEX\n\n- baseline\n", baseline.isoformat()),
        )
        db._conn.execute(
            "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
            "VALUES (?, ?, ?, '[\"__memex__\"]', ?, 1)",
            (str(uuid7()), user_id, "# MEMEX\n\n- baseline\n- new route\n", moved_at.isoformat()),
        )
        cycle_id = db.insert_cycle_record(user_id, model="pi")
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed', memex_updated = 1 WHERE id = ?",
            (cycle_start.isoformat(), cycle_end.isoformat(), cycle_id),
        )
        db._conn.commit()

    detail = query_cycle(str(db_path), user_id, cycle_id)
    assert detail is not None
    assert detail["cycle"]["memex_moved"] is True
    assert "new route" in detail["memex"]["content"]
    assert "new route" not in detail["prev_memex"]["content"]


def test_query_cycle_reports_row_change_without_content_movement(tmp_path):
    from uuid_extensions import uuid7

    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    with SykeDB(db_path) as db:
        first = datetime(2026, 4, 10, 10, 0, tzinfo=UTC)
        second = datetime(2026, 4, 10, 10, 5, tzinfo=UTC)
        content = "# MEMEX\n\nsame content\n"
        db._conn.execute(
            "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
            "VALUES (?, ?, ?, '[\"__memex__\"]', ?, 0)",
            (str(uuid7()), user_id, content, first.isoformat()),
        )
        db._conn.execute(
            "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
            "VALUES (?, ?, ?, '[\"__memex__\"]', ?, 0)",
            (str(uuid7()), user_id, content, second.isoformat()),
        )
        cycle_id = db.insert_cycle_record(user_id, model="pi")
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, "
            "status = 'completed', memex_updated = 1 WHERE id = ?",
            (
                (second - timedelta(minutes=1)).isoformat(),
                (second + timedelta(minutes=1)).isoformat(),
                cycle_id,
            ),
        )
        db._conn.commit()

    detail = query_cycle(str(db_path), user_id, cycle_id)
    assert detail is not None
    assert detail["cycle"]["memex_row_changed"] is True
    assert detail["cycle"]["memex_content_moved"] is False
    assert detail["cycle"]["memex_moved"] is False
    assert detail["cycle"]["memex_written"] is False
    assert detail["prev_memex"]["content"] == detail["memex"]["content"]


def test_query_cycle_recovers_memory_touches_from_trace_extras_and_output(tmp_path):
    from uuid_extensions import uuid7

    from syke.trace_store import persist_rollout_trace

    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    cycle_start = datetime(2026, 4, 10, 10, 0, tzinfo=UTC)
    cycle_end = datetime(2026, 4, 10, 10, 5, tzinfo=UTC)
    memex_row_id = "06a0496e-2921-7847-8000-27a54d9e8508"

    with SykeDB(db_path) as db:
        for memory_id in ("mem_alpha", "mem_beta"):
            db._conn.execute(
                "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
                "VALUES (?, ?, ?, '[]', ?, 1)",
                (
                    memory_id,
                    user_id,
                    f"{memory_id} content",
                    (cycle_start - timedelta(days=1)).isoformat(),
                ),
            )
        db._conn.execute(
            "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (
                memex_row_id,
                user_id,
                "canonical memex projection",
                json.dumps(["__memex__"]),
                cycle_end.isoformat(),
            ),
        )
        cycle_id = db.insert_cycle_record(user_id, model="pi")
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed' WHERE id = ?",
            (cycle_start.isoformat(), cycle_end.isoformat(), cycle_id),
        )
        persist_rollout_trace(
            db=db,
            user_id=user_id,
            run_id=str(uuid7()),
            kind="synthesis",
            started_at=cycle_start,
            completed_at=cycle_end,
            status="completed",
            output_text="Synthesis cycle complete.\n\nUpdated:\n- `mem_beta`\n- `MEMEX.md`",
            runtime={"model": "gpt-5.4", "num_turns": 3},
            extras={"memory_touched_ids": ["mem_alpha", "__memex__", memex_row_id]},
        )
        db._conn.commit()

    detail = query_cycle(str(db_path), user_id, cycle_id)
    assert detail is not None
    assert "memory_ops" not in detail
    assert detail["memory_touches"]["from_trace_extras"] == [
        "mem_alpha",
        "__memex__",
        memex_row_id,
    ]
    assert detail["memory_touches"]["from_trace"] == ["mem_beta", "MEMEX.md"]
    assert detail["memory_touches"]["ids"] == ["mem_alpha", "mem_beta"]
    assert detail["memory_touches"]["active_ids"] == ["mem_alpha", "mem_beta"]
    assert memex_row_id not in detail["memory_touches"]["ids"]
    assert detail["cycle"]["memory_touched_count"] == 2

    timeline = query_timeline(
        str(db_path),
        user_id,
        (cycle_end + timedelta(minutes=1)).isoformat(),
        minutes=30,
    )
    event = next(e for e in timeline["events"] if e["kind"] == "cycle")
    assert event["memory_touched_count"] == 2


def test_query_cycle_memory_snapshot_includes_rows_superseded_after_boundary(tmp_path):
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    before = datetime(2026, 4, 10, 9, 0, tzinfo=UTC)
    boundary = datetime(2026, 4, 10, 10, 0, tzinfo=UTC)
    after = datetime(2026, 4, 10, 11, 0, tzinfo=UTC)

    with SykeDB(db_path) as db:
        db._conn.execute(
            """INSERT INTO memories
               (id, user_id, content, source_event_ids, created_at, superseded_by, active)
               VALUES (?, ?, ?, '[]', ?, ?, 0)""",
            ("mem_old", user_id, "old content", before.isoformat(), "mem_new"),
        )
        db._conn.execute(
            """INSERT INTO memories
               (id, user_id, content, source_event_ids, created_at, active)
               VALUES (?, ?, ?, '[]', ?, 1)""",
            ("mem_new", user_id, "new content", after.isoformat()),
        )
        cycle_id = db.insert_cycle_record(user_id, model="pi")
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed' WHERE id = ?",
            ((boundary - timedelta(minutes=1)).isoformat(), boundary.isoformat(), cycle_id),
        )
        db._conn.commit()

    detail = query_cycle(str(db_path), user_id, cycle_id)
    assert detail is not None
    memory_ids = [m["id"] for m in detail["memories"]]
    assert "mem_old" in memory_ids
    assert "mem_new" not in memory_ids


def test_query_cycle_returns_full_output_text_without_truncation(tmp_path):
    db_path, user_id = _seed_db(tmp_path)
    end_iso = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    t = query_timeline(str(db_path), user_id, end_iso, minutes=7 * 24 * 60)
    cycle = next(e for e in t["events"] if e["kind"] == "cycle")
    long_output = "x" * 50000

    with SykeDB(db_path) as db:
        completed_at = db._conn.execute(
            "SELECT completed_at FROM cycle_records WHERE id = ?",
            (cycle["id"],),
        ).fetchone()["completed_at"]
        completed_dt = datetime.fromisoformat(completed_at)

        from uuid_extensions import uuid7

        from syke.trace_store import persist_rollout_trace

        persist_rollout_trace(
            db=db,
            user_id=user_id,
            run_id=str(uuid7()),
            kind="synthesis",
            started_at=completed_dt,
            completed_at=completed_dt,
            status="completed",
            output_text=long_output,
            runtime={"model": "gpt-5.4"},
        )

    detail = query_cycle(str(db_path), user_id, cycle["id"])
    assert detail is not None
    assert detail["trace"] is not None
    assert detail["trace"]["output_text"] == long_output


def test_query_cycle_includes_failed_trace_error(tmp_path):
    db_path, user_id = _seed_db(tmp_path)
    end_iso = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    t = query_timeline(str(db_path), user_id, end_iso, minutes=7 * 24 * 60)
    cycle = next(e for e in t["events"] if e["kind"] == "cycle")
    error = "Pi runtime failed: No Pi model is configured"

    with SykeDB(db_path) as db:
        completed_at = db._conn.execute(
            "SELECT completed_at FROM cycle_records WHERE id = ?",
            (cycle["id"],),
        ).fetchone()["completed_at"]
        completed_dt = datetime.fromisoformat(completed_at)

        from uuid_extensions import uuid7

        from syke.trace_store import persist_rollout_trace

        persist_rollout_trace(
            db=db,
            user_id=user_id,
            run_id=str(uuid7()),
            kind="synthesis",
            started_at=completed_dt,
            completed_at=completed_dt,
            status="failed",
            error=error,
            runtime={"model": "pi"},
        )

    detail = query_cycle(str(db_path), user_id, cycle["id"])
    assert detail is not None
    assert detail["trace"] is not None
    assert detail["trace"]["status"] == "failed"
    assert detail["trace"]["error"] == error


def test_query_ask_returns_full_output_text_without_truncation(tmp_path):
    db_path, user_id = _seed_db(tmp_path)
    long_output = "y" * 50000

    with SykeDB(db_path) as db:
        db._conn.execute(
            "UPDATE rollout_traces SET output_text = ? WHERE user_id = ? AND kind = 'ask'",
            (long_output, user_id),
        )
        db._conn.commit()

    end_iso = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    t = query_timeline(str(db_path), user_id, end_iso, minutes=7 * 24 * 60)
    ask = next(e for e in t["events"] if e["kind"] == "ask")
    detail = query_ask(str(db_path), user_id, ask["id"])
    assert detail is not None
    assert detail["ask"]["input_text"] == "What is syke?"
    assert detail["ask"]["output_text"] == long_output


def test_query_cycle_decodes_legacy_bytes_memex(tmp_path):
    """Some legacy memex rows were written as BLOBs into the TEXT column.
    sqlite returns them as bytes; the API must coerce to str so they don't
    leak into JSON as `b'...'` literals.
    """
    db_path, user_id = _seed_db(tmp_path)
    # Replace the most recent memex row's content with a bytes-typed value
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE memories SET content = ? WHERE source_event_ids = ? "
        "AND id = (SELECT id FROM memories WHERE source_event_ids = ? "
        "ORDER BY created_at DESC LIMIT 1)",
        (
            b"# MEMEX\n\nbyte-typed content with unicode \xc2\xb7 dot\n",
            '["__memex__"]',
            '["__memex__"]',
        ),
    )
    conn.commit()
    conn.close()

    end_iso = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    t = query_timeline(str(db_path), user_id, end_iso, minutes=7 * 24 * 60)
    cycle = next(e for e in t["events"] if e["kind"] == "cycle")
    detail = query_cycle(str(db_path), user_id, cycle["id"])
    assert detail is not None
    content = detail["memex"]["content"]
    assert isinstance(content, str)
    # Must not contain the Python bytes-repr escape from str(bytes)
    assert "b'" not in content[:5]
    # Must round-trip the unicode middle-dot (U+00B7) correctly
    assert "·" in content


def test_query_log_tail_handles_missing_file(tmp_path, monkeypatch):
    fake = tmp_path / "nope.log"
    monkeypatch.setattr(web_mod, "DAEMON_LOG_PATH", fake)
    out = query_log_tail(50)
    assert out["exists"] is False
    assert out["lines"] == []


def test_query_log_tail_returns_last_n_lines(tmp_path, monkeypatch):
    log = tmp_path / "daemon.log"
    log.write_text("\n".join(f"line {i}" for i in range(500)))
    monkeypatch.setattr(web_mod, "DAEMON_LOG_PATH", log)
    out = query_log_tail(20)
    assert out["exists"] is True
    assert len(out["lines"]) == 20
    assert out["lines"][-1] == "line 499"


# ─── End-to-end through HTTP ─────────────────────────────────────────────────


def test_timeline_endpoint_round_trip(tmp_path, monkeypatch):
    with _running_server(tmp_path, monkeypatch) as (_, _, port):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/timeline?days=7", timeout=2) as r:
            payload = json.loads(r.read())
            assert payload["window"]["days"] == 7
            assert payload["count"] >= 1
            assert all("kind" in e for e in payload["events"])


def test_timeline_endpoint_handles_unencoded_plus_in_end_query_param(tmp_path, monkeypatch):
    db_path = tmp_path / "syke.db"
    user_id = "test_user"
    from uuid_extensions import uuid7

    with SykeDB(db_path) as db:
        cycle_id = db.insert_cycle_record(user_id, model="pi")
        boundary = datetime(2026, 5, 12, 17, 15, tzinfo=UTC)
        db._conn.execute(
            "UPDATE cycle_records SET started_at = ?, completed_at = ?, status = 'completed' "
            "WHERE id = ?",
            (boundary.isoformat(), boundary.isoformat(), cycle_id),
        )
        db._conn.execute(
            "INSERT INTO memories (id, user_id, content, source_event_ids, created_at, active) "
            "VALUES (?, ?, ?, '[\"__memex__\"]', ?, 1)",
            (str(uuid7()), user_id, "# memex\n", boundary.isoformat()),
        )
        db._conn.execute("DELETE FROM rollout_traces")
        db._conn.commit()

    html_path = tmp_path / "index.html"
    html_path.write_text("<!doctype html><html><body>ok</body></html>")
    port = _free_port()
    monkeypatch.setenv("SYKE_DB", str(db_path))
    srv = SykeWebServer(user_id, port, html_path)
    assert srv.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/timeline?end=2026-05-12T18:00:00+00:00&minutes=180",
            timeout=2,
        ) as r:
            payload = json.loads(r.read())
            assert payload["count"] == 1
            assert payload["events"][0]["kind"] == "cycle"
            assert payload["events"][0]["id"] == cycle_id
    finally:
        srv.stop()


def test_unknown_route_returns_404(tmp_path, monkeypatch):
    with _running_server(tmp_path, monkeypatch, html="<!doctype html>") as (_, _, port):
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/nope", timeout=2)
        assert exc.value.code == 404
