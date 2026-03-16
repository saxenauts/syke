from __future__ import annotations

import importlib

from syke.ingestion.observe import ObserveAdapter

_ADAPTER_REGISTRY: dict[str, type[ObserveAdapter]] = {}

_BUILTIN_ADAPTER_MODULES: dict[str, str] = {
    "claude-code": "syke.ingestion.claude_code",
    "opencode": "syke.ingestion.opencode",
    "codex": "syke.ingestion.codex",
    "hermes": "syke.ingestion.hermes",
    "pi": "syke.ingestion.pi",
    "github": "syke.ingestion.github_",
    "gmail": "syke.ingestion.gmail",
}


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
    return _ADAPTER_REGISTRY.get(source)


def _import_adapter_module(source: str) -> None:
    module_name = _BUILTIN_ADAPTER_MODULES.get(source)
    if module_name is None:
        return
    _ = importlib.import_module(module_name)


def _import_builtin_adapters() -> None:
    for source in _BUILTIN_ADAPTER_MODULES:
        _import_adapter_module(source)


_import_builtin_adapters()


__all__ = [
    "_ADAPTER_REGISTRY",
    "get_adapter_class",
    "register_adapter",
    "register_adapter_class",
]
