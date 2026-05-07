"""Tests for the local read-only timeline web server."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from syke.daemon import web as web_mod
from syke.daemon.web import (
    SykeWebServer,
    _extract_host,
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


# ─── Host validation (DNS rebinding defense) ────────────────────────────────


def test_extract_host_strips_port():
    assert _extract_host("localhost:8765") == "localhost"
    assert _extract_host("127.0.0.1:9999") == "127.0.0.1"
    assert _extract_host("[::1]:8765") == "[::1]"
    assert _extract_host("evil.com") == "evil.com"
    assert _extract_host("") == ""
    assert _extract_host(None) == ""


def test_server_rejects_non_localhost_host(tmp_path, monkeypatch):
    db_path, user_id = _seed_db(tmp_path)
    monkeypatch.setenv("SYKE_DB", str(db_path))

    html = tmp_path / "index.html"
    html.write_text("<!doctype html><h1>ok</h1>")
    port = _free_port()
    srv = SykeWebServer(user_id, port, html)
    assert srv.start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"Host": "evil.com"})
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(req, timeout=2)
        assert excinfo.value.code == 403
    finally:
        srv.stop()


def test_server_accepts_localhost_host(tmp_path, monkeypatch):
    db_path, user_id = _seed_db(tmp_path)
    monkeypatch.setenv("SYKE_DB", str(db_path))

    html = tmp_path / "index.html"
    html.write_text("<!doctype html><h1>ok</h1>")
    port = _free_port()
    srv = SykeWebServer(user_id, port, html)
    assert srv.start()
    try:
        for host in ("localhost", "127.0.0.1", "[::1]"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/", headers={"Host": f"{host}:{port}"}
            )
            with urllib.request.urlopen(req, timeout=2) as r:
                assert r.status == 200
                assert b"ok" in r.read()
    finally:
        srv.stop()


def test_server_writes_security_headers(tmp_path, monkeypatch):
    db_path, user_id = _seed_db(tmp_path)
    monkeypatch.setenv("SYKE_DB", str(db_path))

    html = tmp_path / "index.html"
    html.write_text("<!doctype html><h1>ok</h1>")
    port = _free_port()
    srv = SykeWebServer(user_id, port, html)
    assert srv.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            headers = {k.lower(): v for k, v in r.headers.items()}
            assert "no-store" in headers["cache-control"]
            assert headers["x-content-type-options"] == "nosniff"
            assert "default-src 'self'" in headers["content-security-policy"]
    finally:
        srv.stop()


# ─── Query layer ─────────────────────────────────────────────────────────────


def test_query_health_with_seeded_db(tmp_path):
    db_path, user_id = _seed_db(tmp_path)
    h = query_health(str(db_path), user_id)
    assert h["db_present"] is True
    assert h["last_cycle"] is not None
    assert h["last_completed_cycle"] is not None
    assert h["memex_updated_at"] is not None


def test_query_timeline_returns_cycles_and_asks(tmp_path):
    db_path, user_id = _seed_db(tmp_path)
    end_iso = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    t = query_timeline(str(db_path), user_id, end_iso, days=7)
    kinds = {e["kind"] for e in t["events"]}
    assert "cycle" in kinds
    assert "ask" in kinds
    assert t["count"] == len(t["events"]) >= 2


def test_query_cycle_includes_memex_diff_base_and_memories(tmp_path):
    db_path, user_id = _seed_db(tmp_path)
    end_iso = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    t = query_timeline(str(db_path), user_id, end_iso, days=7)
    cycle = next(e for e in t["events"] if e["kind"] == "cycle")
    detail = query_cycle(str(db_path), user_id, cycle["id"])
    assert detail is not None
    assert detail["memex"]["content"].strip().startswith("# MEMEX")
    # prev_memex content is the older memex chain entry
    assert "new route" in detail["memex"]["content"]
    assert "new route" not in detail["prev_memex"]["content"]


def test_query_ask_returns_input_and_output(tmp_path):
    db_path, user_id = _seed_db(tmp_path)
    end_iso = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    t = query_timeline(str(db_path), user_id, end_iso, days=7)
    ask = next(e for e in t["events"] if e["kind"] == "ask")
    detail = query_ask(str(db_path), user_id, ask["id"])
    assert detail is not None
    assert detail["ask"]["input_text"] == "What is syke?"
    assert detail["ask"]["output_text"] == "A memory system."


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
    db_path, user_id = _seed_db(tmp_path)
    monkeypatch.setenv("SYKE_DB", str(db_path))

    html = tmp_path / "index.html"
    html.write_text("<!doctype html><h1>ok</h1>")
    port = _free_port()
    srv = SykeWebServer(user_id, port, html)
    assert srv.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/timeline?days=7", timeout=2) as r:
            payload = json.loads(r.read())
            assert payload["window"]["days"] == 7
            assert payload["count"] >= 1
            assert all("kind" in e for e in payload["events"])
    finally:
        srv.stop()


def test_unknown_route_returns_404(tmp_path, monkeypatch):
    db_path, user_id = _seed_db(tmp_path)
    monkeypatch.setenv("SYKE_DB", str(db_path))
    html = tmp_path / "index.html"
    html.write_text("<!doctype html>")
    port = _free_port()
    srv = SykeWebServer(user_id, port, html)
    assert srv.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/nope", timeout=2)
        assert exc.value.code == 404
    finally:
        srv.stop()
