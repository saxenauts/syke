"""Background sync daemon."""

from __future__ import annotations

import logging
import os
import signal
import time
import traceback
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PIDFILE = Path(os.path.expanduser("~/.config/syke/daemon.pid"))


def _log(level: str, msg: str) -> None:
    """Write a clean one-liner to stdout (captured by launchd/cron as daemon.log)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {level:<5} {msg}", flush=True)


DEFAULT_INTERVAL = 900  # 15 minutes
LAUNCHD_LABEL = "com.syke.daemon"
PLIST_PATH = (
    Path(os.path.expanduser("~/Library/LaunchAgents")) / f"{LAUNCHD_LABEL}.plist"
)
LOG_PATH = Path(os.path.expanduser("~/.config/syke/daemon.log"))


class SykeDaemon:
    """Runs sync on a configurable interval."""

    def __init__(self, user_id: str, interval: int = DEFAULT_INTERVAL):
        self.user_id = user_id
        self.interval = interval
        self.running = True

    def run(self) -> None:
        """Main daemon loop — blocks until signal."""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        _write_pid()
        _log(
            "START", f"user={self.user_id} interval={self.interval}s pid={os.getpid()}"
        )
        try:
            while self.running:
                self._sync_cycle()
                self._sleep(self.interval)
        finally:
            _remove_pid()
            _log("STOP", "daemon stopped")

    def _sync_cycle(self) -> None:
        """Run one sync cycle."""
        from syke.sync import run_sync
        from syke.config import user_db_path
        from syke.db import SykeDB
        from rich.console import Console
        from syke import __version__
        from syke.version_check import check_update_available
        from syke.models import Event
        from datetime import UTC

        try:
            db = SykeDB(user_db_path(self.user_id))
            db.initialize()
        except Exception as exc:
            _log("ERROR", f"db init failed: {exc!r}")
            logger.error("DB init failed:\n%s", traceback.format_exc())
            return
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
            db.close()

    def _sleep(self, seconds: int) -> None:
        """Interruptible sleep."""
        end = time.time() + seconds
        while self.running and time.time() < end:
            time.sleep(1)

    def _handle_signal(self, signum: int, frame: object) -> None:
        self.running = False


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


def generate_plist(
    user_id: str, source_install: bool | None = None, interval: int = DEFAULT_INTERVAL
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
    if syke_bin:
        program_args = (
            f"        <string>{syke_bin}</string>\n"
            f"        <string>--user</string>\n"
            f"        <string>{user_id}</string>\n"
            f"        <string>sync</string>"
        )
        working_dir_block = ""
    else:
        program_args = (
            f"        <string>{sys.executable}</string>\n"
            f"        <string>-m</string>\n"
            f"        <string>syke</string>\n"
            f"        <string>--user</string>\n"
            f"        <string>{user_id}</string>\n"
            f"        <string>sync</string>"
        )
        working_dir_block = (
            f"    <key>WorkingDirectory</key>\n    <string>{PROJECT_ROOT}</string>\n"
        )

        protected_dirs = (
            Path.home() / "Documents",
            Path.home() / "Desktop",
            Path.home() / "Downloads",
        )

        project_root = PROJECT_ROOT.resolve()
        if any(
            project_root == protected_dir.resolve()
            or protected_dir.resolve() in project_root.parents
            for protected_dir in protected_dirs
        ):
            logger.warning(
                "syke daemon launchd plist falling back to venv execution in a TCC-protected path (%s); "
                "ensure `syke` is on PATH to avoid macOS access restrictions",
                project_root,
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
{working_dir_block}{env_block}    <key>StartInterval</key>
    <integer>{interval}</integer>
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


def _build_cron_entry(user_id: str, interval: int = DEFAULT_INTERVAL) -> str:
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


def install_cron(user_id: str, interval: int = DEFAULT_INTERVAL) -> None:
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
    lines = [l for l in existing.splitlines() if CRON_TAG not in l]
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
    filtered = [l for l in lines if CRON_TAG not in l]

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


def install_and_start(user_id: str, interval: int = DEFAULT_INTERVAL) -> None:
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
