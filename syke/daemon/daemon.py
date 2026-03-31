"""Background sync daemon."""

from __future__ import annotations

import logging
import os
import signal
import threading
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from syke.config import DAEMON_INTERVAL
from uuid_extensions import uuid7

logger = logging.getLogger(__name__)

PIDFILE = Path(os.path.expanduser("~/.config/syke/daemon.pid"))


def _log(level: str, msg: str) -> None:
    """Write a clean one-liner to stdout (captured by launchd/cron as daemon.log)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {level:<5} {msg}", flush=True)


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
        self._writer = None
        self._sense_watcher = None
        self._observer = None
        self._sqlite_watchers: list[Any] = []
        self._pi_runtime = None
        self._ipc_server = None
        self._runtime_lock = threading.Lock()
        self._watcher_authoritative_sources: set[str] = set()
        self._file_triggered_sources: set[str] = set()
        self._dirty_sources: set[str] = set()
        self._dirty_paths_by_source: dict[str, set[Path]] = {}
        self._dirty_paths_lock = threading.Lock()

    def run(self) -> None:
        """Main daemon loop — blocks until signal."""
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        _write_pid()
        _log("START", f"user={self.user_id} interval={self.interval}s pid={os.getpid()}")

        try:
            from syke.config import user_data_dir, user_syke_db_path
            from syke.db import SykeDB
            from syke.observe.registry import set_dynamic_adapters_dir

            self._db = SykeDB(user_syke_db_path(self.user_id))
            self._db.initialize()

            adapters_dir = user_data_dir(self.user_id) / "adapters"
            set_dynamic_adapters_dir(adapters_dir)

            self._start_sense_services(self._db)

            # Start Pi runtime if configured
            self._start_pi_runtime()
            self._start_ipc_server()

            while self.running and not self._stop_event.is_set():
                self._daemon_cycle(self._db)
                if self._stop_event.wait(self.interval):
                    break
        finally:
            self._stop_ipc_server()
            self._stop_pi_runtime()
            self._stop_sense_services()
            if self._db is not None:
                self._db.close()
                self._db = None
            _remove_pid()
            _log("STOP", "daemon stopped")

    def stop(self) -> None:
        self.running = False
        self._stop_event.set()

    def _cycle_observer(self, db):
        observer_api = import_module("syke.observe.trace")
        if self._observer is not None:
            return observer_api, self._observer, False
        return observer_api, observer_api.SykeObserver(db, self.user_id), True

    def _daemon_cycle(self, db) -> None:
        observer_api, observer, owns_observer = self._cycle_observer(db)
        run_id = str(uuid7())
        started_at = datetime.now(UTC)
        health: dict[str, object] | None = None
        total_new = 0
        synced: list[str] = []
        synthesis_result: dict[str, object] | None = None
        cycle_error: str | None = None

        observer.record(
            observer_api.DAEMON_CYCLE_START,
            {"start_time": started_at.isoformat()},
            run_id=run_id,
        )
        try:
            health = self._health_check()
            observer.record(
                observer_api.HEALTH_CHECK,
                {
                    "healthy": bool(health.get("healthy", False)),
                },
                run_id=run_id,
            )
            if not health.get("healthy", False):
                observer.record(
                    observer_api.HEALING_TRIGGERED,
                    {"reason": "degraded"},
                    run_id=run_id,
                )
            self._heal(health)
            if not health.get("healthy", False):
                observer.record(
                    observer_api.HEALING_COMPLETE,
                    {"status": "attempted"},
                    run_id=run_id,
                )
            total_new, synced = self._reconcile(db)
            synthesis_result = self._synthesize(db, total_new)
            self._distribute(db, synthesis_result)
        except Exception as exc:
            cycle_error = str(exc) or exc.__class__.__name__
            raise
        finally:
            ended_at = datetime.now(UTC)
            observer.record(
                observer_api.DAEMON_CYCLE_COMPLETE,
                {
                    "start_time": started_at.isoformat(),
                    "end_time": ended_at.isoformat(),
                    "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
                    "events_count": total_new,
                    "sources": synced,
                    "status": "failed" if cycle_error else "completed",
                    "healthy": bool(health.get("healthy", False))
                    if isinstance(health, dict)
                    else None,
                    "synthesis_status": synthesis_result.get("status")
                    if isinstance(synthesis_result, dict)
                    else None,
                    "synthesis_error": synthesis_result.get("error")
                    if isinstance(synthesis_result, dict)
                    else None,
                    "memex_updated": synthesis_result.get("memex_updated")
                    if isinstance(synthesis_result, dict)
                    else None,
                    "error": cycle_error,
                },
                run_id=run_id,
            )
            if owns_observer:
                observer.close()

    def _health_check(self) -> dict[str, object]:
        from syke.daemon.metrics import run_health_check

        health = run_health_check(self.user_id)
        if not health.get("healthy", False):
            _log("HEALTH", "degraded")
        return health

    def _heal(self, health: dict[str, object]) -> None:
        if health.get("healthy", False):
            return
        _log("HEAL", "attempted soft recovery")

    def _reconcile(self, db) -> tuple[int, list[str]]:
        from rich.console import Console

        from syke.metrics import MetricsTracker
        from syke.sync import sync_source

        tracker = MetricsTracker(self.user_id)
        quiet = Console(quiet=True)
        sources = db.get_sources(self.user_id)
        if not sources:
            _log("RECON", "no sources")
            return 0, []

        total_new = 0
        synced: list[str] = []
        skipped: list[str] = []
        for source in sources:
            if source in self._watcher_authoritative_sources:
                skipped.append(source)
                continue
            if source in self._file_triggered_sources and source not in self._dirty_sources:
                skipped.append(source)
                continue
            changed_paths = self._dirty_paths_for_source(source)
            count = sync_source(
                db,
                self.user_id,
                source,
                tracker,
                quiet,
                changed_paths=changed_paths or None,
            )
            if count is None:
                continue
            total_new += count
            if count >= 0 and source != "chatgpt":
                synced.append(source)
            if source in self._file_triggered_sources:
                self._dirty_sources.discard(source)
                self._clear_dirty_paths(source)

        last_synthesis_ts = db.get_last_synthesis_timestamp(self.user_id)
        if last_synthesis_ts:
            pushed_since = db.count_events_since(self.user_id, last_synthesis_ts)
            total_new += max(0, pushed_since - total_new)

        if total_new > 0:
            _log("RECON", f"+{total_new} ({', '.join(synced)})")
        elif skipped:
            _log("RECON", f"watcher-authoritative ({', '.join(skipped)})")
        else:
            _log("RECON", "no new events")

        return total_new, synced

    def _synthesize(self, db, total_new: int) -> dict[str, object]:
        from syke.llm.backends.pi_synthesis import pi_synthesize

        with self._runtime_lock:
            result = pi_synthesize(db, self.user_id)
        status = result.get("status", "unknown")
        if status == "completed":
            _log("SYNTH", f"completed (+{total_new})")
        elif status == "skipped":
            _log("SYNTH", "skipped")
        else:
            _log("SYNTH", f"failed: {result.get('error', 'unknown')}")
        return result

    def _distribute(self, db, synthesis_result: dict[str, object]) -> None:
        from syke.distribution import refresh_distribution

        result = refresh_distribution(db, self.user_id)
        if result.memex_path:
            _log("DIST", f"memex -> {result.memex_path}")
        if result.claude_include_ready:
            _log("DIST", "claude -> include")
        if result.codex_memex_ready:
            _log("DIST", "codex -> AGENTS.md")
        if result.skill_paths:
            _log("DIST", f"skills -> {len(result.skill_paths)}")

        for warning in result.warnings:
            _log("WARN", f"distribution: {warning}")

    def _start_pi_runtime(self) -> None:
        """Start the canonical Pi runtime for daemon-driven synthesis."""
        try:
            from syke.runtime import start_pi_runtime
            from syke.runtime.workspace import WORKSPACE_ROOT, SESSIONS_DIR, setup_workspace

            source_db_path = Path(self._db.event_db_path) if self._db is not None else None
            syke_db_path = Path(self._db.db_path) if self._db is not None else None
            setup_workspace(
                self.user_id,
                source_db_path=source_db_path,
                syke_db_path=syke_db_path,
            )

            self._pi_runtime = start_pi_runtime(
                workspace_dir=WORKSPACE_ROOT,
                session_dir=SESSIONS_DIR,
            )
            _log(
                "PI",
                f"runtime started (pid={self._pi_runtime._process.pid if self._pi_runtime._process else '?'})",
            )
        except FileNotFoundError as e:
            _log("ERROR", f"Pi binary not found: {e}")
        except Exception as e:
            _log("ERROR", f"Pi runtime failed to start: {e}")

    def _stop_pi_runtime(self) -> None:
        """Stop Pi runtime if running."""
        if self._pi_runtime is not None:
            try:
                from syke.runtime import stop_pi_runtime

                stop_pi_runtime()
                _log("PI", "runtime stopped")
            except Exception as e:
                _log("ERROR", f"Pi runtime stop failed: {e}")
            self._pi_runtime = None

    def _start_ipc_server(self) -> None:
        """Start the local ask IPC bridge bound to the daemon's warm runtime."""
        try:
            from syke.daemon.ipc import DaemonIpcServer, socket_path_for_user

            self._ipc_server = DaemonIpcServer(self.user_id, self._handle_ipc_ask)
            if self._ipc_server.start():
                _log("IPC", f"ask server listening at {socket_path_for_user(self.user_id)}")
        except Exception as e:
            _log("ERROR", f"IPC server failed to start: {e}")
            self._ipc_server = None

    def _stop_ipc_server(self) -> None:
        if self._ipc_server is not None:
            try:
                self._ipc_server.stop()
                _log("IPC", "ask server stopped")
            except Exception as e:
                _log("ERROR", f"IPC server stop failed: {e}")
            self._ipc_server = None

    def _handle_ipc_ask(
        self,
        syke_db_path: str,
        event_db_path: str,
        question: str,
        on_event,
        timeout: float | None,
    ) -> tuple[str, dict[str, object]]:
        from syke.db import SykeDB
        from syke.llm.backends.pi_ask import pi_ask
        from syke.daemon.ipc import socket_path_for_user

        request_db = SykeDB(syke_db_path, event_db_path=event_db_path)
        try:
            with self._runtime_lock:
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

    def _start_sense_services(self, db) -> None:
        from syke.config import user_data_dir
        from syke.config_file import expand_path
        from syke.observe.registry import HarnessRegistry
        from syke.observe.factory import heal as heal_adapter
        from syke.observe.runtime import SQLiteWatcher, SenseWatcher, SenseWriter
        from syke.observe.trace import SykeObserver

        try:
            registry = HarnessRegistry(dynamic_adapters_dir=user_data_dir(self.user_id) / "adapters")
        except TypeError:
            registry = HarnessRegistry()
        descriptors = cast(list[Any], registry.active_harnesses())
        watcher_authoritative_sources: set[str] = set()
        file_triggered_sources: set[str] = set()

        observer = SykeObserver(db, self.user_id)
        self._observer = observer

        writer = SenseWriter(db, self.user_id, observer=observer)
        writer.start()
        self._writer = writer

        adapters_dir = user_data_dir(self.user_id) / "adapters"
        _cached_llm_fn = None  # lazy init on first heal
        for descriptor in descriptors:
            if descriptor.format_cluster not in {"jsonl", "json"} or descriptor.discover is None:
                continue
            for root in descriptor.discover.roots:
                root_path = expand_path(root.path)
                if root_path.is_file() or root_path.is_dir():
                    file_triggered_sources.add(descriptor.source)
                    break

        def _on_heal(source: str, samples: list[str]) -> None:
            nonlocal _cached_llm_fn
            if _cached_llm_fn is None:
                try:
                    from syke.llm.simple import build_llm_fn

                    _cached_llm_fn = build_llm_fn()
                except Exception:
                    pass
            _log("INFO", f"Healing triggered for {source}, {len(samples)} samples")
            ok = heal_adapter(source, samples, llm_fn=_cached_llm_fn, adapters_dir=adapters_dir)
            _log("INFO", f"Heal {'succeeded' if ok else 'failed'} for {source}")

        def _mark_source_dirty(source: str, file_path: Path) -> None:
            if source in file_triggered_sources:
                self._dirty_sources.add(source)
                with self._dirty_paths_lock:
                    self._dirty_paths_by_source.setdefault(source, set()).add(file_path)

        self._file_triggered_sources = file_triggered_sources

        sense_watcher = SenseWatcher(
            descriptors,
            writer,
            heal_fn=_on_heal,
            syke_observer=observer,
            on_source_dirty=_mark_source_dirty,
        )
        sense_watcher.start()
        self._sense_watcher = sense_watcher

        sqlite_watchers: list[Any] = []
        for descriptor in descriptors:
            if descriptor.format_cluster != "sqlite":
                continue
            adapter_raw = registry.get_adapter(descriptor.source, db, self.user_id)
            if adapter_raw is None:
                continue
            adapter = cast(Any, adapter_raw)

            paths: set[Path] = set()
            discover = getattr(adapter, "discover", None)
            if callable(discover):
                discovered = discover()
                if isinstance(discovered, list):
                    for candidate in discovered:
                        if isinstance(candidate, Path) and candidate.is_file():
                            paths.add(candidate)

            if descriptor.discover is not None:
                for root in descriptor.discover.roots:
                    root_path = expand_path(root.path)
                    if root_path.is_file():
                        paths.add(root_path)
                        continue
                    if not root_path.is_dir():
                        continue
                    for pattern in root.include or ["*.db", "*.sqlite", "*.sqlite3"]:
                        for match in root_path.glob(pattern):
                            if match.is_file():
                                paths.add(match)

            for db_path in sorted(paths):
                watcher = SQLiteWatcher(db_path, adapter, writer)
                watcher.start()
                sqlite_watchers.append(watcher)
                watcher_authoritative_sources.add(descriptor.source)

        self._sqlite_watchers = sqlite_watchers
        self._watcher_authoritative_sources = watcher_authoritative_sources

    def _stop_sense_services(self) -> None:
        for watcher in self._sqlite_watchers:
            try:
                watcher.stop()
            except Exception as exc:
                _log("ERROR", f"sqlite watcher stop failed: {exc!r}")
        self._sqlite_watchers = []

        if self._sense_watcher is not None:
            try:
                self._sense_watcher.stop()
            except Exception as exc:
                _log("ERROR", f"sense watcher stop failed: {exc!r}")
            self._sense_watcher = None

        if self._writer is not None:
            try:
                self._writer.stop()
            except Exception as exc:
                _log("ERROR", f"sense writer stop failed: {exc!r}")
            self._writer = None
        if self._observer is not None:
            try:
                self._observer.close()
            except Exception as exc:
                _log("ERROR", f"observer close failed: {exc!r}")
            self._observer = None
        self._watcher_authoritative_sources = set()
        self._file_triggered_sources = set()
        self._dirty_sources = set()
        with self._dirty_paths_lock:
            self._dirty_paths_by_source = {}

    def _dirty_paths_for_source(self, source: str) -> list[Path]:
        with self._dirty_paths_lock:
            paths = set(self._dirty_paths_by_source.get(source, set()))
        return sorted(paths)

    def _clear_dirty_paths(self, source: str) -> None:
        with self._dirty_paths_lock:
            self._dirty_paths_by_source.pop(source, None)

    def _signal_handler(self, signum: int, frame: object) -> None:
        self.stop()


# --- PID file helpers ---


def _write_pid() -> None:
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(str(os.getpid()))


def _remove_pid() -> None:
    PIDFILE.unlink(missing_ok=True)


def is_running() -> tuple[bool, int | None]:
    """Check if daemon is running. Returns (running, pid)."""
    if not PIDFILE.exists():
        return False, None
    try:
        pid = int(PIDFILE.read_text().strip())
    except (ValueError, OSError):
        PIDFILE.unlink(missing_ok=True)
        return False, None
    try:
        os.kill(pid, 0)
        return True, pid
    except OSError:
        PIDFILE.unlink(missing_ok=True)
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
    import subprocess

    plist_content = generate_plist(user_id, interval=interval)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_content)
    os.chmod(PLIST_PATH, 0o600)

    # Unload first for idempotency (ignore errors if not loaded)
    subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        check=False,
        capture_output=True,
    )
    subprocess.run(
        ["launchctl", "load", str(PLIST_PATH)],
        check=True,
    )
    return PLIST_PATH


def uninstall_launchd() -> bool:
    """Unload and remove the LaunchAgent. Returns True if removed."""
    import subprocess

    if not PLIST_PATH.exists():
        return False
    subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        check=False,  # may already be unloaded
    )
    PLIST_PATH.unlink(missing_ok=True)
    return True


def launchd_status() -> str | None:
    """Check launchctl for our agent. Returns status string or None."""
    import subprocess

    try:
        r = subprocess.run(
            ["launchctl", "list", LAUNCHD_LABEL],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


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

    if sys.platform == "darwin":
        uninstall_launchd()
    else:
        uninstall_cron()


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
