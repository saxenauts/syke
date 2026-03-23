from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

from syke.config_file import expand_path
from syke.db import SykeDB

logger = logging.getLogger(__name__)


class DiscoverRoot(Protocol):
    path: str
    include: list[str]
    priority: int


class DiscoverConfig(Protocol):
    roots: list[DiscoverRoot]


class HarnessDescriptor(Protocol):
    source: str
    format_cluster: str
    status: str
    discover: DiscoverConfig | None


class AdapterFactory(Protocol):
    def __call__(self, db: SykeDB, user_id: str) -> object: ...


class GetAdapterClass(Protocol):
    def __call__(self, source: str) -> AdapterFactory | None: ...


_descriptor_module: ModuleType = importlib.import_module("syke.observe.descriptor")
load_all_descriptors = cast(
    Callable[[Path], list[HarnessDescriptor]], _descriptor_module.load_all_descriptors
)
validate_descriptor = cast(
    Callable[[HarnessDescriptor], list[str]], _descriptor_module.validate_descriptor
)


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
    _descriptor_cache: dict[Path, tuple[HarnessDescriptor, ...]] = {}

    def __init__(self, descriptors_dir: Path | None = None):
        directory = descriptors_dir or (Path(__file__).parent / "descriptors")
        self.descriptors_dir: Path = directory.expanduser().resolve()
        self._descriptors: list[HarnessDescriptor] = list(
            self._load_descriptors(self.descriptors_dir)
        )
        self._descriptors_by_source: dict[str, HarnessDescriptor] = {
            desc.source: desc for desc in self._descriptors
        }

    @classmethod
    def _load_descriptors(cls, directory: Path) -> tuple[HarnessDescriptor, ...]:
        if directory not in cls._descriptor_cache:
            descriptors = load_all_descriptors(directory)
            for descriptor in descriptors:
                for warning in validate_descriptor(descriptor):
                    logger.warning("Descriptor %s: %s", descriptor.source, warning)
            cls._descriptor_cache[directory] = tuple(descriptors)
        return cls._descriptor_cache[directory]

    def list_harnesses(self) -> list[HarnessDescriptor]:
        return list(self._descriptors)

    def get(self, source: str) -> HarnessDescriptor | None:
        return self._descriptors_by_source.get(source)

    def by_format_cluster(self, cluster: str) -> list[HarnessDescriptor]:
        return [desc for desc in self._descriptors if desc.format_cluster == cluster]

    def by_status(self, status: str) -> list[HarnessDescriptor]:
        return [desc for desc in self._descriptors if desc.status == status]

    def active_harnesses(self) -> list[HarnessDescriptor]:
        return self.by_status("active")

    def get_adapter(self, source: str, db: SykeDB, user_id: str) -> object | None:
        get_adapter_class = cast(
            GetAdapterClass,
            importlib.import_module("syke.observe.adapter_registry").get_adapter_class,
        )

        descriptor = self.get(source)
        if descriptor is None:
            return None

        if descriptor.status not in {"active"}:
            return None

        adapter_cls = get_adapter_class(source)
        if adapter_cls is not None:
            return adapter_cls(db, user_id)

        logger.warning("Harness %s is active but has no adapter implementation", source)
        return None

    def health_summary(self) -> dict[str, str]:
        return {desc.source: desc.status for desc in self._descriptors}

    def check_health(self, source: str) -> HarnessHealth:
        descriptor = self.get(source)
        now = datetime.now()
        if descriptor is None:
            return HarnessHealth(source=source, status="not_installed", last_check=now)

        if descriptor.status == "stub":
            return HarnessHealth(source=source, status="stub", last_check=now)

        if descriptor.status in {"planned", "research", "deprecated"}:
            details: dict[str, object] = {}
            if descriptor.status != "planned":
                details["descriptor_status"] = descriptor.status
            return HarnessHealth(
                source=source,
                status="planned",
                last_check=now,
                details=details,
            )

        if descriptor.format_cluster == "cloud_api":
            return HarnessHealth(source=source, status="cloud_api", last_check=now)

        if descriptor.format_cluster == "sqlite":
            return self._check_sqlite_health(descriptor, now)

        if descriptor.format_cluster in {"jsonl", "json", "multi_file", "markdown"}:
            return self._check_file_health(descriptor, now)

        return HarnessHealth(
            source=source,
            status="planned",
            last_check=now,
            error=f"Unsupported format cluster: {descriptor.format_cluster}",
            details={"format_cluster": descriptor.format_cluster},
        )

    def check_all_health(self) -> dict[str, HarnessHealth]:
        return {desc.source: self.check_health(desc.source) for desc in self._descriptors}

    def _check_file_health(
        self, descriptor: HarnessDescriptor, checked_at: datetime
    ) -> HarnessHealth:
        discover_cfg = descriptor.discover
        roots = discover_cfg.roots if discover_cfg is not None else []
        if not roots:
            return HarnessHealth(
                source=descriptor.source,
                status="no_data",
                last_check=checked_at,
                error="No discover roots configured",
            )

        candidates: dict[Path, tuple[float, Path]] = {}
        existing_roots: list[str] = []
        missing_roots: list[str] = []

        for root in roots:
            root_path = expand_path(root.path)
            if not root_path.exists() or not root_path.is_dir():
                missing_roots.append(str(root_path))
                continue

            existing_roots.append(str(root_path))
            for pattern in root.include:
                for path in root_path.glob(pattern):
                    if not path.is_file():
                        continue
                    candidates[path] = (path.stat().st_mtime, path)

        if not existing_roots:
            return HarnessHealth(
                source=descriptor.source,
                status="not_installed",
                last_check=checked_at,
                error="No configured roots exist",
                details={"roots": missing_roots},
            )

        if not candidates:
            return HarnessHealth(
                source=descriptor.source,
                status="no_data",
                last_check=checked_at,
                details={"roots": existing_roots},
            )

        latest_mtime, latest_path = max(
            candidates.values(), key=lambda item: (item[0], str(item[1]))
        )

        try:
            self._probe_file_parse(descriptor.format_cluster, latest_path)
        except Exception as exc:
            return HarnessHealth(
                source=descriptor.source,
                status="parse_error",
                last_check=checked_at,
                files_found=len(candidates),
                latest_file_mtime=latest_mtime,
                error=str(exc),
                details={
                    "latest_file": str(latest_path),
                    "roots": existing_roots,
                },
            )

        return HarnessHealth(
            source=descriptor.source,
            status="healthy",
            last_check=checked_at,
            files_found=len(candidates),
            latest_file_mtime=latest_mtime,
            details={
                "latest_file": str(latest_path),
                "roots": existing_roots,
            },
        )

    def _check_sqlite_health(
        self, descriptor: HarnessDescriptor, checked_at: datetime
    ) -> HarnessHealth:
        discover_cfg = descriptor.discover
        roots = discover_cfg.roots if discover_cfg is not None else []
        if not roots:
            return HarnessHealth(
                source=descriptor.source,
                status="not_installed",
                last_check=checked_at,
                error="No database path configured",
            )

        existing_paths: list[Path] = []
        missing_paths: list[str] = []
        for root in roots:
            root_path = expand_path(root.path)
            if root_path.is_file():
                existing_paths.append(root_path)
            elif root_path.is_dir():
                for pattern in root.include or ["*.db", "*.sqlite"]:
                    for match in root_path.glob(pattern):
                        if match.is_file():
                            existing_paths.append(match)
            if not existing_paths:
                missing_paths.append(str(root_path))

        if not existing_paths:
            return HarnessHealth(
                source=descriptor.source,
                status="not_installed",
                last_check=checked_at,
                error="Database file not found",
                details={"paths": missing_paths},
            )

        latest_path = max(existing_paths, key=lambda path: (path.stat().st_mtime, str(path)))
        return HarnessHealth(
            source=descriptor.source,
            status="healthy",
            last_check=checked_at,
            files_found=len(existing_paths),
            latest_file_mtime=latest_path.stat().st_mtime,
            details={"database_path": str(latest_path)},
        )

    @staticmethod
    def _probe_file_parse(format_cluster: str, path: Path) -> None:
        if format_cluster == "jsonl":
            with path.open("r", encoding="utf-8") as handle:
                for idx, line in enumerate(handle):
                    if idx >= 5:
                        break
                    stripped = line.strip()
                    if not stripped:
                        continue
                    json.loads(stripped)
            return

        if format_cluster == "json":
            with path.open("r", encoding="utf-8") as handle:
                json.load(handle)
            return

        if format_cluster in {"multi_file", "markdown"}:
            with path.open("r", encoding="utf-8") as handle:
                for idx, _ in enumerate(handle):
                    if idx >= 4:
                        break
            return


__all__ = ["HarnessHealth", "HarnessRegistry"]
