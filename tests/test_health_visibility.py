from __future__ import annotations

from syke.health import runtime_health, signals


def test_runtime_health_surfaces_disabled_self_observation(db, user_id, monkeypatch) -> None:
    monkeypatch.setattr(
        "syke.observe.trace.self_observation_status",
        lambda: {
            "enabled": False,
            "disabled_by_env": True,
            "env_var": "SYKE_DISABLE_SELF_OBSERVATION",
            "detail": "Self-observation disabled by SYKE_DISABLE_SELF_OBSERVATION",
        },
    )
    monkeypatch.setattr(
        "syke.metrics.runtime_metrics_status",
        lambda _user_id: {
            "file_logging": {"ok": True, "detail": "File logging writable"},
            "metrics_store": {"ok": True, "detail": "Metrics store writable"},
        },
    )
    monkeypatch.setattr("syke.daemon.daemon.is_running", lambda: (False, None))
    monkeypatch.setattr(
        "syke.daemon.ipc.daemon_ipc_status",
        lambda _user_id: {
            "socket_path": "/tmp/daemon.sock",
            "socket_present": False,
            "ok": False,
            "detail": "socket not found at /tmp/daemon.sock",
        },
    )

    health = runtime_health(db, user_id)

    assert health["self_observation_enabled"] is False
    assert "disabled" in str(health["self_observation_detail"]).lower()


def test_signals_include_runtime_visibility_warnings(db, user_id, monkeypatch) -> None:
    monkeypatch.setattr(
        "syke.observe.trace.self_observation_status",
        lambda: {
            "enabled": False,
            "disabled_by_env": True,
            "env_var": "SYKE_DISABLE_SELF_OBSERVATION",
            "detail": "Self-observation disabled by SYKE_DISABLE_SELF_OBSERVATION",
        },
    )
    monkeypatch.setattr(
        "syke.metrics.runtime_metrics_status",
        lambda _user_id: {
            "file_logging": {
                "ok": False,
                "detail": "File logging disabled: Operation not permitted",
            },
            "metrics_store": {
                "ok": False,
                "detail": "Metrics store disabled: Read-only file system",
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
            "detail": "socket not found at /tmp/daemon.sock",
        },
    )

    result = signals(db, user_id)
    signal_types = {item["type"] for item in result}

    assert "self_observation_disabled" in signal_types
    assert "file_logging_disabled" in signal_types
    assert "metrics_persist_disabled" in signal_types
    assert "daemon_ipc_unavailable" in signal_types
