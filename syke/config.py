"""Configuration — config.toml loading, .env loading, paths, user data dirs, env helpers.

Precedence (highest wins): env var → config.toml → hardcoded default.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

from syke.config_file import SykeConfig, expand_path, load_config

# Syke home directory (persisted config, credentials)
SYKE_HOME = Path.home() / ".syke"

# Load .env files: ~/.syke/.env first (persisted credentials), then project .env
_syke_env = SYKE_HOME / ".env"
if _syke_env.exists():
    load_dotenv(_syke_env)
load_dotenv()  # project .env (won't overwrite already-set vars)

# Root of the syke project
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Load config.toml (after .env so env vars can override) ──────────────────

CFG: SykeConfig = load_config()


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

AUTH_PATH = expand_path(os.getenv("SYKE_AUTH_PATH", "") or CFG.paths.auth)

# Source paths (where to find session data)
CLAUDE_CODE_DIR = expand_path(CFG.paths.sources.claude_code)
CODEX_DIR = expand_path(CFG.paths.sources.codex)
CHATGPT_EXPORT_DIR = expand_path(CFG.paths.sources.chatgpt_export)

# Distribution paths (where memex gets written)
CLAUDE_GLOBAL_MD = expand_path(CFG.paths.distribution.claude_md)
SKILLS_DIRS = [expand_path(p) for p in CFG.paths.distribution.skills_dirs]
HERMES_HOME = expand_path(CFG.paths.distribution.hermes_home)


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


def _env_float(var: str, cfg_val: float) -> float:
    """Return env var as float if set, else config value."""
    env = os.getenv(var)
    return float(env) if env else cfg_val


# ── Agent settings (env var > config.toml > hardcoded default) ──────────────

# Models
SYNC_MODEL: str = os.getenv("SYKE_SYNC_MODEL", "") or CFG.models.synthesis
ASK_MODEL: str | None = _env_str("SYKE_ASK_MODEL", CFG.models.ask)
REBUILD_MODEL: str = os.getenv("SYKE_REBUILD_MODEL", "") or CFG.models.rebuild

# Ask agent
ASK_MAX_TURNS: int = _env_int("SYKE_ASK_MAX_TURNS", CFG.ask.max_turns)
ASK_BUDGET: float = _env_float("SYKE_ASK_BUDGET", CFG.ask.budget)
ASK_TIMEOUT: int = _env_int("SYKE_ASK_TIMEOUT", CFG.ask.timeout)

# Synthesis agent
SYNC_MAX_TURNS: int = _env_int("SYKE_SYNC_MAX_TURNS", CFG.synthesis.max_turns)
SYNC_BUDGET: float = _env_float("SYKE_SYNC_BUDGET", CFG.synthesis.budget)
SYNC_THINKING: int = _env_int("SYKE_SYNC_THINKING", CFG.synthesis.thinking)

# First-run synthesis (no existing memex) — needs more room to process full history
SETUP_SYNC_MAX_TURNS: int = _env_int("SYKE_SETUP_SYNC_MAX_TURNS", CFG.synthesis.first_run_max_turns)
SETUP_SYNC_BUDGET: float = _env_float("SYKE_SETUP_SYNC_BUDGET", CFG.synthesis.first_run_budget)

# Rebuild
REBUILD_MAX_TURNS: int = _env_int("SYKE_REBUILD_MAX_TURNS", CFG.rebuild.max_turns)
REBUILD_BUDGET: float = _env_float("SYKE_REBUILD_BUDGET", CFG.rebuild.budget)
REBUILD_THINKING: int = _env_int("SYKE_REBUILD_THINKING", CFG.rebuild.thinking)

# Daemon
DAEMON_INTERVAL: int = _env_int("SYKE_DAEMON_INTERVAL", CFG.daemon.interval)

# Sync threshold
SYNC_EVENT_THRESHOLD: int = _env_int("SYKE_SYNC_THRESHOLD", CFG.synthesis.threshold)

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


def user_db_path(user_id: str) -> Path:
    """Return the SQLite DB path for a user."""
    return user_data_dir(user_id) / "syke.db"


def user_profile_path(user_id: str) -> Path:
    """Return the latest profile JSON path for a user."""
    return user_data_dir(user_id) / "profile.json"


# ── Claude env isolation ──────────────────────────────────────────────────
# Prefixes that signal "you're inside a Claude session." The Agent SDK
# inherits os.environ into child subprocesses; these must be stripped
# to avoid nesting-detection rejection (upstream SDK issue #573).
_CLAUDE_NESTING_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_")

# Auth env vars that leak parent credentials into SDK subprocesses.
# The SDK builds subprocess env as {**os.environ, **options.env}, so
# inherited auth vars bypass provider routing unless explicitly removed.
# Note: CLAUDE_CODE_OAUTH_TOKEN* is already caught by _CLAUDE_NESTING_PREFIXES.
_ANTHROPIC_AUTH_LEAK_VARS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")


@contextmanager
def clean_claude_env():
    """Temporarily strip Claude nesting markers and auth vars from os.environ.

    The Claude Agent SDK merges os.environ into child subprocess env
    (env={} in ClaudeAgentOptions only adds, never removes keys).
    This context manager removes nesting markers AND auth credentials
    before the SDK call, restoring them afterward. Without this,
    parent-process auth (e.g. from opencode, dotenv) leaks into the
    subprocess and bypasses provider routing.
    """
    stripped: dict[str, str] = {}
    for key in list(os.environ):
        if any(key == p or key.startswith(p) for p in _CLAUDE_NESTING_PREFIXES):
            stripped[key] = os.environ.pop(key)
    for key in _ANTHROPIC_AUTH_LEAK_VARS:
        if key in os.environ:
            stripped[key] = os.environ.pop(key)
    try:
        yield
    finally:
        os.environ.update(stripped)
