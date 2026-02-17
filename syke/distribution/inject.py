"""File injection — write context files to target directories."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from syke.distribution.formatters import format_profile
from syke.models import UserProfile

CLAUDE_USER_CONFIG_PATH = Path.home() / ".claude.json"  # MCP servers (Claude Code reads this)
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"  # Hooks, env, plugins
def _get_claude_desktop_config_path() -> Path:
    """Platform-aware Claude Desktop config path."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    else:
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"

CLAUDE_DESKTOP_CONFIG_PATH = _get_claude_desktop_config_path()


def _detect_install_method() -> str:
    """Detect how syke was installed.

    Returns ``"uvx"`` if uvx is available (zero-install, agent-friendly),
    otherwise ``"pip"`` (console script on PATH via pip/pipx).
    """
    if shutil.which("uvx"):
        return "uvx"
    return "pip"


def _build_server_entry(user_id: str, source_install: bool = False) -> dict:
    """Build the MCP server entry dict.

    Source install: ``"command": sys.executable`` + ``-m syke`` (venv python).
    uvx available: ``"command": "uvx"`` + ``["syke", ...]`` (zero-install).
    Fallback: ``"command": "syke"`` (console script on PATH via pip/pipx).
    Always injects ``ANTHROPIC_API_KEY`` into env when available.
    """
    if source_install:
        from syke.config import PROJECT_ROOT
        entry: dict = {
            "command": sys.executable,
            "args": ["-m", "syke", "--user", user_id, "serve", "--transport", "stdio"],
            "env": {"PYTHONPATH": str(PROJECT_ROOT)},
        }
    elif _detect_install_method() == "uvx":
        entry = {
            "command": shutil.which("uvx") or "uvx",
            "args": ["syke", "--user", user_id, "serve", "--transport", "stdio"],
        }
    else:
        entry = {
            "command": shutil.which("syke") or "syke",
            "args": ["--user", user_id, "serve", "--transport", "stdio"],
        }

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        entry.setdefault("env", {})["ANTHROPIC_API_KEY"] = api_key

    return entry


def inject_profile(profile: UserProfile, target_dir: str, fmt: str = "claude-md") -> Path:
    """Inject a formatted profile into a target directory.

    Args:
        profile: The user profile to inject
        target_dir: Target directory path
        fmt: Format to use (claude-md or user-md)

    Returns:
        Path to the written file
    """
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    filename = {
        "claude-md": "CLAUDE.md",
        "user-md": "USER.md",
    }.get(fmt, "CLAUDE.md")

    content = format_profile(profile, fmt)
    file_path = target / filename
    file_path.write_text(content)

    return file_path


def inject_mcp_config(user_id: str, source_install: bool = False) -> Path:
    """Inject Syke MCP server config into ~/.claude.json.

    Merges the syke server entry into the existing mcpServers key,
    preserving any other servers the user already has configured.

    Args:
        user_id: User identifier for the MCP server.
        source_install: True when running from a git clone / editable install.

    Returns:
        Path to the updated config file.
    """
    config_path = CLAUDE_USER_CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        config = json.loads(config_path.read_text())
    else:
        config = {}

    mcp_servers = config.setdefault("mcpServers", {})
    mcp_servers["syke"] = _build_server_entry(user_id, source_install=source_install)

    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return config_path


def inject_mcp_config_desktop(user_id: str, source_install: bool = False) -> Path | None:
    """Inject Syke MCP server config into Claude Desktop.

    Works on macOS (~/Library/Application Support/Claude/) and
    Linux (~/.config/Claude/). Returns None if Claude Desktop is not installed.

    Args:
        user_id: User identifier for the MCP server.
        source_install: True when running from a git clone / editable install.

    Returns:
        Path to the updated config file, or None if Claude Desktop
        is not installed.
    """
    config_path = CLAUDE_DESKTOP_CONFIG_PATH
    if not config_path.parent.exists():
        return None  # Claude Desktop not installed

    if config_path.exists():
        config = json.loads(config_path.read_text())
    else:
        config = {}

    mcp_servers = config.setdefault("mcpServers", {})
    mcp_servers["syke"] = _build_server_entry(user_id, source_install=source_install)

    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return config_path


def inject_mcp_config_project(user_id: str, project_root: Path) -> Path:
    """Inject Syke MCP into project-level .mcp.json.

    Project-scoped MCP servers are defined in .mcp.json at the project root.
    Always uses sys.executable (source install context — venv python won't be
    on PATH when Claude Code spawns the MCP subprocess).

    Returns:
        Path to the created/updated .mcp.json file.
    """
    mcp_path = project_root / ".mcp.json"

    if mcp_path.exists():
        config = json.loads(mcp_path.read_text())
    else:
        config = {}

    mcp_servers = config.setdefault("mcpServers", {})
    mcp_servers["syke"] = _build_server_entry(user_id, source_install=True)

    mcp_path.write_text(json.dumps(config, indent=2) + "\n")
    return mcp_path


def _contains_syke_hook(entry: dict, marker: str) -> bool:
    """Check if a hook entry (old or new format) contains a syke script."""
    # New format: {"matcher": {}, "hooks": [{"command": "..."}]}
    for hook in entry.get("hooks", []):
        if marker in hook.get("command", ""):
            return True
    # Old format fallback: {"type": "command", "command": "..."}
    if marker in entry.get("command", ""):
        return True
    return False


def inject_hooks_config(project_root: Path) -> Path:
    """Inject Syke lifecycle hooks into ~/.claude/settings.json.

    Adds SessionStart and Stop hook entries pointing to our shell scripts.
    Merges with existing hooks — never overwrites the user's other hooks.

    Returns:
        Path to the updated settings file.
    """
    settings_path = CLAUDE_SETTINGS_PATH
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})

    start_script = str(project_root / ".claude" / "hooks" / "syke-session-start.sh")
    stop_script = str(project_root / ".claude" / "hooks" / "syke-session-stop.sh")

    # SessionStart — append our hook, don't overwrite existing ones
    session_start = hooks.setdefault("SessionStart", [])
    # Remove any existing syke hooks (old or new format) to avoid duplicates
    session_start = [h for h in session_start if not _contains_syke_hook(h, "syke-session-start")]
    session_start.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": f"bash {start_script}"}],
    })
    hooks["SessionStart"] = session_start

    # Stop — append our hook
    stop = hooks.setdefault("Stop", [])
    stop = [h for h in stop if not _contains_syke_hook(h, "syke-session-stop")]
    stop.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": f"bash {stop_script}"}],
    })
    hooks["Stop"] = stop

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return settings_path
