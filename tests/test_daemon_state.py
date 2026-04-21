from __future__ import annotations

from syke.cli_support import daemon_state


def test_daemon_payload_reports_cron_registration_on_non_darwin(monkeypatch) -> None:
    monkeypatch.setattr(daemon_state.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        daemon_state,
        "daemon_process_state",
        lambda: {"running": False, "pid": None, "source": "none"},
    )
    monkeypatch.setattr(daemon_state, "cron_is_running", lambda: (True, None))

    payload = daemon_state.daemon_payload()

    assert payload["registered"] is True
    assert payload["detail"] == "cron registered"


def test_daemon_readiness_snapshot_uses_cron_registration_on_non_darwin(monkeypatch) -> None:
    monkeypatch.setattr(daemon_state.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        daemon_state,
        "daemon_process_state",
        lambda: {"running": False, "pid": None, "source": "none"},
    )
    monkeypatch.setattr(daemon_state, "cron_is_running", lambda: (True, None))
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
