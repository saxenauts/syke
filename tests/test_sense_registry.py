from __future__ import annotations

import importlib
from collections.abc import Iterable
from pathlib import Path
from textwrap import dedent
from typing import Protocol, cast, override

from syke.db import SykeDB
from syke.ingestion.claude_code import ClaudeCodeAdapter
from syke.ingestion.observe import ObserveAdapter, ObservedSession
from syke.ingestion.registry import HarnessRegistry


class RegistryModule(Protocol):
    def get_adapter_class(self, source: str) -> type[ObserveAdapter] | None: ...

    def register_adapter_class(self, source: str, cls: type[ObserveAdapter]) -> None: ...


registry_module = cast(
    RegistryModule,
    cast(object, importlib.import_module("syke.sense.registry")),
)
_ADAPTER_REGISTRY = cast(
    dict[str, type[ObserveAdapter]],
    getattr(registry_module, "_ADAPTER_REGISTRY"),
)
get_adapter_class = registry_module.get_adapter_class
register_adapter_class = registry_module.register_adapter_class


def _write_descriptor(directory: Path, source: str, *, status: str = "active") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    content = f"""
    spec_version = 1
    source = {source!r}
    format_cluster = "jsonl"
    status = {status!r}

    [discover]
    roots = [{{ path = "~/.harness", include = ["*.jsonl"], priority = 1 }}]

    [session]
    scope = "file"
    id_fallback = "$file.stem"

    [turn]
    role_field = "type"
    content_parser = "extract_text_content"
    timestamp_field = "timestamp"
    """
    _ = (directory / f"{source}.toml").write_text(dedent(content).strip() + "\n", encoding="utf-8")


def test_all_existing_adapters_registered() -> None:
    assert set(_ADAPTER_REGISTRY) >= {
        "claude-code",
        "opencode",
        "codex",
        "hermes",
        "pi",
        "github",
        "gmail",
    }


def test_get_adapter_returns_correct_type(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code")

    adapter = HarnessRegistry(descriptors_dir).get_adapter("claude-code", db, user_id)

    assert isinstance(adapter, ClaudeCodeAdapter)


def test_runtime_registration(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    class RuntimeAdapter(ObserveAdapter):
        @override
        def discover(self) -> list[Path]:
            return []

        @override
        def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
            return ()

    source = "runtime-test"
    register_adapter_class(source, RuntimeAdapter)

    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, source)

    assert get_adapter_class(source) is RuntimeAdapter
    assert isinstance(
        HarnessRegistry(descriptors_dir).get_adapter(source, db, user_id), RuntimeAdapter
    )


def test_unknown_source_returns_none(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    registry = HarnessRegistry(tmp_path / "descriptors")

    assert get_adapter_class("nonexistent") is None
    assert registry.get_adapter("nonexistent", db, user_id) is None
