"""Configuration — config.toml loading, .env loading, paths, and runtime knobs."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from syke.config_file import THINKING_LEVELS, SykeConfig, expand_path, load_config

# Syke home directory (persisted config, credentials)
SYKE_HOME = Path.home() / ".syke"

# Load ~/.syke/.env first (persisted daemon-safe environment config).
_syke_env = SYKE_HOME / ".env"
if _syke_env.exists():
    load_dotenv(_syke_env)

# Root of the syke project
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Load config.toml (after ~/.syke/.env so env vars can override) ───────────

CFG: SykeConfig = load_config()


def reload_config() -> SykeConfig:
    """Re-read config.toml and replace the module-level CFG."""
    global CFG
    CFG = load_config()
    return CFG


def _is_source_install() -> bool:
    """True when running from a git clone (pyproject.toml exists at PROJECT_ROOT)."""
    return (PROJECT_ROOT / "pyproject.toml").exists()


# ── Paths (config.toml → env var override) ──────────────────────────────────


def _resolve_data_dir() -> Path:
    """Resolve data directory: env var > config.toml > default."""
    env = os.getenv("SYKE_DATA_DIR")
    if env:
        return Path(env).resolve()
    return expand_path(CFG.paths.data_dir)


DATA_DIR = _resolve_data_dir()

# Source paths (where to find session data)
CODEX_DIR = expand_path(CFG.paths.sources.codex)
CODEX_GLOBAL_AGENTS = CODEX_DIR / "AGENTS.md"

# Distribution paths (where memex gets written)
CLAUDE_GLOBAL_MD = expand_path(CFG.paths.distribution.claude_md)
SKILLS_DIRS = [expand_path(p) for p in CFG.paths.distribution.skills_dirs]


# ── Helper: env var or config value ─────────────────────────────────────────


def _env_str(var: str, cfg_val: str | None) -> str | None:
    """Return env var if set, else config value. None if both empty."""
    env = os.getenv(var)
    if env:
        return env
    return cfg_val if cfg_val else None


def _env_int(var: str, cfg_val: int) -> int:
    """Return env var as int if set, else config value."""
    env = os.getenv(var)
    return int(env) if env else cfg_val


# ── Agent settings (env var > config.toml > hardcoded default) ──────────────

# Ask agent
ASK_TIMEOUT: int = _env_int("SYKE_ASK_TIMEOUT", CFG.ask.timeout)

# Synthesis agent
SYNC_TIMEOUT: int = _env_int("SYKE_SYNC_TIMEOUT", CFG.synthesis.timeout)
FIRST_RUN_SYNC_TIMEOUT: int = _env_int(
    "SYKE_SYNC_FIRST_RUN_TIMEOUT",
    CFG.synthesis.first_run_timeout,
)
SYNC_THINKING_LEVEL = _env_str("SYKE_SYNC_THINKING_LEVEL", CFG.synthesis.thinking_level) or "medium"
if SYNC_THINKING_LEVEL not in THINKING_LEVELS:
    SYNC_THINKING_LEVEL = "medium"

# Daemon
DAEMON_INTERVAL: int = _env_int("SYKE_DAEMON_INTERVAL", CFG.daemon.interval)

# Timezone
SYKE_TIMEZONE: str = os.getenv("SYKE_TIMEZONE", "") or CFG.timezone

# Default user — env var > config.toml > system username
DEFAULT_USER: str = os.getenv("SYKE_USER", "") or CFG.user


# ── Per-user paths (derived from DATA_DIR) ──────────────────────────────────


def user_data_dir(user_id: str) -> Path:
    """Return the data directory for a specific user, creating it if needed."""
    if "/" in user_id or "\\" in user_id or ".." in user_id:
        raise ValueError(f"Invalid user_id: {user_id!r}")
    d = DATA_DIR / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_syke_db_path(user_id: str) -> Path:
    """Return the canonical mutable Syke DB path for a user.

    Override: SYKE_DB env var bypasses the standard path resolution.
    Used by sandbox tests to point at an isolated scratch DB.
    """
    env_override = os.getenv("SYKE_DB")
    if env_override:
        return Path(env_override).resolve()
    return user_data_dir(user_id) / "syke.db"


