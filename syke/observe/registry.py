from __future__ import annotations

import importlib.util
import inspect
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from syke.config_file import expand_path
from syke.db import SykeDB
from syke.observe.adapter import ObserveAdapter
from syke.observe.descriptor import HarnessDescriptor, load_all_descriptors, validate_descriptor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Adapter registry (module-level state)
# ---------------------------------------------------------------------------

_ADAPTER_REGISTRY: dict[str, type[ObserveAdapter]] = {}
_dynamic_adapters_dir: Path | None = None


def set_dynamic_adapters_dir(path: Path | None) -> None:
    global _dynamic_adapters_dir
    _dynamic_adapters_dir = path


def register_adapter(source: str):
    def decorator(cls: type[ObserveAdapter]) -> type[ObserveAdapter]:
        register_adapter_class(source, cls)
        return cls

    return decorator


def register_adapter_class(source: str, cls: type[ObserveAdapter]) -> None:
    _ADAPTER_REGISTRY[source] = cls
    cls.source = source


def get_adapter_class(source: str) -> type[ObserveAdapter] | None:
    adapter_cls = _ADAPTER_REGISTRY.get(source)
    if adapter_cls is not None:
        return adapter_cls
    return _try_load_dynamic(source)


def _try_load_dynamic(source: str) -> type[ObserveAdapter] | None:
    if _dynamic_adapters_dir is None:
        return None
    adapter_dir = _dynamic_adapters_dir / source
    adapter_py = adapter_dir / "adapter.py"
    if not adapter_py.is_file():
        return None

    try:
        native_cls = _try_load_native_adapter(adapter_py, source)
        if native_cls is not None:
            _ADAPTER_REGISTRY[source] = native_cls  # type: ignore[assignment]
            logger.info("Loaded native adapter for %s from %s", source, adapter_dir)
            return native_cls  # type: ignore[return-value]

        from syke.observe.dynamic_adapter import DynamicAdapter

        descriptor_toml = adapter_dir / "descriptor.toml"
        discover_roots: list[Path] = []
        file_glob = "**/*.jsonl"
        if descriptor_toml.is_file():
            discover_roots, file_glob = _parse_descriptor_paths(descriptor_toml)

        def _factory(
            db, user_id, _src=source, _dir=adapter_dir, _roots=discover_roots, _glob=file_glob
        ):
            return DynamicAdapter(
                db=db,
                user_id=user_id,
                source_name=_src,
                adapter_dir=_dir,
                discover_roots=_roots,
                file_glob=_glob,
            )

        _factory.source = source  # type: ignore[attr-defined]
        _ADAPTER_REGISTRY[source] = _factory  # type: ignore[assignment]
        logger.info("Loaded dynamic adapter for %s from %s", source, adapter_dir)
        return _factory  # type: ignore[return-value]
    except Exception:
        logger.warning("Failed to load dynamic adapter for %s", source, exc_info=True)
        return None


def _try_load_native_adapter(adapter_py: Path, source: str) -> type | None:
    """Check if adapter.py defines an ObserveAdapter subclass (e.g. SQLite adapters)."""
    spec = importlib.util.spec_from_file_location(f"syke_adapter_{source}", adapter_py)
    if spec is None or spec.loader is None:
        return None

    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None

    for _name, obj in inspect.getmembers(mod, inspect.isclass):
        if issubclass(obj, ObserveAdapter) and obj is not ObserveAdapter:
            return obj

    return None


def _parse_descriptor_paths(toml_path: Path) -> tuple[list[Path], str]:
    import tomllib

    roots: list[Path] = []
    file_glob = "**/*.jsonl"
    try:
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
        discover = data.get("discover", {})
        raw_roots = discover.get("roots", [])
        if isinstance(raw_roots, list):
            for item in raw_roots:
                if isinstance(item, str):
                    roots.append(expand_path(item))
        raw_glob = discover.get("glob")
        if isinstance(raw_glob, str):
            file_glob = raw_glob
    except Exception:
        pass
    return roots, file_glob


def list_dynamic_sources() -> list[str]:
    if _dynamic_adapters_dir is None or not _dynamic_adapters_dir.is_dir():
        return []
    return sorted(
        d.name
        for d in _dynamic_adapters_dir.iterdir()
        if d.is_dir() and (d / "adapter.py").is_file()
    )


# ---------------------------------------------------------------------------
# Harness health
# ---------------------------------------------------------------------------

@dataclass
class HarnessHealth:
    source: str
    status: str
    last_check: datetime
    files_found: int = 0
    latest_file_mtime: float | None = None
    error: str | None = None
    details: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Unified registry
# ---------------------------------------------------------------------------

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

    def _check_file_health(self, descriptor, checked_at: datetime) -> HarnessHealth:
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

    def _check_sqlite_health(self, descriptor, checked_at: datetime) -> HarnessHealth:
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


__all__ = [
    "HarnessHealth",
    "HarnessRegistry",
    "get_adapter_class",
    "list_dynamic_sources",
    "register_adapter",
    "register_adapter_class",
    "set_dynamic_adapters_dir",
]
