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
DEFAULT_INTERVAL = 900  # 15 minutes
LAUNCHD_LABEL = "com.syke.daemon"
PLIST_PATH = Path(os.path.expanduser("~/Library/LaunchAgents")) / f"{LAUNCHD_LABEL}.plist"
LOG_PATH = Path(os.path.expanduser("~/.config/syke/daemon.log"))


class SykeDaemon:
    """Runs sync on a configurable interval."""

    def __init__(self, user_id: str, interval: int = DEFAULT_INTERVAL):
        self.user_id = user_id
        self.interval = interval
        self.running = True

    def run(self) -> None:
        """Main daemon loop — blocks until signal."""
        from rich.console import Console

        self.console = Console()
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        _write_pid()

        self.console.print(
            f"[bold]Syke daemon started[/bold] — user: [cyan]{self.user_id}[/cyan] "
            f"(every {self.interval}s)"
        )

        try:
            while self.running:
                self._sync_cycle()
                self._sleep(self.interval)
        finally:
            _remove_pid()
            self.console.print("\n[dim]Daemon stopped.[/dim]")

    def _sync_cycle(self) -> None:
        """Run one sync cycle."""
        from syke.sync import run_sync
        from syke.config import user_db_path
        from syke.db import SykeDB

        now = datetime.now().strftime("%H:%M:%S")
        try:
            db = SykeDB(user_db_path(self.user_id))
            db.initialize()
        except Exception as exc:
            self.console.print(f"[red][{now}] DB init error: {exc}[/red]")
            logger.error("DB init failed:\n%s", traceback.format_exc())
            return  # Skip this cycle, try again next time
        try:
            total_new, synced = run_sync(
                db, self.user_id, skip_profile=False, out=self.console,
            )
            if total_new > 0:
                self.console.print(
                    f"[dim][{now}][/dim] Sync: +{total_new} from {', '.join(synced)}."
                )
            else:
                self.console.print(f"[dim][{now}] Sync: no new events.[/dim]")
        except Exception as exc:
            self.console.print(f"[red][{now}] Sync error: {exc}[/red]")
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

def generate_plist(user_id: str, source_install: bool | None = None, interval: int = DEFAULT_INTERVAL) -> str:
    """Generate macOS LaunchAgent plist XML.

    Pip install: uses ``syke`` console script on PATH.
    Source install: uses ``sys.executable -m syke`` with WorkingDirectory.
    Injects ``ANTHROPIC_API_KEY`` into EnvironmentVariables when set.
    """
    import shutil
    import sys

    from syke.config import PROJECT_ROOT, _is_source_install

    if source_install is None:
        source_install = _is_source_install()

    log_path = str(LOG_PATH)

    if source_install:
        program_args = (
            f"        <string>{sys.executable}</string>\n"
            f"        <string>-m</string>\n"
            f"        <string>syke</string>\n"
            f"        <string>--user</string>\n"
            f"        <string>{user_id}</string>\n"
            f"        <string>sync</string>"
        )
        working_dir_block = (
            f"    <key>WorkingDirectory</key>\n"
            f"    <string>{PROJECT_ROOT}</string>\n"
        )
    else:
        syke_bin = shutil.which("syke") or "syke"
        program_args = (
            f"        <string>{syke_bin}</string>\n"
            f"        <string>--user</string>\n"
            f"        <string>{user_id}</string>\n"
            f"        <string>sync</string>"
        )
        working_dir_block = ""

    # Inject API key into plist EnvironmentVariables when available
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        env_block = (
            "    <key>EnvironmentVariables</key>\n"
            "    <dict>\n"
            "        <key>ANTHROPIC_API_KEY</key>\n"
            f"        <string>{api_key}</string>\n"
            "    </dict>\n"
        )
    else:
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
        check=False, capture_output=True,
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
            capture_output=True, text=True, timeout=5,
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

    env_prefix = ""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        env_prefix = f"ANTHROPIC_API_KEY={api_key} "

    # Convert seconds to minutes for cron (minimum 1 min)
    minutes = max(1, interval // 60)
    return f"*/{minutes} * * * * {env_prefix}{syke_bin} --user {user_id} sync >> {log_path} 2>&1 {CRON_TAG}"


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
