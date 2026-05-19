"""Daemon state helpers for the Syke CLI."""

from __future__ import annotations

import platform
import time
from typing import cast

from syke.daemon.daemon import (
    cron_is_running,
    daemon_process_state,
    launchd_metadata,
    systemd_metadata,
)
from syke.daemon.ipc import daemon_ipc_status


def daemon_persistence_payload(system: str | None = None) -> dict[str, object]:
    system = system or platform.system()
    if system == "Darwin":
        return {
            "manager": "launchd",
            "manager_scope": "user",
            "keeps_syncing": True,
            "keeps_daemon_alive": True,
            "serves_timeline_while_idle": True,
            "restart_policy": "RunAtLoad + KeepAlive",
            "detail": "launchd restarts Syke if the daemon exits unexpectedly.",
        }
    if system == "Linux":
        return {
            "manager": "systemd",
            "manager_scope": "user",
            "keeps_syncing": True,
            "keeps_daemon_alive": True,
            "serves_timeline_while_idle": True,
            "restart_policy": "Restart=always",
            "requires_linger_for_boot": True,
            "detail": (
                "systemd restarts Syke while the user manager is active; "
                "boot persistence requires loginctl linger."
            ),
        }
    return {
        "manager": "manual",
        "manager_scope": "foreground",
        "keeps_syncing": False,
        "keeps_daemon_alive": False,
        "serves_timeline_while_idle": False,
        "restart_policy": "foreground run only",
        "detail": "run `syke daemon run` manually on this platform.",
    }


def _daemon_registration_state(system: str) -> tuple[bool, dict[str, object] | None]:
    if system == "Darwin":
        launchd = launchd_metadata()
        return bool(launchd.get("registered")), launchd
    if system == "Linux":
        systemd = systemd_metadata()
        if systemd.get("registered"):
            return True, systemd
    registered, _ = cron_is_running()
    if registered:
        return True, {"manager": "cron", "registered": True}
    return False, None


def _default_manager(system: str, registration: dict[str, object] | None) -> str:
    if registration is not None and isinstance(registration.get("manager"), str):
        return cast(str, registration["manager"])
    if system == "Darwin":
        return "launchd"
    if system == "Linux":
        return "systemd"
    return "manual"


def _daemon_lifecycle_state(
    *,
    manager: str,
    running: bool,
    registered: bool,
    stale: bool,
) -> str:
    if running:
        return "running"
    if stale:
        return "stale"
    if manager == "cron" and registered:
        return "legacy_scheduled_sync"
    if registered:
        return "registered"
    return "stopped"


def _service_detail(
    *,
    manager: str,
    running: bool,
    pid: object,
    process_source: object,
    registered: bool,
    stale: bool,
    stale_reasons: list[str],
    registration: dict[str, object] | None,
) -> str:
    if running and pid is not None:
        source = process_source or "process"
        if registered and manager in {"launchd", "systemd"}:
            return f"{manager} registered, PID {pid} ({source})"
        return f"PID {pid}"
    if stale:
        return f"{manager} stale: " + "; ".join(stale_reasons)
    if manager == "cron" and registered:
        return "legacy scheduled sync registered; no background service"
    if registered and manager == "systemd":
        active = registration.get("active_state") if registration else None
        sub = registration.get("sub_state") if registration else None
        return f"systemd registered ({active or 'unknown'}/{sub or 'unknown'})"
    if registered and manager == "launchd":
        exit_status = registration.get("last_exit_status") if registration else None
        if exit_status is None:
            exit_status = "?"
        return f"launchd registered (last exit: {exit_status})"
    if registered:
        return "daemon registered"
    return "not running"


def _build_daemon_payload(
    *,
    system: str,
    registered: bool,
    registration: dict[str, object] | None,
    process: dict[str, object],
) -> dict[str, object]:
    running = bool(process.get("running"))
    pid = process.get("pid")
    process_source = process.get("source")
    manager = _default_manager(system, registration)
    stale = bool(registration.get("stale")) if registration is not None else False
    stale_reasons = (
        cast(list[str], registration.get("stale_reasons") or [])
        if registration is not None
        else []
    )
    state = _daemon_lifecycle_state(
        manager=manager,
        running=running,
        registered=registered,
        stale=stale,
    )
    persistence = daemon_persistence_payload(system)
    detail = _service_detail(
        manager=manager,
        running=running,
        pid=pid,
        process_source=process_source,
        registered=registered,
        stale=stale,
        stale_reasons=stale_reasons,
        registration=registration,
    )

    service: dict[str, object] = {
        "platform": system,
        "manager": manager,
        "state": state,
        "registered": registered,
        "scheduled_only": manager == "cron",
        "running": running,
        "pid": pid,
        "process_source": process_source,
        "stale": stale,
        "stale_reasons": stale_reasons,
        "last_exit_status": registration.get("last_exit_status") if registration else None,
        "launcher_path": registration.get("program_path") if registration else None,
        "unit_path": registration.get("unit_path") if registration else None,
        "active_state": registration.get("active_state") if registration else None,
        "sub_state": registration.get("sub_state") if registration else None,
        "detail": detail,
        "persistence": persistence,
    }

    return {
        "running": running,
        "registered": registered,
        "pid": pid,
        "process_source": process_source,
        "state": state,
        "manager": manager,
        "detail": detail,
        "persistence": persistence,
        "service": service,
        "stale": stale,
        "stale_reasons": stale_reasons,
        "last_exit_status": service["last_exit_status"],
        "launcher_path": service["launcher_path"],
        "unit_path": service["unit_path"],
        "active_state": service["active_state"],
        "sub_state": service["sub_state"],
    }


def daemon_payload() -> dict[str, object]:
    system = platform.system()
    registered, registration = _daemon_registration_state(system)
    process = daemon_process_state()
    return _build_daemon_payload(
        system=system,
        registered=registered,
        registration=registration,
        process=process,
    )


def daemon_readiness_snapshot(user_id: str) -> dict[str, object]:
    system = platform.system()
    registered, registration = _daemon_registration_state(system)
    process = daemon_process_state()
    payload = _build_daemon_payload(
        system=system,
        registered=registered,
        registration=registration,
        process=process,
    )
    snapshot: dict[str, object] = {
        "platform": system,
        "running": payload["running"],
        "pid": payload["pid"],
        "process_source": payload["process_source"],
        "state": payload["state"],
        "manager": payload["manager"],
        "ipc": daemon_ipc_status(user_id),
        "registered": registered,
        "registration": registration,
        "service": payload["service"],
        "persistence": payload["persistence"],
    }

    return snapshot


def wait_for_daemon_startup(user_id: str, *, timeout_seconds: float = 20.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    snapshot = daemon_readiness_snapshot(user_id)
    while time.monotonic() < deadline:
        snapshot = daemon_readiness_snapshot(user_id)
        ipc = cast(dict[str, object], snapshot["ipc"])
        if snapshot.get("running") and ipc.get("ok"):
            break
        time.sleep(0.25)
    return snapshot


def wait_for_daemon_shutdown(user_id: str, *, timeout_seconds: float = 10.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    snapshot = daemon_readiness_snapshot(user_id)
    while time.monotonic() < deadline:
        snapshot = daemon_readiness_snapshot(user_id)
        if not snapshot.get("running") and not snapshot.get("registered"):
            break
        time.sleep(0.25)
    return snapshot
