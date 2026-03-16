"""Sense Intelligence — orchestrates discover → analyze → generate → test → deploy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from syke.sense.adapter_generator import AdapterGenerator
from syke.sense.analyzer import SenseAnalyzer
from syke.sense.discovery import SenseDiscovery, DiscoveryResult
from syke.sense.sandbox import AdapterSandbox

logger = logging.getLogger(__name__)


@dataclass
class ConnectResult:
    source_name: str
    success: bool
    message: str
    discovery: DiscoveryResult | None = None


class SenseIntelligence:
    def __init__(
        self,
        home: Path | None = None,
        llm_fn: callable | None = None,
    ):
        self._discovery = SenseDiscovery(home=home)
        self._analyzer = SenseAnalyzer()
        self._sandbox = AdapterSandbox()
        self._generator = AdapterGenerator(sandbox=self._sandbox, llm_fn=llm_fn)

    def discover(self) -> list[DiscoveryResult]:
        """Discover all known AI harnesses on the system."""
        return self._discovery.scan()

    def connect(self, path: Path | str) -> ConnectResult:
        """Connect a new harness: discover → analyze → generate → test → deploy."""
        path = Path(path)
        if not path.exists():
            return ConnectResult(
                source_name="unknown",
                success=False,
                message=f"Path not found: {path}",
            )

        # Read samples
        samples = self._read_samples(path)
        if not samples:
            return ConnectResult(
                source_name="unknown",
                success=False,
                message="No data found to analyze",
            )

        # Analyze
        analysis = self._analyzer.analyze(samples)
        source_name = path.name.lstrip(".")

        # Generate
        generated = self._generator.generate(analysis, samples, source_name=source_name)

        if generated.sandbox_result and generated.sandbox_result.success:
            return ConnectResult(
                source_name=source_name,
                success=True,
                message=f"Adapter generated: {generated.sandbox_result.events_parsed} events parsed from samples",
            )

        errors = (
            generated.sandbox_result.errors if generated.sandbox_result else ["Generation failed"]
        )
        return ConnectResult(
            source_name=source_name,
            success=False,
            message=f"Adapter failed sandbox: {'; '.join(errors)}",
        )

    def heal(self, source: str, samples: list[str]) -> ConnectResult:
        """Heal a broken adapter: analyze failure samples → regenerate → test."""
        analysis = self._analyzer.analyze(samples)
        generated = self._generator.generate(analysis, samples, source_name=source)

        if generated.sandbox_result and generated.sandbox_result.success:
            return ConnectResult(
                source_name=source,
                success=True,
                message="Adapter healed successfully",
            )

        errors = generated.sandbox_result.errors if generated.sandbox_result else ["Healing failed"]
        return ConnectResult(
            source_name=source,
            success=False,
            message=f"Healing failed: {'; '.join(errors)}",
        )

    def _read_samples(self, path: Path, max_lines: int = 50) -> list[str]:
        """Read sample data from a directory."""
        samples: list[str] = []
        for ext in ("*.jsonl", "*.json"):
            for f in path.rglob(ext):
                try:
                    for line in f.open():
                        line = line.strip()
                        if line:
                            samples.append(line)
                            if len(samples) >= max_lines:
                                return samples
                except (OSError, UnicodeDecodeError):
                    continue
        return samples
