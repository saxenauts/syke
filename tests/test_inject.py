"""Tests for MCP and hooks auto-injection."""

from __future__ import annotations

import json
import os
import sys

from syke.distribution.inject import (
    inject_hooks_config,
    inject_mcp_config,
    inject_mcp_config_desktop,
    inject_mcp_config_project,
)


# --- Global MCP injection (inject_mcp_config) ---


def test_inject_mcp_config_creates_new_pip(tmp_path, monkeypatch):
    """Creates ~/.claude.json with absolute 'syke' path when uvx is not available."""
    config_path = tmp_path / ".claude.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_USER_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("syke.distribution.inject._detect_install_method", lambda: "pip")
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/local/bin/{cmd}")

    result = inject_mcp_config("testuser")
    assert result == config_path
    assert config_path.exists()

    config = json.loads(config_path.read_text())
    assert "syke" in config["mcpServers"]
    entry = config["mcpServers"]["syke"]
    assert entry["command"] == "/usr/local/bin/syke"
    assert entry["args"] == ["--user", "testuser", "serve", "--transport", "stdio"]
    assert "cwd" not in entry
    assert "env" not in entry


def test_inject_mcp_config_creates_new_source(tmp_path, monkeypatch):
    """Creates ~/.claude.json with sys.executable for source installs."""
    config_path = tmp_path / ".claude.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_USER_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = inject_mcp_config("testuser", source_install=True)
    assert result == config_path

    config = json.loads(config_path.read_text())
    entry = config["mcpServers"]["syke"]
    assert entry["command"] == sys.executable
    assert entry["args"] == ["-m", "syke", "--user", "testuser", "serve", "--transport", "stdio"]
    assert "cwd" not in entry
    assert "PYTHONPATH" in entry.get("env", {})


def test_inject_mcp_config_merges(tmp_path, monkeypatch):
    """Preserves existing MCP servers when adding syke."""
    config_path = tmp_path / ".claude.json"
    config_path.write_text(json.dumps({
        "mcpServers": {
            "other-server": {"command": "other", "args": []}
        },
        "someOtherSetting": True,
    }))
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_USER_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    inject_mcp_config("testuser")

    config = json.loads(config_path.read_text())
    assert "other-server" in config["mcpServers"]
    assert "syke" in config["mcpServers"]
    assert config["someOtherSetting"] is True


def test_inject_mcp_config_with_api_key(tmp_path, monkeypatch):
    """Does NOT bake ANTHROPIC_API_KEY into MCP config even when it is set.

    The MCP server uses Claude Code session auth via the Agent SDK. Injecting
    the key here would create stale-key risk and conflict with session-auth-first
    design.
    """
    config_path = tmp_path / ".claude.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_USER_CONFIG_PATH", config_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-123")

    inject_mcp_config("testuser")

    config = json.loads(config_path.read_text())
    entry = config["mcpServers"]["syke"]
    assert "env" not in entry or "ANTHROPIC_API_KEY" not in entry.get("env", {})


def test_inject_mcp_config_no_api_key(tmp_path, monkeypatch):
    """Omits env block when ANTHROPIC_API_KEY is not set."""
    config_path = tmp_path / ".claude.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_USER_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    inject_mcp_config("testuser")

    config = json.loads(config_path.read_text())
    assert "env" not in config["mcpServers"]["syke"]


# --- uvx MCP injection tests ---


def test_inject_mcp_config_uvx(tmp_path, monkeypatch):
    """Uses absolute 'uvx' path when uvx is available and not source install."""
    config_path = tmp_path / ".claude.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_USER_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("syke.distribution.inject._detect_install_method", lambda: "uvx")
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/local/bin/{cmd}")

    inject_mcp_config("testuser")

    config = json.loads(config_path.read_text())
    entry = config["mcpServers"]["syke"]
    assert entry["command"] == "/usr/local/bin/uvx"
    assert entry["args"] == ["syke", "--user", "testuser", "serve", "--transport", "stdio"]
    assert "cwd" not in entry


def test_inject_mcp_config_no_uvx_falls_back(tmp_path, monkeypatch):
    """Falls back to absolute 'syke' path when uvx is not on PATH."""
    config_path = tmp_path / ".claude.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_USER_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("syke.distribution.inject._detect_install_method", lambda: "pip")
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/local/bin/{cmd}")

    inject_mcp_config("testuser")

    config = json.loads(config_path.read_text())
    entry = config["mcpServers"]["syke"]
    assert entry["command"] == "/usr/local/bin/syke"
    assert entry["args"] == ["--user", "testuser", "serve", "--transport", "stdio"]


def test_inject_mcp_config_source_install_overrides_uvx(tmp_path, monkeypatch):
    """source_install=True takes precedence over uvx detection."""
    config_path = tmp_path / ".claude.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_USER_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("syke.distribution.inject._detect_install_method", lambda: "uvx")

    inject_mcp_config("testuser", source_install=True)

    config = json.loads(config_path.read_text())
    entry = config["mcpServers"]["syke"]
    assert entry["command"] == sys.executable
    assert entry["args"] == ["-m", "syke", "--user", "testuser", "serve", "--transport", "stdio"]
    assert "PYTHONPATH" in entry.get("env", {})


def test_inject_mcp_config_uvx_resolves_absolute_path(tmp_path, monkeypatch):
    """Verifies uvx and pip commands are resolved to absolute paths, not bare names."""
    config_path = tmp_path / ".claude.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_USER_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda cmd: f"/home/user/.local/bin/{cmd}")

    # uvx tier
    monkeypatch.setattr("syke.distribution.inject._detect_install_method", lambda: "uvx")
    inject_mcp_config("testuser")
    config = json.loads(config_path.read_text())
    cmd = config["mcpServers"]["syke"]["command"]
    assert os.path.isabs(cmd), f"uvx command is not absolute: {cmd}"

    # pip tier
    monkeypatch.setattr("syke.distribution.inject._detect_install_method", lambda: "pip")
    inject_mcp_config("testuser")
    config = json.loads(config_path.read_text())
    cmd = config["mcpServers"]["syke"]["command"]
    assert os.path.isabs(cmd), f"pip command is not absolute: {cmd}"


def test_inject_mcp_config_which_returns_none_fallback(tmp_path, monkeypatch):
    """Falls back to bare name when shutil.which returns None."""
    config_path = tmp_path / ".claude.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_USER_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    monkeypatch.setattr("syke.distribution.inject._detect_install_method", lambda: "pip")

    inject_mcp_config("testuser")
    config = json.loads(config_path.read_text())
    assert config["mcpServers"]["syke"]["command"] == "syke"


def test_inject_mcp_source_install_with_api_key(tmp_path, monkeypatch):
    """Source install env contains PYTHONPATH but NOT ANTHROPIC_API_KEY."""
    config_path = tmp_path / ".claude.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_USER_CONFIG_PATH", config_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-456")

    inject_mcp_config("testuser", source_install=True)

    config = json.loads(config_path.read_text())
    env = config["mcpServers"]["syke"]["env"]
    assert "PYTHONPATH" in env
    assert "ANTHROPIC_API_KEY" not in env


# --- Hooks injection (unchanged logic, same tests) ---


def test_inject_hooks_creates_new(tmp_path, monkeypatch):
    """Creates settings.json with hooks if it doesn't exist."""
    settings_path = tmp_path / ".claude" / "settings.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_SETTINGS_PATH", settings_path)

    result = inject_hooks_config(tmp_path / "project")
    assert result == settings_path

    settings = json.loads(settings_path.read_text())
    assert "SessionStart" in settings["hooks"]
    assert "Stop" in settings["hooks"]
    assert len(settings["hooks"]["SessionStart"]) == 1
    assert len(settings["hooks"]["Stop"]) == 1
    # New matcher-wrapped format
    start_entry = settings["hooks"]["SessionStart"][0]
    assert "matcher" in start_entry
    assert "syke-session-start" in start_entry["hooks"][0]["command"]
    stop_entry = settings["hooks"]["Stop"][0]
    assert "matcher" in stop_entry
    assert "syke-session-stop" in stop_entry["hooks"][0]["command"]


def test_inject_hooks_merges(tmp_path, monkeypatch):
    """Preserves existing hooks when adding syke hooks."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {"matcher": "", "hooks": [{"type": "command", "command": "echo existing-hook"}]}
            ],
            "PreToolUse": [
                {"matcher": "", "hooks": [{"type": "command", "command": "echo pre-tool"}]}
            ],
        }
    }))
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_SETTINGS_PATH", settings_path)

    inject_hooks_config(tmp_path / "project")

    settings = json.loads(settings_path.read_text())
    # Existing SessionStart hook preserved + syke hook added
    entries = settings["hooks"]["SessionStart"]
    commands = [e["hooks"][0]["command"] for e in entries]
    assert "echo existing-hook" in commands
    assert any("syke-session-start" in c for c in commands)
    # PreToolUse untouched
    assert len(settings["hooks"]["PreToolUse"]) == 1
    # Stop hook added
    assert len(settings["hooks"]["Stop"]) == 1


def test_inject_hooks_idempotent(tmp_path, monkeypatch):
    """Running inject_hooks_config twice doesn't duplicate syke hooks."""
    settings_path = tmp_path / ".claude" / "settings.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_SETTINGS_PATH", settings_path)

    inject_hooks_config(tmp_path / "project")
    inject_hooks_config(tmp_path / "project")

    settings = json.loads(settings_path.read_text())
    # Should have exactly 1 syke hook per event, not 2
    assert len(settings["hooks"]["SessionStart"]) == 1
    assert len(settings["hooks"]["Stop"]) == 1


# --- Claude Desktop MCP injection tests ---


def test_inject_mcp_config_desktop_creates_new(tmp_path, monkeypatch):
    """Creates claude_desktop_config.json with syke MCP server."""
    config_path = tmp_path / "Claude" / "claude_desktop_config.json"
    config_path.parent.mkdir(parents=True)

    import types
    fake_sys = types.ModuleType("fake_sys")
    fake_sys.platform = "darwin"
    fake_sys.executable = sys.executable
    monkeypatch.setattr("syke.distribution.inject.sys", fake_sys)
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_DESKTOP_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = inject_mcp_config_desktop("testuser", source_install=True)
    assert result == config_path
    assert config_path.exists()

    config = json.loads(config_path.read_text())
    assert "syke" in config["mcpServers"]
    entry = config["mcpServers"]["syke"]
    assert entry["command"] == sys.executable
    assert "cwd" not in entry
    assert "PYTHONPATH" in entry.get("env", {})


def test_inject_mcp_config_desktop_pip_install(tmp_path, monkeypatch):
    """Uses absolute 'syke' path for pip installs in Desktop config."""
    config_path = tmp_path / "Claude" / "claude_desktop_config.json"
    config_path.parent.mkdir(parents=True)

    import types
    fake_sys = types.ModuleType("fake_sys")
    fake_sys.platform = "darwin"
    fake_sys.executable = sys.executable
    monkeypatch.setattr("syke.distribution.inject.sys", fake_sys)
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_DESKTOP_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("syke.distribution.inject._detect_install_method", lambda: "pip")
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/local/bin/{cmd}")

    inject_mcp_config_desktop("testuser", source_install=False)

    config = json.loads(config_path.read_text())
    entry = config["mcpServers"]["syke"]
    assert entry["command"] == "/usr/local/bin/syke"
    assert entry["args"] == ["--user", "testuser", "serve", "--transport", "stdio"]
    assert "cwd" not in entry


def test_inject_mcp_config_desktop_merges(tmp_path, monkeypatch):
    """Preserves existing MCP servers when adding syke to Claude Desktop."""
    config_path = tmp_path / "Claude" / "claude_desktop_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({
        "mcpServers": {
            "other-server": {"command": "other", "args": []}
        },
        "globalShortcut": "Ctrl+Space",
    }))

    import types
    fake_sys = types.ModuleType("fake_sys")
    fake_sys.platform = "darwin"
    fake_sys.executable = sys.executable
    monkeypatch.setattr("syke.distribution.inject.sys", fake_sys)
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_DESKTOP_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    inject_mcp_config_desktop("testuser")

    config = json.loads(config_path.read_text())
    assert "other-server" in config["mcpServers"]
    assert "syke" in config["mcpServers"]
    assert config["globalShortcut"] == "Ctrl+Space"


def test_inject_mcp_config_desktop_with_api_key(tmp_path, monkeypatch):
    """Desktop MCP config does NOT include ANTHROPIC_API_KEY even when it is set."""
    config_path = tmp_path / "Claude" / "claude_desktop_config.json"
    config_path.parent.mkdir(parents=True)

    import types
    fake_sys = types.ModuleType("fake_sys")
    fake_sys.platform = "darwin"
    fake_sys.executable = sys.executable
    monkeypatch.setattr("syke.distribution.inject.sys", fake_sys)
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_DESKTOP_CONFIG_PATH", config_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-desktop-key")

    inject_mcp_config_desktop("testuser")

    config = json.loads(config_path.read_text())
    entry = config["mcpServers"]["syke"]
    assert "env" not in entry or "ANTHROPIC_API_KEY" not in entry.get("env", {})


def test_inject_mcp_config_desktop_linux_path(tmp_path, monkeypatch):
    """Uses Linux config path on Linux when Claude Desktop dir exists."""
    config_path = tmp_path / ".config" / "Claude" / "claude_desktop_config.json"
    config_path.parent.mkdir(parents=True)

    monkeypatch.setattr("syke.distribution.inject.CLAUDE_DESKTOP_CONFIG_PATH", config_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = inject_mcp_config_desktop("testuser", source_install=True)
    assert result == config_path
    assert config_path.exists()

    config = json.loads(config_path.read_text())
    assert "syke" in config["mcpServers"]


def test_inject_mcp_config_desktop_skips_missing_dir(tmp_path, monkeypatch):
    """Returns None when Claude Desktop config directory doesn't exist."""
    config_path = tmp_path / "nonexistent" / "claude_desktop_config.json"
    monkeypatch.setattr("syke.distribution.inject.CLAUDE_DESKTOP_CONFIG_PATH", config_path)

    result = inject_mcp_config_desktop("testuser")
    assert result is None


# --- Project-level MCP injection tests ---


def test_inject_mcp_config_project_creates_new(tmp_path, monkeypatch):
    """Creates .mcp.json at project root even when it doesn't exist."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = inject_mcp_config_project("testuser", project_root)
    mcp_path = project_root / ".mcp.json"
    assert result == mcp_path
    assert mcp_path.exists()

    config = json.loads(mcp_path.read_text())
    entry = config["mcpServers"]["syke"]
    # Project always uses sys.executable (source install context)
    assert entry["command"] == sys.executable
    assert entry["args"] == ["-m", "syke", "--user", "testuser", "serve", "--transport", "stdio"]
    assert "cwd" not in entry
    assert "PYTHONPATH" in entry.get("env", {})


def test_inject_mcp_config_project_merges(tmp_path, monkeypatch):
    """Merges syke into existing .mcp.json, preserving other servers."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    mcp_path = project_root / ".mcp.json"
    mcp_path.write_text(json.dumps({
        "mcpServers": {
            "other-server": {"command": "other", "args": []}
        },
    }))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = inject_mcp_config_project("testuser", project_root)
    assert result == mcp_path

    config = json.loads(mcp_path.read_text())
    assert "syke" in config["mcpServers"]
    assert "other-server" in config["mcpServers"]
    assert config["mcpServers"]["syke"]["command"] == sys.executable


def test_inject_mcp_config_project_with_api_key(tmp_path, monkeypatch):
    """Project .mcp.json contains PYTHONPATH but NOT ANTHROPIC_API_KEY."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-project-key")

    inject_mcp_config_project("testuser", project_root)

    mcp_path = project_root / ".mcp.json"
    config = json.loads(mcp_path.read_text())
    env = config["mcpServers"]["syke"]["env"]
    assert "PYTHONPATH" in env
    assert "ANTHROPIC_API_KEY" not in env
