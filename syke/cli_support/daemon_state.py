"""Daemon state helpers for the Syke CLI."""

from __future__ import annotations

import platform
import time
from typing import cast

from syke.daemon.daemon import cron_is_running, daemon_process_state, launchd_metadata
from syke.daemon.ipc import daemon_ipc_status


def _daemon_registration_state(system: str) -> tuple[bool, dict[str, object] | None]:
    if system == "Darwin":
        launchd = launchd_metadata()
        return bool(launchd.get("registered")), launchd
    registered, _ = cron_is_running()
    return registered, None


def daemon_payload() -> dict[str, object]:
    system = platform.system()
    registered, launchd = _daemon_registration_state(system)
    process = daemon_process_state()
    running = bool(process.get("running"))
    pid = process.get("pid")
    payload: dict[str, object] = {
        "running": False,
        "registered": registered,
        "pid": pid,
        "detail": "not running",
    }

    if system == "Darwin" and launchd is not None:
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

    if running and pid is not None:
        payload["running"] = True
        payload["detail"] = f"PID {pid}"
    elif registered:
        payload["detail"] = "cron registered"
    return payload


def daemon_readiness_snapshot(user_id: str) -> dict[str, object]:
    system = platform.system()
    registered, _ = _daemon_registration_state(system)
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
    }

    return snapshot


def wait_for_daemon_startup(user_id: str, *, timeout_seconds: float = 20.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    snapshot = daemon_readiness_snapshot(user_id)
    while time.monotonic() < deadline:
        snapshot = daemon_readiness_snapshot(user_id)
        if snapshot.get("platform") == "Darwin":
            ipc = cast(dict[str, object], snapshot["ipc"])
            if snapshot.get("running") and ipc.get("ok"):
                break
        elif snapshot.get("registered"):
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
