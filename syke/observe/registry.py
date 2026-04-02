from __future__ import annotations

import importlib.util
import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from syke.db import SykeDB
from syke.observe.adapter import ObserveAdapter
from syke.observe.catalog import SourceSpec, active_sources, discovered_roots, iter_discovered_files
from syke.observe.seeds import get_seed_adapter_path

logger = logging.getLogger(__name__)

AdapterConstructor = type[ObserveAdapter]
_dynamic_adapters_dir: Path | None = None


def set_dynamic_adapters_dir(path: Path | None) -> None:
    global _dynamic_adapters_dir
    _dynamic_adapters_dir = path.expanduser().resolve() if path is not None else None


def get_deployed_adapter_path(
    source: str,
    *,
    dynamic_adapters_dir: Path | None = None,
) -> Path | None:
    base = dynamic_adapters_dir or _dynamic_adapters_dir
    if base is None:
        return None
    adapter_py = base / source / "adapter.py"
    return adapter_py if adapter_py.is_file() else None


def get_adapter_path(source: str, *, dynamic_adapters_dir: Path | None = None) -> Path | None:
    deployed = get_deployed_adapter_path(source, dynamic_adapters_dir=dynamic_adapters_dir)
    if deployed is not None:
        return deployed
    return get_seed_adapter_path(source)


def get_adapter_class(
    source: str,
    *,
    dynamic_adapters_dir: Path | None = None,
) -> AdapterConstructor | None:
    adapter_py = get_adapter_path(source, dynamic_adapters_dir=dynamic_adapters_dir)
    if adapter_py is None:
        return None
    return _load_adapter_class(adapter_py, source)


def _load_adapter_class(adapter_py: Path, source: str) -> AdapterConstructor | None:
    spec = importlib.util.spec_from_file_location(f"syke_adapter_{source}", adapter_py)
    if spec is None or spec.loader is None:
        return None

    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        logger.warning("Failed to load adapter for %s from %s", source, adapter_py, exc_info=True)
        return None

    for _name, obj in inspect.getmembers(mod, inspect.isclass):
        if issubclass(obj, ObserveAdapter) and obj is not ObserveAdapter:
            return obj

    logger.warning("Adapter for %s at %s defines no ObserveAdapter subclass", source, adapter_py)
    return None


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
        descriptors_dir: Path | None = None,
        *,
        dynamic_adapters_dir: Path | None = None,
    ):
        _ = descriptors_dir
        self.dynamic_adapters_dir: Path | None = (
            dynamic_adapters_dir.expanduser().resolve()
            if dynamic_adapters_dir is not None
            else _dynamic_adapters_dir
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

    def get_adapter(self, source: str, db: SykeDB, user_id: str) -> ObserveAdapter | None:
        adapter_cls = get_adapter_class(source, dynamic_adapters_dir=self.dynamic_adapters_dir)
        if adapter_cls is None:
            return None
        return adapter_cls(db, user_id)

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
        adapter_path = get_adapter_path(source, dynamic_adapters_dir=self.dynamic_adapters_dir)
        return HarnessHealth(
            source=source,
            status="healthy" if adapter_path is not None else "no_adapter",
            last_check=now,
            files_found=len(files),
            latest_file_mtime=latest.stat().st_mtime,
            error=None if adapter_path is not None else "No deployed adapter",
            details={
                "roots": [str(root) for root in discovered_roots(spec)],
                "adapter_path": str(adapter_path) if adapter_path is not None else None,
            },
        )

    def check_all_health(self) -> dict[str, HarnessHealth]:
        return {spec.source: self.check_health(spec.source) for spec in self._sources}


__all__ = [
    "HarnessHealth",
    "HarnessRegistry",
    "get_adapter_class",
    "get_adapter_path",
    "get_deployed_adapter_path",
    "set_dynamic_adapters_dir",
]
