from __future__ import annotations

from syke.cli_support import daemon_state, setup_support


def test_daemon_payload_reports_systemd_registration_on_linux(monkeypatch) -> None:
    monkeypatch.setattr(daemon_state.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        daemon_state,
        "daemon_process_state",
        lambda: {"running": False, "pid": None, "source": "none"},
    )
    monkeypatch.setattr(
        daemon_state,
        "systemd_metadata",
        lambda: {
            "manager": "systemd",
            "registered": True,
            "stale": False,
            "active_state": "inactive",
            "sub_state": "dead",
            "unit_path": "/tmp/syke-daemon.service",
        },
    )
    monkeypatch.setattr(daemon_state, "cron_is_running", lambda: (False, None))

    payload = daemon_state.daemon_payload()

    assert payload["registered"] is True
    assert payload["manager"] == "systemd"
    assert payload["detail"] == "systemd registered (inactive/dead)"


def test_daemon_readiness_snapshot_uses_systemd_registration_on_linux(monkeypatch) -> None:
    monkeypatch.setattr(daemon_state.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        daemon_state,
        "daemon_process_state",
        lambda: {"running": False, "pid": None, "source": "none"},
    )
    monkeypatch.setattr(
        daemon_state,
        "systemd_metadata",
        lambda: {"manager": "systemd", "registered": True, "active_state": "active"},
    )
    monkeypatch.setattr(daemon_state, "cron_is_running", lambda: (False, None))
    monkeypatch.setattr(
        daemon_state,
        "daemon_ipc_status",
        lambda _user_id: {
            "ok": False,
            "supported": True,
            "socket_present": False,
            "reachable": False,
            "socket_path": "/tmp/daemon.sock",
            "detail": "missing",
        },
    )

    snapshot = daemon_state.daemon_readiness_snapshot("test_user")

    assert snapshot["platform"] == "Linux"
    assert snapshot["registered"] is True
    assert snapshot["registration"]["manager"] == "systemd"


def test_setup_daemon_viability_uses_systemd_on_linux(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(
        setup_support,
        "daemon_payload",
        lambda: {"running": False, "registered": False, "detail": "not running"},
    )
    monkeypatch.setattr(
        "syke.daemon.daemon.systemd_user_available",
        lambda: (True, "systemd user manager available"),
    )

    payload = setup_support.setup_daemon_viability_payload()

    assert payload["platform"] == "Linux"
    assert payload["installable"] is True
    assert payload["detail"] == "systemd user service available"
    assert payload["persistence"]["manager"] == "systemd"
