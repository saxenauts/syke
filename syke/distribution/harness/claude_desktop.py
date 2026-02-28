"""Claude Desktop harness adapter — cowork and agent mode context.

Claude Desktop loads context via:
  - MCP servers in ~/Library/Application Support/Claude/claude_desktop_config.json
  - Trusted folders (agent mode / cowork)
  - Does NOT read ~/.claude/CLAUDE.md automatically (unlike Claude Code)

Agent mode and cowork mode read skills from ~/.claude/skills/ (shared
with Claude Code). The main gap is that Desktop doesn't follow @include
in CLAUDE.md, so the memex isn't automatically available.

Current strategy: ensure ~/.syke/data/{user}/ is in the trusted folders list
so Desktop can access the memex file when operating in agent/cowork mode.

NOTE: Claude Chat (web) has no local config — not automatable. Users must
paste custom instructions manually via the Settings UI.

TODO (deferred): Trusted folders alone are insufficient — Desktop can
ACCESS files but doesn't AUTO-READ them. For real cowork integration:
  1. Register Syke as an MCP server in claude_desktop_config.json so
     Desktop can call syke ask/context/record as tools.
  2. This was removed earlier; add back when MCP server infra is ready.
  3. Same MCP server would serve other integrations (Cursor, Windsurf, etc.).
  4. Bug #2 (subprocess nesting) may also surface here — clean_claude_env()
     should handle it, but needs manual verification from a Desktop session.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from syke.distribution.harness.base import AdapterResult, AdapterStatus, HarnessAdapter

log = logging.getLogger(__name__)

CLAUDE_DESKTOP_CONFIG = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Claude"
    / "claude_desktop_config.json"
)


class ClaudeDesktopAdapter(HarnessAdapter):
    name = "claude-desktop"
    display_name = "Claude Desktop"
    protocol = "json-config"
    protocol_version = "1.0"
    has_native_memory = True  # Claude Desktop has Memory feature (beta)

    def detect(self) -> bool:
        """Claude Desktop is installed if the config file exists."""
        return CLAUDE_DESKTOP_CONFIG.exists()

    def install(
        self, memex: str | None = None, skill_content: str | None = None
    ) -> AdapterResult:
        result = AdapterResult()

        if not self.detect():
            result.skipped.append("Claude Desktop not installed")
            return result

        try:
            config = json.loads(CLAUDE_DESKTOP_CONFIG.read_text())
        except (json.JSONDecodeError, OSError) as e:
            result.warnings.append(f"Could not read config: {e}")
            return result

        # Ensure Syke data dir is in trusted folders
        from syke.config import DATA_DIR

        syke_data = str(DATA_DIR)
        prefs = config.setdefault("preferences", {})
        trusted = prefs.setdefault("localAgentModeTrustedFolders", [])

        if syke_data not in trusted:
            trusted.append(syke_data)
            try:
                CLAUDE_DESKTOP_CONFIG.write_text(json.dumps(config, indent=2) + "\n")
                result.installed.append(CLAUDE_DESKTOP_CONFIG)
                log.info("Added Syke data dir to Claude Desktop trusted folders")
            except OSError as e:
                result.warnings.append(f"Config write: {e}")
        else:
            result.installed.append(CLAUDE_DESKTOP_CONFIG)
            log.debug("Syke data dir already in trusted folders")

        return result

    def status(self) -> AdapterStatus:
        detected = self.detect()
        connected = False
        notes = ""

        if detected:
            try:
                config = json.loads(CLAUDE_DESKTOP_CONFIG.read_text())
                from syke.config import DATA_DIR

                trusted = config.get("preferences", {}).get(
                    "localAgentModeTrustedFolders", []
                )
                connected = str(DATA_DIR) in trusted

                # Report MCP server count
                mcp_count = len(config.get("mcpServers", {}))
                notes = f"{mcp_count} MCP servers configured"
            except (json.JSONDecodeError, OSError):
                notes = "Could not read config"

        return AdapterStatus(
            name=self.name,
            detected=detected,
            connected=connected,
            native_memory=self.has_native_memory,
            files=[CLAUDE_DESKTOP_CONFIG] if connected else [],
            notes=notes,
        )

    def uninstall(self) -> bool:
        try:
            if not CLAUDE_DESKTOP_CONFIG.exists():
                return True

            config = json.loads(CLAUDE_DESKTOP_CONFIG.read_text())
            from syke.config import DATA_DIR

            trusted = config.get("preferences", {}).get(
                "localAgentModeTrustedFolders", []
            )
            syke_data = str(DATA_DIR)

            if syke_data in trusted:
                trusted.remove(syke_data)
                CLAUDE_DESKTOP_CONFIG.write_text(json.dumps(config, indent=2) + "\n")
                log.info("Removed Syke data dir from Claude Desktop trusted folders")

            return True
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Claude Desktop uninstall failed: %s", e)
            return False
