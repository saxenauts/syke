from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from textwrap import dedent
from typing import override

from syke.db import SykeDB
from syke.observe.observe import ObserveAdapter, ObservedSession
from syke.observe.registry import (
    HarnessRegistry,
    _ADAPTER_REGISTRY,
    get_adapter_class,
    register_adapter_class,
)


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


def test_dynamic_adapter_loaded_from_disk(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    from syke.observe.registry import set_dynamic_adapters_dir

    adapters_dir = tmp_path / "adapters" / "test-dyn"
    adapters_dir.mkdir(parents=True)
    (adapters_dir / "adapter.py").write_text(
        "import json\ndef parse_line(line):\n    return json.loads(line)\n"
    )
    set_dynamic_adapters_dir(tmp_path / "adapters")
    cls = get_adapter_class("test-dyn")
    assert cls is not None
    set_dynamic_adapters_dir(None)


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
