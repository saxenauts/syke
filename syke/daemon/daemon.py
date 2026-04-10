"""Background sync daemon."""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import TextIO, cast

from uuid_extensions import uuid7

from syke.config import DAEMON_INTERVAL

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

logger = logging.getLogger(__name__)

PIDFILE = Path(os.path.expanduser("~/.config/syke/daemon.pid"))
LOCKFILE = Path(os.path.expanduser("~/.config/syke/daemon.lock"))


_TAG_MAP: dict[str, str] = {
    "syke.sync": "SYNC",
    "syke.runtime.workspace": "WKSP",
    "syke.runtime.psyche_md": "WKSP",
    "syke.runtime": "PI",
    "syke.llm.pi_client": "PI",
    "syke.llm.pi_runtime": "PI",
    "syke.llm.backends.pi_synthesis": "SYNTH",
    "syke.llm.backends.pi_ask": "ASK",
    "syke.metrics": "COST",
    "syke.daemon.metrics": "COST",
    "syke.observe": "OBS",
    "syke.distribution": "DIST",
    "syke.memory": "MEM",
    "syke.config": "CONF",
}


class DaemonFormatter(logging.Formatter):
    """Symmetric daemon log format: ``2026-04-03 00:52:08 TAG   message``."""

    def format(self, record: logging.LogRecord) -> str:
        tag = getattr(record, "tag", None)
        if not tag:
            name = record.name
            for prefix in sorted(_TAG_MAP, key=len, reverse=True):
                if name == prefix or name.startswith(prefix + "."):
                    tag = _TAG_MAP[prefix]
                    break
            else:
                tag = "LOG"
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        return f"{ts} {tag:<5} {record.getMessage()}"


LAUNCHD_LABEL = "com.syke.daemon"
PLIST_PATH = Path(os.path.expanduser("~/Library/LaunchAgents")) / f"{LAUNCHD_LABEL}.plist"
LOG_PATH = Path(os.path.expanduser("~/.config/syke/daemon.log"))


class SykeDaemon:
    def __init__(self, user_id: str, interval: int = DAEMON_INTERVAL):
        self.user_id = user_id
        self.interval = interval
        self.running = True
        self._stop_event = threading.Event()
        self._db = None
        self._pi_runtime = None
        self._ipc_server = None
        self._runtime_lock = threading.Lock()
        self._lock_handle: TextIO | None = None

    def run(self) -> None:
        """Main daemon loop — blocks until signal."""
        # Install DaemonFormatter so every line in daemon.log (stdout captured
        # by launchd) has the same ``YYYY-MM-DD HH:MM:SS TAG   msg`` format.
        syke_logger = logging.getLogger("syke")
        for h in syke_logger.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setFormatter(DaemonFormatter())

        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        try:
            self._lock_handle = _acquire_daemon_lock()
        except DaemonInstanceLocked:
            logger.info(
                "user=%s duplicate daemon instance blocked", self.user_id, extra={"tag": "START"}
            )
            return
        _write_pid()
        logger.info(
            "user=%s interval=%ss pid=%s",
            self.user_id,
            self.interval,
            os.getpid(),
            extra={"tag": "START"},
        )

        try:
            from syke.config import user_syke_db_path
            from syke.db import SykeDB

            self._db = SykeDB(user_syke_db_path(self.user_id))
            self._db.initialize()

            # Start Pi runtime if configured
            self._start_pi_runtime()
            self._start_ipc_server()

            while self.running and not self._stop_event.is_set():
                self._ensure_process_markers()
                cycle_failed = False
                try:
                    self._daemon_cycle(self._db)
                except Exception as exc:
                    cycle_failed = True
                    logger.error("cycle failed: %s", exc, extra={"tag": "ERROR"})
                wait_seconds = min(self.interval, 5) if cycle_failed else self.interval
                if self._stop_event.wait(wait_seconds):
                    break
        finally:
            self._stop_ipc_server()
            self._stop_pi_runtime()
            if self._db is not None:
                self._db.close()
                self._db = None
            _remove_pid()
            _release_daemon_lock(self._lock_handle)
            self._lock_handle = None
            logger.info("daemon stopped", extra={"tag": "STOP"})

    def stop(self) -> None:
        self.running = False
        self._stop_event.set()

    def _daemon_cycle(self, db) -> None:
        run_id = str(uuid7())
        started_at = datetime.now(UTC)
        health: dict[str, object] | None = None
        total_new = 0
        synced: list[str] = []
        synthesis_result: dict[str, object] | None = None
        cycle_error: str | None = None

        try:
            health = self._health_check()
            self._heal(health)
            total_new, synced = 0, []
            synthesis_result = self._synthesize(db, total_new)
            if isinstance(synthesis_result, dict) and synthesis_result.get("status") == "failed":
                cycle_error = str(synthesis_result.get("error") or "synthesis failed")
            else:
                self._distribute(db, synthesis_result)
        except Exception as exc:
            cycle_error = str(exc) or exc.__class__.__name__
            raise
        finally:
            try:
                from syke.trace_store import persist_rollout_trace

                persist_rollout_trace(
                    db=db,
                    user_id=self.user_id,
                    run_id=run_id,
                    kind="daemon_cycle",
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                    status="failed" if cycle_error else "completed",
                    error=cycle_error,
                    output_text="",
                    thinking=[],
                    transcript=[],
                    tool_calls=[],
                    metrics={
                        "duration_ms": int((datetime.now(UTC) - started_at).total_seconds() * 1000),
                        "cost_usd": 0.0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                    },
                    runtime={
                        "provider": None,
                        "model": None,
                        "response_id": None,
                        "stop_reason": None,
                        "num_turns": 0,
                        "runtime_reused": None,
                        "transport": "daemon",
                    },
                    extras={
                        "healthy": bool(health.get("healthy", False))
                        if isinstance(health, dict)
                        else None,
                        "sources": synced,
                        "synthesis_status": synthesis_result.get("status")
                        if isinstance(synthesis_result, dict)
                        else None,
                        "synthesis_error": synthesis_result.get("error")
                        if isinstance(synthesis_result, dict)
                        else None,
                        "memex_updated": synthesis_result.get("memex_updated")
                        if isinstance(synthesis_result, dict)
                        else None,
                    },
                )
            except Exception:
                logger.debug("Failed to persist daemon cycle trace", exc_info=True)

    def _health_check(self) -> dict[str, object]:
        from syke.daemon.metrics import run_health_check

        health = run_health_check(self.user_id)
        if not health.get("healthy", False):
            logger.warning("degraded", extra={"tag": "HEALTH"})
        return health

    def _heal(self, health: dict[str, object]) -> None:
        if health.get("healthy", False):
            return
        logger.info("attempted soft recovery", extra={"tag": "HEAL"})

    def _synthesize(self, db, total_new: int) -> dict[str, object]:
        from syke.llm.backends.pi_synthesis import pi_synthesize

        SYNTHESIS_TIMEOUT = 600  # 10 minutes
        if not self._runtime_lock.acquire(timeout=SYNTHESIS_TIMEOUT):
            logger.error(
                "Synthesis lock held for >%ds — possible hang",
                SYNTHESIS_TIMEOUT,
                extra={"tag": "SYNTH"},
            )
            return {"status": "failed", "error": "synthesis timeout (lock contention)"}
        try:
            result = pi_synthesize(db, self.user_id)
        finally:
            self._runtime_lock.release()
        status = result.get("status", "unknown")
        if status == "completed":
            logger.info("completed (+%d)", total_new, extra={"tag": "SYNTH"})
        elif status == "skipped":
            logger.info("skipped", extra={"tag": "SYNTH"})
        else:
            logger.error("failed: %s", result.get("error", "unknown"), extra={"tag": "SYNTH"})
        return result

    def _distribute(self, db, synthesis_result: dict[str, object]) -> None:
        from syke.distribution import refresh_distribution

        if synthesis_result.get("status") == "failed":
            logger.info("skipped (synthesis failed)", extra={"tag": "DIST"})
            return

        memex_changed = bool(synthesis_result.get("memex_updated", False))
        result = refresh_distribution(db, self.user_id, memex_updated=memex_changed)
        if result.memex_path:
            logger.info("memex -> %s", result.memex_path, extra={"tag": "DIST"})
        if result.skill_paths:
            logger.info("skills -> %d", len(result.skill_paths), extra={"tag": "DIST"})

        for warning in result.warnings:
            logger.warning("distribution: %s", warning, extra={"tag": "WARN"})

    def _start_pi_runtime(self) -> None:
        """Start the canonical Pi runtime for daemon-driven synthesis."""
        try:
            from syke.runtime import start_pi_runtime
            from syke.runtime.workspace import SESSIONS_DIR, WORKSPACE_ROOT, initialize_workspace

            initialize_workspace()

            self._pi_runtime = start_pi_runtime(
                workspace_dir=WORKSPACE_ROOT,
                session_dir=SESSIONS_DIR,
            )
            logger.info(
                "runtime started (pid=%s)",
                self._pi_runtime._process.pid if self._pi_runtime._process else "?",
                extra={"tag": "PI"},
            )
        except FileNotFoundError as e:
            logger.error("Pi binary not found: %s", e, extra={"tag": "ERROR"})
        except Exception as e:
            logger.error("Pi runtime failed to start: %s", e, extra={"tag": "ERROR"})

    def _stop_pi_runtime(self) -> None:
        """Stop Pi runtime if running."""
        if self._pi_runtime is not None:
            try:
                from syke.runtime import stop_pi_runtime

                stop_pi_runtime()
                logger.info("runtime stopped", extra={"tag": "PI"})
            except Exception as e:
                logger.error("Pi runtime stop failed: %s", e, extra={"tag": "ERROR"})
            self._pi_runtime = None

    def _start_ipc_server(self) -> None:
        """Start the local ask IPC bridge bound to the daemon's warm runtime."""
        try:
            from syke.daemon.ipc import DaemonIpcServer, socket_path_for_user

            self._ipc_server = DaemonIpcServer(
                self.user_id,
                self._handle_ipc_ask,
                self._handle_ipc_runtime_status,
            )
            if self._ipc_server.start():
                logger.info(
                    "ask server listening at %s",
                    socket_path_for_user(self.user_id),
                    extra={"tag": "IPC"},
                )
        except Exception as e:
            logger.error("IPC server failed to start: %s", e, extra={"tag": "ERROR"})
            self._ipc_server = None

    def _ensure_process_markers(self) -> None:
        expected_pid = str(os.getpid())
        try:
            current_pid = PIDFILE.read_text().strip() if PIDFILE.exists() else None
        except OSError:
            current_pid = None
        if current_pid != expected_pid:
            _write_pid()

        # Auto-recover warm Pi runtime if it died
        if self._pi_runtime is not None:
            try:
                status = self._pi_runtime.status()
                if not status.get("alive"):
                    logger.info("warm runtime dead; restarting", extra={"tag": "PI"})
                    self._stop_pi_runtime()
                    self._start_pi_runtime()
            except Exception:
                logger.info("warm runtime unreachable; restarting", extra={"tag": "PI"})
                self._stop_pi_runtime()
                self._start_pi_runtime()
        elif self._pi_runtime is None:
            logger.info("warm runtime missing; starting", extra={"tag": "PI"})
            self._start_pi_runtime()

        ipc_server = self._ipc_server
        if ipc_server is not None and not ipc_server.socket_path.exists():
            logger.info(
                "socket path missing; rebinding %s", ipc_server.socket_path, extra={"tag": "IPC"}
            )
            try:
                ipc_server.stop()
            except Exception as exc:
                logger.error("IPC server restart cleanup failed: %s", exc, extra={"tag": "ERROR"})
            self._ipc_server = None
            self._start_ipc_server()

    def _stop_ipc_server(self) -> None:
        if self._ipc_server is not None:
            try:
                self._ipc_server.stop()
                logger.info("ask server stopped", extra={"tag": "IPC"})
            except Exception as e:
                logger.error("IPC server stop failed: %s", e, extra={"tag": "ERROR"})
            self._ipc_server = None

    def _handle_ipc_ask(
        self,
        syke_db_path: str,
        question: str,
        on_event,
        timeout: float | None,
    ) -> tuple[str, dict[str, object]]:
        from syke.daemon.ipc import DaemonIpcBusy, socket_path_for_user
        from syke.db import SykeDB
        from syke.llm.backends.pi_ask import pi_ask

        # Foreground asks should not queue behind a long-running synthesis turn.
        # If the shared daemon runtime is currently busy, tell the caller to
        # bypass IPC and use an isolated direct ask runtime instead.
        if not self._runtime_lock.acquire(blocking=False):
            raise DaemonIpcBusy("daemon busy: runtime in use")

        request_db = SykeDB(syke_db_path)
        try:
            return pi_ask(
                request_db,
                self.user_id,
                question,
                on_event=on_event,
                timeout=timeout,
                transport="daemon_ipc",
                transport_details={
                    "daemon_pid": os.getpid(),
                    "ipc_socket_path": str(socket_path_for_user(self.user_id)),
                },
            )
        finally:
            request_db.close()
            self._runtime_lock.release()

    def _handle_ipc_runtime_status(self) -> dict[str, object]:
        runtime = self._pi_runtime
        if runtime is None:
            return {
                "alive": False,
                "busy": False,
                "provider": None,
                "model": None,
                "pid": None,
                "uptime_s": None,
                "binding_error": None,
            }
        try:
            return {
                **cast(dict[str, object], runtime.status()),
                "busy": self._runtime_lock.locked(),
            }
        except Exception as exc:
            return {
                "alive": False,
                "busy": self._runtime_lock.locked(),
                "provider": None,
                "model": None,
                "pid": None,
                "uptime_s": None,
                "binding_error": str(exc),
            }

    def _signal_handler(self, signum: int, frame: object) -> None:
        self.stop()


# --- PID file helpers ---


def _write_pid() -> None:
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(os.getpid()))


class DaemonInstanceLocked(RuntimeError):
    """Raised when another daemon instance already holds the daemon lock."""


def _acquire_daemon_lock() -> TextIO:
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCKFILE.open("a+", encoding="utf-8")
    if fcntl is None:
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        return handle
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise DaemonInstanceLocked("another daemon instance already holds the lock") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def _release_daemon_lock(handle: TextIO | None) -> None:
    if handle is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except OSError:
        pass


def _remove_pid() -> None:
    _unlink_pidfile()


def _unlink_pidfile() -> bool:
    try:
        PIDFILE.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _pid_looks_like_syke(pid: int) -> bool | None:
    try:
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None

    command = result.stdout.strip().lower()
    if not command:
        return None
    return "syke" in command


def is_running() -> tuple[bool, int | None]:
    """Check if daemon is running. Returns (running, pid)."""
    if not PIDFILE.exists():
        return False, None
    try:
        pid = int(PIDFILE.read_text().strip())
    except (ValueError, OSError):
        _unlink_pidfile()
        return False, None
    try:
        os.kill(pid, 0)
        return True, pid
    except PermissionError:
        pid_looks_like_syke = _pid_looks_like_syke(pid)
        if pid_looks_like_syke is False:
            _unlink_pidfile()
            return False, None
        return True, pid
    except OSError:
        _unlink_pidfile()
        return False, None


def stop_daemon() -> bool:
    """Send SIGTERM to running daemon. Returns True if signal sent."""
    running, pid = is_running()
    if not running or pid is None:
        return False
    os.kill(pid, signal.SIGTERM)
    return True


# --- launchd helpers ---


def _is_tcc_protected(path: Path) -> bool:
    """Check if a path is inside a macOS TCC-protected directory."""
    from syke.runtime.locator import is_tcc_protected

    return is_tcc_protected(path)


def generate_plist(
    user_id: str, source_install: bool | None = None, interval: int = DAEMON_INTERVAL
) -> str:
    """Generate macOS LaunchAgent plist XML.

    Prefers the ``syke`` console script from PATH for both pip and source installs.
    Falls back to ``sys.executable -m syke`` with WorkingDirectory only when
    no ``syke`` binary is available on PATH.

    Auth is not baked into the plist. Provider resolution happens at runtime from
    the active Syke auth/config state and environment variables, so launchd keeps
    following the current local configuration.
    """
    from syke.config import _is_source_install
    from syke.runtime.locator import ensure_syke_launcher, resolve_background_syke_runtime

    if source_install is None:
        source_install = _is_source_install()

    log_path = str(LOG_PATH)
    runtime = resolve_background_syke_runtime()
    launcher_path = ensure_syke_launcher(runtime)
    program_args = (
        f"        <string>{launcher_path}</string>\n"
        f"        <string>--user</string>\n"
        f"        <string>{user_id}</string>\n"
        f"        <string>daemon</string>\n"
        f"        <string>run</string>\n"
        f"        <string>--interval</string>\n"
        f"        <string>{interval}</string>"
    )
    working_dir_block = ""

    # Auth is not baked into the plist. Keys or endpoints captured at setup time
    # become stale; runtime provider resolution should always use the current
    # Syke auth store, config, and environment variables instead.
    env_block = ""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{program_args}
    </array>
{working_dir_block}{env_block}    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>
"""


def install_launchd(user_id: str, interval: int = DAEMON_INTERVAL) -> Path:
    """Write plist and load the LaunchAgent. Returns plist path."""
    plist_content = generate_plist(user_id, interval=interval)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_content)
    os.chmod(PLIST_PATH, 0o600)

    # Clear stale registrations before loading the fresh LaunchAgent.
    _clear_launchd_registration()
    subprocess.run(
        ["launchctl", "enable", _launchd_service_target()],
        check=False,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["launchctl", "load", str(PLIST_PATH)],
        check=True,
    )
    return PLIST_PATH


def uninstall_launchd() -> bool:
    """Unload and remove the LaunchAgent. Returns True if removed."""
    had_plist = PLIST_PATH.exists()
    removed = _clear_launchd_registration()
    PLIST_PATH.unlink(missing_ok=True)
    return removed or had_plist


def launchd_status() -> str | None:
    """Check launchctl for our agent. Returns status string or None."""
    import subprocess

    commands = [
        ["launchctl", "print", _launchd_service_target()],
        ["launchctl", "list", LAUNCHD_LABEL],
    ]
    for cmd in commands:
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _launchd_service_target() -> str:
    return f"gui/{os.getuid()}/{LAUNCHD_LABEL}"


def _parse_launchd_program(status: str) -> Path | None:
    patterns = (
        r'"Program"\s*=\s*"([^"]+)"',
        r"^\s*program\s*=\s*(.+?)\s*$",
    )
    for pattern in patterns:
        match = re.search(pattern, status, re.MULTILINE)
        if match is None:
            continue
        program = match.group(1).strip().strip('"')
        if program:
            return Path(os.path.expanduser(program))
    return None


def _parse_launchd_exit_status(status: str) -> int | None:
    patterns = (
        r'"LastExitStatus"\s*=\s*(\d+)',
        r"last exit code = (\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, status, re.MULTILINE)
        if match is None:
            continue
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _parse_launchd_pid(status: str) -> int | None:
    match = re.search(r"^\s*pid = (\d+)\s*$", status, re.MULTILINE)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _parse_launchd_state(status: str) -> str | None:
    match = re.search(r"^\s*state = ([^\s]+)\s*$", status, re.MULTILINE)
    if match is None:
        return None
    state = match.group(1).strip()
    return state or None


def launchd_metadata() -> dict[str, object]:
    """Return structured launchd registration details for the Syke agent."""
    status = launchd_status()
    registered = status is not None
    program_path = _parse_launchd_program(status) if status is not None else None
    service_pid = _parse_launchd_pid(status) if status is not None else None
    service_state = _parse_launchd_state(status) if status is not None else None
    plist_exists = PLIST_PATH.exists()
    launcher_exists = program_path.exists() if program_path is not None else None
    stale_reasons: list[str] = []

    if registered and not plist_exists:
        stale_reasons.append(f"plist missing at {PLIST_PATH}")
    if registered and program_path is not None and not launcher_exists:
        stale_reasons.append(f"launcher missing at {program_path}")

    return {
        "registered": registered,
        "status": status,
        "pid": service_pid,
        "state": service_state,
        "last_exit_status": _parse_launchd_exit_status(status) if status is not None else None,
        "program_path": str(program_path) if program_path is not None else None,
        "plist_exists": plist_exists,
        "launcher_exists": launcher_exists,
        "stale": bool(stale_reasons),
        "stale_reasons": stale_reasons,
    }


def daemon_process_state() -> dict[str, object]:
    """Return best-effort process truth for the daemon."""
    running, pid = is_running()
    if running and pid is not None:
        return {"running": True, "pid": pid, "source": "pidfile"}

    if os.sys.platform == "darwin":
        metadata = launchd_metadata()
        launchd_pid = metadata.get("pid")
        launchd_state = metadata.get("state")
        if (
            metadata.get("registered")
            and launchd_state == "running"
            and isinstance(launchd_pid, int)
        ):
            try:
                os.kill(launchd_pid, 0)
            except OSError:
                pass
            else:
                return {"running": True, "pid": launchd_pid, "source": "launchd"}

    return {"running": False, "pid": pid, "source": "none"}


def _clear_launchd_registration() -> bool:
    """Best-effort removal of stale or active launchd state for the Syke agent."""
    commands: list[list[str]] = []
    if PLIST_PATH.exists():
        commands.append(["launchctl", "unload", str(PLIST_PATH)])
        commands.append(["launchctl", "bootout", f"gui/{os.getuid()}", str(PLIST_PATH)])
    commands.append(["launchctl", "bootout", _launchd_service_target()])
    commands.append(["launchctl", "remove", LAUNCHD_LABEL])
    commands.append(["launchctl", "disable", _launchd_service_target()])

    removed = False
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return removed
        removed = removed or result.returncode == 0
    return removed


# --- cron helpers (Linux/generic) ---

CRON_TAG = "# syke-daemon"


def _build_cron_entry(user_id: str, interval: int = DAEMON_INTERVAL) -> str:
    """Build a crontab line for periodic sync."""
    import shlex

    from syke.runtime.locator import ensure_syke_launcher, resolve_syke_runtime

    syke_bin = ensure_syke_launcher(resolve_syke_runtime())
    log_path = str(LOG_PATH)

    # Convert seconds to minutes for cron (minimum 1 min)
    minutes = max(1, interval // 60)
    # Do NOT bake ANTHROPIC_API_KEY into the crontab — it exposes the key in
    # plaintext in `crontab -l` and creates a stale-key risk if the key rotates.
    # sync reads from ~/.syke/.env or uses Claude Code session auth automatically.
    return (
        f"*/{minutes} * * * * {shlex.quote(str(syke_bin))} --user {shlex.quote(user_id)} "
        f"sync >> {shlex.quote(log_path)} 2>&1 {CRON_TAG}"
    )


def install_cron(user_id: str, interval: int = DAEMON_INTERVAL) -> None:
    """Append a tagged crontab entry for periodic sync."""
    import subprocess

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Read existing crontab
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if r.returncode == 0:
        existing = r.stdout
    else:
        existing = ""

    # Remove any old syke-daemon entry
    lines = [line for line in existing.splitlines() if CRON_TAG not in line]
    lines.append(_build_cron_entry(user_id, interval))

    new_crontab = "\n".join(lines) + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)


def uninstall_cron() -> bool:
    """Remove syke-daemon entry from crontab. Returns True if removed."""
    import subprocess

    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if r.returncode != 0:
        return False

    lines = r.stdout.splitlines()
    filtered = [line for line in lines if CRON_TAG not in line]

    if len(filtered) == len(lines):
        return False  # nothing to remove

    new_crontab = "\n".join(filtered) + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    return True


def cron_is_running() -> tuple[bool, None]:
    """Check if a syke-daemon cron entry exists. Returns (found, None)."""
    import subprocess

    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and CRON_TAG in r.stdout:
            return True, None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False, None


def cron_status() -> str:
    """Get human-readable cron daemon status."""
    found, _ = cron_is_running()
    if found:
        return "[green]Cron job installed[/green] (syke-daemon)"
    return "[dim]No cron job installed[/dim]"


# --- CLI-friendly wrappers (platform-dispatched) ---


def install_and_start(user_id: str, interval: int = DAEMON_INTERVAL) -> None:
    """Install and start the daemon (launchd on macOS, cron elsewhere)."""
    import sys

    if sys.platform == "darwin":
        install_launchd(user_id, interval=interval)
    else:
        install_cron(user_id, interval=interval)


def stop_and_unload() -> None:
    """Stop and uninstall the daemon."""
    import sys

    running, pid = is_running()
    if sys.platform == "darwin":
        uninstall_launchd()
    else:
        uninstall_cron()

    if running and pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            still_running, _ = is_running()
            if not still_running:
                break
            time.sleep(0.1)

        still_running, current_pid = is_running()
        if still_running and current_pid is not None:
            try:
                os.kill(current_pid, signal.SIGKILL)
            except OSError:
                pass
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                final_running, _ = is_running()
                if not final_running:
                    break
                time.sleep(0.1)


def get_status() -> str:
    """Get human-readable daemon status."""
    import sys

    running, pid = is_running()

    if sys.platform == "darwin":
        status = launchd_status()
        if running and pid:
            msg = f"[green]Daemon is running[/green] (PID {pid})"
            if status:
                msg += f"\n\nLaunchAgent status:\n{status}"
            return msg
        elif status:
            return f"[yellow]LaunchAgent installed but daemon not running[/yellow]\n\nLaunchAgent status:\n{status}"
        else:
            return "[dim]Daemon not running[/dim]"
    else:
        cron_found, _ = cron_is_running()
        if running and pid:
            msg = f"[green]Daemon is running[/green] (PID {pid})"
            if cron_found:
                msg += "\n\nCron job: installed"
            return msg
        elif cron_found:
            return cron_status()
        else:
            return "[dim]Daemon not running[/dim]"
