"""Pi (pi.dev / Piebald) harness adapter — stub.

Pi uses a TypeScript extension/skill system. No standardized local config
directory has been identified. This is a detection-only stub — install
will be implemented once Pi's config format stabilizes.

Pi supports MCP servers, so the long-term approach may be an MCP adapter
rather than file-based injection.
"""

from __future__ import annotations

import logging
from pathlib import Path

from syke.distribution.harness.base import AdapterResult, AdapterStatus, HarnessAdapter

log = logging.getLogger(__name__)

# Pi doesn't have a well-defined config directory yet.
# Check for common locations.
PI_POSSIBLE_HOMES = [
    Path.home() / ".pi",
    Path.home() / ".config" / "pi",
    Path.home() / ".config" / "piebald",
]


class PiAdapter(HarnessAdapter):
    name = "pi"
    display_name = "Pi (pi.dev)"
    protocol = "unknown"
    protocol_version = "0.0"
    has_native_memory = False

    def _find_home(self) -> Path | None:
        """Try to find Pi's config directory."""
        for p in PI_POSSIBLE_HOMES:
            if p.exists():
                return p
        return None

    def detect(self) -> bool:
        """Pi is installed if any known config directory exists."""
        return self._find_home() is not None

    def install(
        self, memex: str | None = None, skill_content: str | None = None
    ) -> AdapterResult:
        result = AdapterResult()
        home = self._find_home()

        if not home:
            result.skipped.append("Pi not installed (no config directory found)")
            return result

        # Stub: Pi's extension format isn't stable enough to write to yet.
        result.skipped.append(
            f"Pi detected at {home} but install not yet implemented — "
            "extension format still evolving"
        )
        return result

    def status(self) -> AdapterStatus:
        home = self._find_home()
        return AdapterStatus(
            name=self.name,
            detected=home is not None,
            connected=False,
            native_memory=self.has_native_memory,
            notes=f"Config at {home}" if home else "No config directory found",
        )

    def uninstall(self) -> bool:
        # Nothing to uninstall yet
        return True
