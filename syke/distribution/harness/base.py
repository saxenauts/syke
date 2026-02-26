"""Harness adapter base — protocol-resilient platform integration.

Harness adapters connect Syke to AI agent platforms. Each adapter knows
how to detect, install, check, and remove Syke from a specific platform.

Design principles:
  - A/B test mode: Syke coexists with native memory, never replaces it
  - Protocol-resilient: adapters declare protocol + version, so format
    changes are handled per-adapter without touching the interface
  - Minimal: detect → install → status → uninstall. That's it.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class AdapterResult:
    """Result of an install or uninstall operation."""

    installed: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.installed) > 0 and len(self.warnings) == 0


@dataclass
class AdapterStatus:
    """Health check for a single platform integration."""

    name: str
    detected: bool  # Is the platform installed on this system?
    connected: bool  # Is Syke integrated into it?
    native_memory: bool  # Does the platform have its own memory system?
    files: list[Path] = field(default_factory=list)  # Files we manage
    notes: str = ""


class HarnessAdapter(ABC):
    """Base class for platform harness adapters.

    Subclasses override the four abstract methods and set the class-level
    metadata fields. The registry discovers adapters automatically.

    Metadata fields help the system adapt when protocols change —
    bump protocol_version and update install() logic, not the interface.
    """

    # Override in every subclass
    name: str  # "hermes", "amp", "roo", "goose", "claude-desktop"
    display_name: str  # "Hermes Agent", "Amp Code"
    protocol: str  # "agentskills", "markdown-rules", "yaml-config"
    protocol_version: str  # version of the format we target
    has_native_memory: bool  # True if platform has its own memory

    @abstractmethod
    def detect(self) -> bool:
        """Check if this platform is installed on the system."""
        ...

    @abstractmethod
    def install(
        self, memex: str | None = None, skill_content: str | None = None
    ) -> AdapterResult:
        """Install Syke into this platform.

        A/B mode: coexist with native memory, never replace.

        Args:
            memex: Current memex content (for adapters that inline it).
            skill_content: Override SKILL.md content (adapter uses its own by default).
        """
        ...

    @abstractmethod
    def status(self) -> AdapterStatus:
        """Health check for this integration."""
        ...

    @abstractmethod
    def uninstall(self) -> bool:
        """Clean removal of all Syke files from this platform."""
        ...
