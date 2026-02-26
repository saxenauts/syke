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
os.environ.pop("ANTHROPIC_API_KEY", None)

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

# ── Agent settings (all env-overridable) ────────────────────────────────────
ASK_MODEL: str | None = os.getenv("SYKE_ASK_MODEL") or None  # None = SDK tier default
ASK_MAX_TURNS: int = int(os.getenv("SYKE_ASK_MAX_TURNS", "8"))
ASK_BUDGET: float = float(os.getenv("SYKE_ASK_BUDGET", "1.0"))
ASK_TIMEOUT: int = int(os.getenv("SYKE_ASK_TIMEOUT", "120"))  # wall-clock seconds

SYNC_MODEL: str = os.getenv("SYKE_SYNC_MODEL", "sonnet")
SYNC_MAX_TURNS: int = int(os.getenv("SYKE_SYNC_MAX_TURNS", "10"))
SYNC_BUDGET: float = float(os.getenv("SYKE_SYNC_BUDGET", "0.5"))
SYNC_THINKING: int = int(os.getenv("SYKE_SYNC_THINKING", "2000"))

REBUILD_MODEL: str = os.getenv("SYKE_REBUILD_MODEL", "opus")
REBUILD_MAX_TURNS: int = int(os.getenv("SYKE_REBUILD_MAX_TURNS", "20"))
REBUILD_BUDGET: float = float(os.getenv("SYKE_REBUILD_BUDGET", "3.0"))
REBUILD_THINKING: int = int(os.getenv("SYKE_REBUILD_THINKING", "30000"))

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
