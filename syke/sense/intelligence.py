"""Sense Intelligence — orchestrates discover → analyze → generate → test → deploy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from syke.sense.adapter_generator import AdapterGenerator, GeneratedAdapter
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
    generated: GeneratedAdapter | None = None


class SenseIntelligence:
    def __init__(
        self,
        home: Path | None = None,
        llm_fn: callable | None = None,
        adapters_dir: Path | None = None,
    ):
        self._discovery = SenseDiscovery(home=home)
        self._analyzer = SenseAnalyzer()
        self._sandbox = AdapterSandbox()
        self._generator = AdapterGenerator(sandbox=self._sandbox, llm_fn=llm_fn)
        self._adapters_dir = adapters_dir

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
            # Deploy: persist to disk
            deployed = self.deploy(source_name, generated)
            deploy_msg = " (deployed)" if deployed else " (deploy failed)"
            return ConnectResult(
                source_name=source_name,
                success=True,
                message=f"Adapter generated: {generated.sandbox_result.events_parsed} events parsed{deploy_msg}",
                generated=generated,
            )

        errors = (
            generated.sandbox_result.errors if generated.sandbox_result else ["Generation failed"]
        )
        return ConnectResult(
            source_name=source_name,
            success=False,
            message=f"Adapter failed sandbox: {'; '.join(errors)}",
            generated=generated,
        )

    def heal(self, source: str, samples: list[str]) -> ConnectResult:
        """Heal a broken adapter: analyze failure samples → regenerate → test."""
        analysis = self._analyzer.analyze(samples)
        generated = self._generator.generate(analysis, samples, source_name=source)

        if generated.sandbox_result and generated.sandbox_result.success:
            deployed = self.deploy(source, generated)
            deploy_msg = " (deployed)" if deployed else " (deploy failed)"
            return ConnectResult(
                source_name=source,
                success=True,
                message=f"Adapter healed successfully{deploy_msg}",
                generated=generated,
            )

        errors = generated.sandbox_result.errors if generated.sandbox_result else ["Healing failed"]
        return ConnectResult(
            source_name=source,
            success=False,
            message=f"Healing failed: {'; '.join(errors)}",
            generated=generated,
        )

    def deploy(self, source_name: str, generated: GeneratedAdapter) -> bool:
        """Persist a generated adapter to disk.

        Writes adapter.py, descriptor.toml, and test_adapter.py to
        the adapters directory. Returns True on success.
        """
        adapters_dir = self._adapters_dir
        if adapters_dir is None:
            return False

        target = adapters_dir / source_name
        try:
            target.mkdir(parents=True, exist_ok=True)
            (target / "adapter.py").write_text(generated.adapter_code, encoding="utf-8")
            (target / "descriptor.toml").write_text(generated.descriptor_toml, encoding="utf-8")
            if generated.test_code:
                (target / "test_adapter.py").write_text(generated.test_code, encoding="utf-8")
            logger.info("Deployed adapter for %s to %s", source_name, target)
            return True
        except OSError:
            logger.warning("Failed to deploy adapter for %s", source_name, exc_info=True)
            return False

    @staticmethod
    def list_deployed(adapters_dir: Path) -> list[str]:
        """List source names with deployed adapters on disk."""
        if not adapters_dir.is_dir():
            return []
        return sorted(
            d.name for d in adapters_dir.iterdir() if d.is_dir() and (d / "adapter.py").is_file()
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
