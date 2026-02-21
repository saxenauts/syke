"""Configuration — .env loading, paths, user data dirs."""

from __future__ import annotations

import getpass
import os
from pathlib import Path

from dotenv import load_dotenv

# Syke home directory (persisted config, credentials)
SYKE_HOME = Path.home() / ".syke"

# Load .env files: ~/.syke/.env first (persisted credentials), then project .env
_syke_env = SYKE_HOME / ".env"
if _syke_env.exists():
    load_dotenv(_syke_env)
load_dotenv()  # project .env (won't overwrite already-set vars)

# Root of the syke project
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _is_source_install() -> bool:
    """True when running from a git clone (pyproject.toml exists at PROJECT_ROOT)."""
    return (PROJECT_ROOT / "pyproject.toml").exists()


def _default_data_dir() -> Path:
    """Resolve data directory: env var override or ~/.syke/data."""
    env = os.getenv("SYKE_DATA_DIR")
    if env:
        return Path(env).resolve()
    return Path.home() / ".syke" / "data"


# Data directory (per-user data lives here)
DATA_DIR = _default_data_dir()

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Agent settings (all env-overridable) ────────────────────────────────────
ASK_MODEL: str | None = os.getenv("SYKE_ASK_MODEL") or None  # None = SDK tier default
ASK_MAX_TURNS: int    = int(os.getenv("SYKE_ASK_MAX_TURNS", "8"))
ASK_BUDGET: float     = float(os.getenv("SYKE_ASK_BUDGET", "1.0"))

SYNC_MODEL: str       = os.getenv("SYKE_SYNC_MODEL", "sonnet")
SYNC_MAX_TURNS: int   = int(os.getenv("SYKE_SYNC_MAX_TURNS", "10"))
SYNC_BUDGET: float    = float(os.getenv("SYKE_SYNC_BUDGET", "0.5"))
SYNC_THINKING: int    = int(os.getenv("SYKE_SYNC_THINKING", "2000"))

REBUILD_MODEL: str     = os.getenv("SYKE_REBUILD_MODEL", "opus")
REBUILD_MAX_TURNS: int = int(os.getenv("SYKE_REBUILD_MAX_TURNS", "20"))
REBUILD_BUDGET: float  = float(os.getenv("SYKE_REBUILD_BUDGET", "3.0"))
REBUILD_THINKING: int  = int(os.getenv("SYKE_REBUILD_THINKING", "30000"))

# Default user — env var override, else system username
DEFAULT_USER = os.getenv("SYKE_USER", "") or getpass.getuser()


def user_data_dir(user_id: str) -> Path:
    """Return the data directory for a specific user, creating it if needed."""
    if "/" in user_id or "\\" in user_id or ".." in user_id:
        raise ValueError(f"Invalid user_id: {user_id!r}")
    d = DATA_DIR / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_db_path(user_id: str) -> Path:
    """Return the SQLite DB path for a user."""
    return user_data_dir(user_id) / "syke.db"


def user_profile_path(user_id: str) -> Path:
    """Return the latest profile JSON path for a user."""
    return user_data_dir(user_id) / "profile.json"


def save_api_key(api_key: str) -> Path:
    """Persist ANTHROPIC_API_KEY to ~/.syke/.env (chmod 600).

    Called during setup when a key is detected in the environment,
    so future invocations (cron, non-interactive shells) can find it
    without relying on .zshrc being sourced.

    NOTE: ask() (Agent SDK / MCP) does NOT use this — it uses Claude Code
    session auth via ~/.claude/. This file is read by agentic_perceiver.py
    only when running outside a Claude Code session (e.g. CI/CD).
    """
    SYKE_HOME.mkdir(parents=True, exist_ok=True)
    env_file = SYKE_HOME / ".env"
    env_file.write_text(f"ANTHROPIC_API_KEY={api_key}\n")
    os.chmod(env_file, 0o600)
    return env_file


def load_api_key() -> str:
    """Load API key: env var first, then ~/.syke/.env fallback."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    env_file = SYKE_HOME / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


