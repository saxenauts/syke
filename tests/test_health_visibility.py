from __future__ import annotations

from syke.health import runtime_health, signals


def test_runtime_health_includes_file_logging(db, user_id, monkeypatch) -> None:
    monkeypatch.setattr(
        "syke.metrics.runtime_metrics_status",
        lambda _user_id: {
            "file_logging": {"ok": True, "detail": "File logging writable"},
            "trace_store": {"ok": True, "detail": "Trace store writable"},
        },
    )
    monkeypatch.setattr("syke.daemon.daemon.is_running", lambda: (False, None))
    monkeypatch.setattr(
        "syke.daemon.ipc.daemon_ipc_status",
        lambda _user_id: {
            "socket_path": "/tmp/daemon.sock",
            "socket_present": False,
            "ok": False,
            "detail": "socket not found",
        },
    )

    health = runtime_health(db, user_id)
    assert health["file_logging_enabled"] is True


def test_signals_include_runtime_visibility_warnings(db, user_id, monkeypatch) -> None:
    monkeypatch.setattr(
        "syke.metrics.runtime_metrics_status",
        lambda _user_id: {
            "file_logging": {
                "ok": False,
                "detail": "File logging disabled: Operation not permitted",
            },
            "trace_store": {
                "ok": False,
                "detail": "Trace store disabled: Read-only file system",
            },
        },
    )
    monkeypatch.setattr("syke.daemon.daemon.is_running", lambda: (True, 1234))
    monkeypatch.setattr(
        "syke.daemon.ipc.daemon_ipc_status",
        lambda _user_id: {
            "socket_path": "/tmp/daemon.sock",
            "socket_present": False,
            "ok": False,
            "detail": "socket not found",
        },
    )

    result = signals(db, user_id)
    signal_types = {item["type"] for item in result}

    assert "file_logging_disabled" in signal_types
    assert "trace_store_disabled" in signal_types
    assert "daemon_ipc_unavailable" in signal_types
