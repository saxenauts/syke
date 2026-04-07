from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from syke.observe.catalog import SourceSpec, active_sources, discovered_roots, iter_discovered_files
from syke.observe.seeds import get_seed_adapter_md_path

logger = logging.getLogger(__name__)


def get_deployed_adapter_md_path(
    source: str,
    *,
    adapters_dir: Path | None = None,
) -> Path | None:
    """Return the deployed adapter markdown for *source*, if present."""
    if adapters_dir is None:
        return None
    md = adapters_dir / source / "adapter.md"
    return md if md.is_file() else None


@dataclass
class HarnessHealth:
    source: str
    status: str
    last_check: datetime
    files_found: int = 0
    latest_file_mtime: float | None = None
    error: str | None = None
    details: dict[str, object] = field(default_factory=dict)


class HarnessRegistry:
    def __init__(
        self,
        *,
        dynamic_adapters_dir: Path | None = None,
    ):
        self.dynamic_adapters_dir: Path | None = (
            dynamic_adapters_dir.expanduser().resolve()
            if dynamic_adapters_dir is not None
            else None
        )
        self._sources: tuple[SourceSpec, ...] = active_sources()
        self._sources_by_id: dict[str, SourceSpec] = {spec.source: spec for spec in self._sources}

    def list_harnesses(self) -> list[SourceSpec]:
        return list(self._sources)

    def get(self, source: str) -> SourceSpec | None:
        return self._sources_by_id.get(source)

    def by_format_cluster(self, cluster: str) -> list[SourceSpec]:
        return [spec for spec in self._sources if spec.format_cluster == cluster]

    def by_status(self, status: str) -> list[SourceSpec]:
        return [spec for spec in self._sources if spec.status == status]

    def active_harnesses(self) -> list[SourceSpec]:
        return self.by_status("active")

    def health_summary(self) -> dict[str, str]:
        return {spec.source: self.check_health(spec.source).status for spec in self._sources}

    def check_health(self, source: str) -> HarnessHealth:
        spec = self.get(source)
        now = datetime.now()
        if spec is None:
            return HarnessHealth(source=source, status="not_installed", last_check=now)

        files = iter_discovered_files(spec)
        if not files:
            return HarnessHealth(
                source=source,
                status="not_installed",
                last_check=now,
                error="No source artifacts found",
                details={"roots": [str(root) for root in discovered_roots(spec)]},
            )

        latest = max(files, key=lambda path: (path.stat().st_mtime, str(path)))
        has_adapter = (
            get_deployed_adapter_md_path(source, adapters_dir=self.dynamic_adapters_dir)
            is not None
            or get_seed_adapter_md_path(source) is not None
        )
        return HarnessHealth(
            source=source,
            status="healthy" if has_adapter else "no_adapter",
            last_check=now,
            files_found=len(files),
            latest_file_mtime=latest.stat().st_mtime,
            error=None if has_adapter else "No deployed adapter",
            details={
                "roots": [str(root) for root in discovered_roots(spec)],
            },
        )

    def check_all_health(self) -> dict[str, HarnessHealth]:
        return {spec.source: self.check_health(spec.source) for spec in self._sources}


__all__ = [
    "HarnessHealth",
    "HarnessRegistry",
]
