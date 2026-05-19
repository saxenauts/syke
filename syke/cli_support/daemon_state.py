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
            "keeps_syncing": True,
            "keeps_daemon_alive": True,
            "serves_timeline_while_idle": True,
            "restart_policy": "RunAtLoad + KeepAlive",
            "detail": "launchd restarts Syke if the daemon exits unexpectedly.",
        }
    if system == "Linux":
        return {
            "manager": "systemd",
            "keeps_syncing": True,
            "keeps_daemon_alive": True,
            "serves_timeline_while_idle": True,
            "restart_policy": "Restart=always",
            "detail": "systemd restarts Syke if the daemon exits unexpectedly.",
        }
    return {
        "manager": "manual",
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


def daemon_payload() -> dict[str, object]:
    system = platform.system()
    registered, registration = _daemon_registration_state(system)
    process = daemon_process_state()
    running = bool(process.get("running"))
    pid = process.get("pid")
    payload: dict[str, object] = {
        "running": False,
        "registered": registered,
        "pid": pid,
        "detail": "not running",
        "persistence": daemon_persistence_payload(system),
    }

    if system == "Darwin" and registration is not None:
        launchd = registration
        if registered:
            payload["registered"] = True
            payload["stale"] = bool(launchd.get("stale"))
            payload["stale_reasons"] = cast(list[str], launchd.get("stale_reasons") or [])
            payload["last_exit_status"] = launchd.get("last_exit_status")
            payload["launcher_path"] = launchd.get("program_path")
            if running and pid is not None:
                payload["running"] = True
                source = process.get("source") or "process"
                payload["detail"] = f"launchd registered, PID {pid} ({source})"
            elif launchd.get("stale"):
                payload["detail"] = "launchd stale: " + "; ".join(
                    cast(list[str], launchd.get("stale_reasons") or [])
                )
            else:
                exit_status = launchd.get("last_exit_status")
                if exit_status is None:
                    exit_status = "?"
                payload["detail"] = f"launchd registered (last exit: {exit_status})"
            return payload

    if system == "Linux" and registration is not None:
        manager = str(registration.get("manager") or "systemd")
        payload["manager"] = manager
        if registered:
            payload["registered"] = True
            if manager == "systemd":
                payload["stale"] = bool(registration.get("stale"))
                payload["stale_reasons"] = cast(
                    list[str], registration.get("stale_reasons") or []
                )
                payload["last_exit_status"] = registration.get("last_exit_status")
                payload["launcher_path"] = registration.get("program_path")
                payload["unit_path"] = registration.get("unit_path")
                payload["active_state"] = registration.get("active_state")
                payload["sub_state"] = registration.get("sub_state")
                if running and pid is not None:
                    payload["running"] = True
                    source = process.get("source") or "process"
                    payload["detail"] = f"systemd registered, PID {pid} ({source})"
                elif registration.get("stale"):
                    payload["detail"] = "systemd stale: " + "; ".join(
                        cast(list[str], registration.get("stale_reasons") or [])
                    )
                else:
                    active = registration.get("active_state") or "unknown"
                    sub = registration.get("sub_state") or "unknown"
                    payload["detail"] = f"systemd registered ({active}/{sub})"
                return payload
            if manager == "cron":
                payload["detail"] = "legacy cron registered (scheduled sync only)"
                return payload

    if running and pid is not None:
        payload["running"] = True
        payload["detail"] = f"PID {pid}"
    elif registered:
        payload["detail"] = "daemon registered"
    return payload


def daemon_readiness_snapshot(user_id: str) -> dict[str, object]:
    system = platform.system()
    registered, registration = _daemon_registration_state(system)
    process = daemon_process_state()
    running = bool(process.get("running"))
    pid = process.get("pid")
    snapshot: dict[str, object] = {
        "platform": system,
        "running": running,
        "pid": pid,
        "process_source": process.get("source"),
        "ipc": daemon_ipc_status(user_id),
        "registered": registered,
        "registration": registration,
        "persistence": daemon_persistence_payload(system),
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
