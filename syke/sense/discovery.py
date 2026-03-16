"""Sense discovery — scan filesystem for AI tool installations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWN_HARNESSES: dict[str, str] = {
    ".claude": "claude-code",
    ".codex": "codex",
    ".factory": "factory-droid",
    ".cursor": "cursor",
    ".hermes": "hermes",
    ".continue": "continue",
    ".gemini": "gemini",
    ".openclaw": "openclaw",
}

KNOWN_HARNESSES_NONSTANDARD: dict[str, str] = {
    ".local/share/opencode": "opencode",
    ".pi/agent": "pi",
}


@dataclass
class DiscoveryResult:
    path: Path
    format_guess: str  # "jsonl" | "json" | "sqlite" | "unknown"
    status: str  # "known" | "unknown"
    source_name: str | None = None


class SenseDiscovery:
    def __init__(self, home: Path | None = None):
        self._home = home or Path.home()

    def scan(self) -> list[DiscoveryResult]:
        results: list[DiscoveryResult] = []
        for dirname, source in KNOWN_HARNESSES.items():
            path = self._home / dirname
            if path.exists() and path.is_dir():
                fmt = self._guess_format(path)
                results.append(
                    DiscoveryResult(
                        path=path,
                        format_guess=fmt,
                        status="known",
                        source_name=source,
                    )
                )

        for relpath, source in KNOWN_HARNESSES_NONSTANDARD.items():
            path = self._home / relpath
            if path.exists():
                fmt = self._guess_format(path) if path.is_dir() else "sqlite"
                results.append(
                    DiscoveryResult(
                        path=path,
                        format_guess=fmt,
                        status="known",
                        source_name=source,
                    )
                )

        # Scan for unknown .aider* dirs
        for p in self._home.glob(".aider*"):
            if p.is_dir() and p.name not in KNOWN_HARNESSES:
                results.append(
                    DiscoveryResult(
                        path=p,
                        format_guess=self._guess_format(p),
                        status="unknown",
                        source_name=None,
                    )
                )
        return results

    def _guess_format(self, path: Path) -> str:
        for f in path.rglob("*.jsonl"):
            return "jsonl"
        for f in path.rglob("*.json"):
            return "json"
        for f in path.rglob("*.db"):
            return "sqlite"
        return "unknown"
