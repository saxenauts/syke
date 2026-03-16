from __future__ import annotations

import importlib
import logging
from pathlib import Path

from syke.ingestion.observe import ObserveAdapter

logger = logging.getLogger(__name__)

_ADAPTER_REGISTRY: dict[str, type[ObserveAdapter]] = {}

_BUILTIN_ADAPTER_MODULES: dict[str, str] = {}

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

    _import_adapter_module(source)
    adapter_cls = _ADAPTER_REGISTRY.get(source)
    if adapter_cls is not None:
        return adapter_cls

    return _try_load_dynamic(source)


def _import_adapter_module(source: str) -> None:
    module_name = _BUILTIN_ADAPTER_MODULES.get(source)
    if module_name is None:
        return
    _ = importlib.import_module(module_name)


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

        from syke.sense.dynamic_adapter import DynamicAdapter

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
    import importlib.util
    import inspect

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
                    roots.append(Path(item).expanduser())
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


def _import_builtin_adapters() -> None:
    for source in _BUILTIN_ADAPTER_MODULES:
        _import_adapter_module(source)


_import_builtin_adapters()


__all__ = [
    "_ADAPTER_REGISTRY",
    "get_adapter_class",
    "list_dynamic_sources",
    "register_adapter",
    "register_adapter_class",
    "set_dynamic_adapters_dir",
]
