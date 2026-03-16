"""Background sync daemon."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
import traceback
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
        self._sqlite_watchers: list[Any] = []

    def run(self) -> None:
        """Main daemon loop — blocks until signal."""
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        _write_pid()
        _log("START", f"user={self.user_id} interval={self.interval}s pid={os.getpid()}")

        try:
            from syke.config import user_data_dir, user_db_path
            from syke.db import SykeDB
            from syke.sense.registry import set_dynamic_adapters_dir

            self._db = SykeDB(user_db_path(self.user_id))
            self._db.initialize()

            adapters_dir = user_data_dir(self.user_id) / "adapters"
            set_dynamic_adapters_dir(adapters_dir)

            self._start_sense_services(self._db)

            while self.running and not self._stop_event.is_set():
                self._daemon_cycle(self._db)
                if self._stop_event.wait(self.interval):
                    break
        finally:
            self._stop_sense_services()
            if self._db is not None:
                self._db.close()
                self._db = None
            _remove_pid()
            _log("STOP", "daemon stopped")

    def stop(self) -> None:
        self.running = False
        self._stop_event.set()

    def _daemon_cycle(self, db) -> None:
        health = self._health_check()
        self._heal(health)
        total_new, _ = self._reconcile(db)
        synthesis_result = self._synthesize(db, total_new)
        self._distribute(db, synthesis_result)

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
        for source in sources:
            count = sync_source(db, self.user_id, source, tracker, quiet)
            total_new += count
            if count >= 0 and source != "chatgpt":
                synced.append(source)

        last_synthesis_ts = db.get_last_synthesis_timestamp(self.user_id)
        if last_synthesis_ts:
            pushed_since = db.count_events_since(self.user_id, last_synthesis_ts)
            total_new += max(0, pushed_since - total_new)

        if total_new > 0:
            _log("RECON", f"+{total_new} ({', '.join(synced)})")
        else:
            _log("RECON", "no new events")

        return total_new, synced

    def _synthesize(self, db, total_new: int) -> dict[str, object]:
        from syke.memory.synthesis import synthesize

        result = synthesize(db, self.user_id)
        status = result.get("status", "unknown")
        if status == "ok":
            _log("SYNTH", f"ok (+{total_new})")
        elif status == "skipped":
            _log("SYNTH", "skipped")
        else:
            _log("SYNTH", f"error: {result.get('error', 'unknown')}")
        return result

    def _distribute(self, db, synthesis_result: dict[str, object]) -> None:
        from syke.distribution.context_files import distribute_memex
        from syke.distribution.harness import install_all as install_harness
        from syke.memory.memex import get_memex_for_injection

        try:
            path = distribute_memex(db, self.user_id)
            if path:
                _log("DIST", f"memex -> {path}")
        except Exception as exc:
            _log("ERROR", f"distribution failed: {exc!r}")

        try:
            memex_content = get_memex_for_injection(db, self.user_id)
            harness_results = install_harness(memex=memex_content)
            updated = [name for name, result in harness_results.items() if result.ok]
            if updated:
                _log("DIST", f"harness -> {', '.join(updated)}")
        except Exception:
            pass

    def _start_sense_services(self, db) -> None:
        from syke.config_file import expand_path
        from syke.ingestion.registry import HarnessRegistry
        from syke.sense.sqlite_watcher import SQLiteWatcher
        from syke.sense.watcher import SenseWatcher
        from syke.sense.writer import SenseWriter

        registry = HarnessRegistry()
        descriptors = cast(list[Any], registry.active_harnesses())

        writer = SenseWriter(db, self.user_id)
        writer.start()
        self._writer = writer

        sense_watcher = SenseWatcher(descriptors, writer)
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

        self._sqlite_watchers = sqlite_watchers

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

    def _sync_cycle(self) -> None:
        """Run one sync cycle."""
        from rich.console import Console

        from syke import __version__
        from syke.config import user_db_path
        from syke.db import SykeDB
        from syke.models import Event
        from syke.sync import run_sync
        from syke.version_check import check_update_available

        db = None
        try:
            db = SykeDB(user_db_path(self.user_id))
            db.initialize()
        except Exception as exc:
            _log("ERROR", f"db init failed: {exc!r}")
            logger.error("DB init failed:\n%s", traceback.format_exc())
            return
        observer_api = import_module("syke.sense.self_observe")
        observer = observer_api.SykeObserver(db, self.user_id)
        run_id = str(uuid7())
        started_at = datetime.now(UTC)
        observer.record(
            observer_api.DAEMON_CYCLE_START,
            {"start_time": started_at.isoformat()},
            run_id=run_id,
        )
        try:
            quiet = Console(quiet=True)
            total_new, synced = run_sync(db, self.user_id, out=quiet)
            if total_new > 0:
                _log("SYNC", f"+{total_new} ({', '.join(synced)})")
            else:
                _log("SYNC", "no new events")
            try:
                update_available, latest = check_update_available(__version__)
                if update_available:
                    _log(
                        "WARN",
                        f"update available: {__version__} -> {latest} (run: syke self-update)",
                    )
                    event = Event(
                        user_id=self.user_id,
                        source="syke-daemon",
                        event_type="update-available",
                        title=f"Syke update available: {__version__} \u2192 {latest}",
                        content="Run: syke self-update",
                        external_id=f"update-available-{latest}",
                        timestamp=datetime.now(UTC),
                    )
                    db.insert_event(event)
            except Exception:
                pass  # version check must never crash the sync loop
        except Exception as exc:
            _log("ERROR", f"sync failed: {exc!r}")
            logger.error("Daemon sync failed:\n%s", traceback.format_exc())
        finally:
            ended_at = datetime.now(UTC)
            observer.record(
                observer_api.DAEMON_CYCLE_COMPLETE,
                {
                    "start_time": started_at.isoformat(),
                    "end_time": ended_at.isoformat(),
                    "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
                    "events_count": locals().get("total_new", 0),
                    "sources": locals().get("synced", []),
                },
                run_id=run_id,
            )
            if db is not None:
                db.close()

    def _sleep(self, seconds: int) -> None:
        """Interruptible sleep."""
        end = time.time() + seconds
        while self.running and time.time() < end:
            time.sleep(1)

    def _handle_signal(self, signum: int, frame: object) -> None:
        self.stop()

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
    protected_dirs = (
        Path.home() / "Documents",
        Path.home() / "Desktop",
        Path.home() / "Downloads",
    )
    resolved = path.resolve()
    return any(resolved == d.resolve() or d.resolve() in resolved.parents for d in protected_dirs)


def _find_safe_syke_bin() -> str | None:
    """Find a syke binary outside TCC-protected dirs.

    Checks common install locations (pipx, uv tool, Homebrew) that
    launchd can access without Full Disk Access.
    """
    import shutil

    candidates = [
        shutil.which("syke"),
        str(Path.home() / ".local" / "bin" / "syke"),
        "/opt/homebrew/bin/syke",
        "/usr/local/bin/syke",
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        p = Path(candidate)
        if p.exists() and not _is_tcc_protected(p):
            return str(p.resolve())
    return None


def generate_plist(
    user_id: str, source_install: bool | None = None, interval: int = DAEMON_INTERVAL
) -> str:
    """Generate macOS LaunchAgent plist XML.

    Prefers the ``syke`` console script from PATH for both pip and source installs.
    Falls back to ``sys.executable -m syke`` with WorkingDirectory only when
    no ``syke`` binary is available on PATH.

    Auth: synthesis uses ``~/.claude/`` session auth (Agent SDK) or ``~/.syke/.env`` fallback.
    ``ANTHROPIC_API_KEY`` is NOT injected into the plist — keys baked at setup time become
    stale and silently fail with no recovery path.
    """
    import shutil
    import sys

    from syke.config import PROJECT_ROOT, _is_source_install

    if source_install is None:
        source_install = _is_source_install()

    log_path = str(LOG_PATH)

    syke_bin = shutil.which("syke")

    # Resolve the executable path and reject anything inside TCC-protected dirs.
    # macOS blocks LaunchAgent processes from accessing ~/Documents, ~/Desktop,
    # ~/Downloads — the daemon will crash-loop silently if the binary lives there.
    resolved_bin = Path(syke_bin).resolve() if syke_bin else Path(sys.executable).resolve()
    if _is_tcc_protected(resolved_bin):
        # Try to find an alternative syke binary outside TCC-protected dirs.
        # shutil.which may have found the .venv/bin/syke inside ~/Documents when
        # running via `uv run` — but ~/.local/bin/syke (pipx) may also exist.
        syke_bin = _find_safe_syke_bin()
        if syke_bin is None:
            raise RuntimeError(
                f"Cannot install daemon: resolved binary path is inside a macOS-protected "
                f"directory ({resolved_bin}). launchd will be blocked by TCC.\n\n"
                f"Fix: install syke to a non-protected location:\n"
                f"  pipx install syke        # installs to ~/.local/bin/\n"
                f"  uv tool install syke     # installs to ~/.local/bin/\n\n"
                f"Or if developing from source:\n"
                f"  uv tool install -e .     # creates shim at ~/.local/bin/syke\n"
                f"  pip install -e .         # with a venv outside ~/Documents"
            )

    if syke_bin:
        program_args = (
            f"        <string>{syke_bin}</string>\n"
            f"        <string>--user</string>\n"
            f"        <string>{user_id}</string>\n"
            f"        <string>daemon</string>\n"
            f"        <string>run</string>\n"
            f"        <string>--interval</string>\n"
            f"        <string>{interval}</string>"
        )
        working_dir_block = ""
    else:
        program_args = (
            f"        <string>{sys.executable}</string>\n"
            f"        <string>-m</string>\n"
            f"        <string>syke</string>\n"
            f"        <string>--user</string>\n"
            f"        <string>{user_id}</string>\n"
            f"        <string>daemon</string>\n"
            f"        <string>run</string>\n"
            f"        <string>--interval</string>\n"
            f"        <string>{interval}</string>"
        )
        working_dir_block = (
            f"    <key>WorkingDirectory</key>\n    <string>{PROJECT_ROOT}</string>\n"
        )

    # Auth is NOT baked into the plist. Keys baked at setup time become stale and
    # silently fail with no recovery path. Memory synthesis reads ~/.syke/.env
    # (chmod 600) as fallback; Agent SDK reads ~/.claude/ session auth directly.
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


def install_launchd(user_id: str) -> Path:
    """Write plist and load the LaunchAgent. Returns plist path."""
    import subprocess

    plist_content = generate_plist(user_id)
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
    import shutil

    syke_bin = shutil.which("syke") or "syke"
    log_path = str(LOG_PATH)

    # Convert seconds to minutes for cron (minimum 1 min)
    minutes = max(1, interval // 60)
    # Do NOT bake ANTHROPIC_API_KEY into the crontab — it exposes the key in
    # plaintext in `crontab -l` and creates a stale-key risk if the key rotates.
    # sync reads from ~/.syke/.env or uses Claude Code session auth automatically.
    return f"*/{minutes} * * * * {syke_bin} --user {user_id} sync >> {log_path} 2>&1 {CRON_TAG}"


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
        install_launchd(user_id)
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
