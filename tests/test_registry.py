from __future__ import annotations

import importlib
import logging
from pathlib import Path
from textwrap import dedent
from types import ModuleType
from typing import Protocol, cast

import pytest

from syke.db import SykeDB
from syke.ingestion.claude_code import ClaudeCodeAdapter


class HarnessDescriptor(Protocol):
    source: str
    format_cluster: str
    status: str


class HarnessHealthInstance(Protocol):
    source: str
    status: str
    files_found: int
    latest_file_mtime: float | None
    error: str | None
    details: dict[str, object]


class RegistryInstance(Protocol):
    def list_harnesses(self) -> list[HarnessDescriptor]: ...

    def get(self, source: str) -> HarnessDescriptor | None: ...

    def by_format_cluster(self, cluster: str) -> list[HarnessDescriptor]: ...

    def by_status(self, status: str) -> list[HarnessDescriptor]: ...

    def active_harnesses(self) -> list[HarnessDescriptor]: ...

    def get_adapter(self, source: str, db: SykeDB, user_id: str) -> object | None: ...

    def health_summary(self) -> dict[str, str]: ...

    def check_health(self, source: str) -> HarnessHealthInstance: ...

    def check_all_health(self) -> dict[str, HarnessHealthInstance]: ...


class RegistryClass(Protocol):
    _descriptor_cache: dict[Path, tuple[object, ...]]

    def __call__(self, descriptors_dir: Path | None = None) -> RegistryInstance: ...


registry_module: ModuleType = importlib.import_module("syke.ingestion.registry")
HarnessHealth = cast(type[object], registry_module.HarnessHealth)
HarnessRegistry = cast(RegistryClass, registry_module.HarnessRegistry)


def _write_toml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(dedent(content).strip() + "\n", encoding="utf-8")


def _write_descriptor(
    directory: Path,
    source: str,
    *,
    format_cluster: str = "jsonl",
    status: str = "stub",
    root_path: str = "~/.harness",
    include: list[str] | None = None,
) -> None:
    patterns = include or ["*.jsonl"]
    include_values = ", ".join(repr(pattern) for pattern in patterns)
    content = f"""
    spec_version = 1
    source = {source!r}
    format_cluster = {format_cluster!r}
    status = {status!r}
    """

    if status == "active":
        content += f"""

        [discover]
        roots = [{{ path = {root_path!r}, include = [{include_values}], priority = 1 }}]

        [session]
        scope = "file"
        id_fallback = "$file.stem"

        [turn]
        role_field = "type"
        content_parser = "extract_text_content"
        timestamp_field = "timestamp"
        """

    _write_toml(directory / f"{source}.toml", content)


def test_registry_loads_descriptors_from_directory(tmp_path: Path) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code", status="active")
    _write_descriptor(descriptors_dir, "codex", format_cluster="json", status="stub")

    registry = HarnessRegistry(descriptors_dir)

    assert [desc.source for desc in registry.list_harnesses()] == ["claude-code", "codex"]


def test_registry_get_returns_descriptor_by_source(tmp_path: Path) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code", status="active")

    registry = HarnessRegistry(descriptors_dir)
    descriptor = registry.get("claude-code")

    assert descriptor is not None
    assert descriptor.source == "claude-code"
    assert descriptor.status == "active"


def test_registry_get_returns_none_for_unknown_source(tmp_path: Path) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code", status="active")

    registry = HarnessRegistry(descriptors_dir)

    assert registry.get("cursor") is None


def test_registry_by_format_cluster_filters(tmp_path: Path) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code", format_cluster="jsonl", status="active")
    _write_descriptor(descriptors_dir, "codex", format_cluster="json", status="stub")
    _write_descriptor(descriptors_dir, "cursor", format_cluster="jsonl", status="planned")

    registry = HarnessRegistry(descriptors_dir)

    assert [desc.source for desc in registry.by_format_cluster("jsonl")] == [
        "claude-code",
        "cursor",
    ]


def test_registry_by_status_filters(tmp_path: Path) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code", status="active")
    _write_descriptor(descriptors_dir, "codex", status="stub")
    _write_descriptor(descriptors_dir, "cursor", status="planned")

    registry = HarnessRegistry(descriptors_dir)

    assert [desc.source for desc in registry.by_status("stub")] == ["codex"]


def test_registry_active_harnesses_returns_only_active(tmp_path: Path) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code", status="active")
    _write_descriptor(descriptors_dir, "codex", status="stub")
    _write_descriptor(descriptors_dir, "cursor", status="active")

    registry = HarnessRegistry(descriptors_dir)

    assert [desc.source for desc in registry.active_harnesses()] == ["claude-code", "cursor"]


def test_registry_get_adapter_returns_claude_code_adapter(
    tmp_path: Path, db: SykeDB, user_id: str
) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code", status="active")

    registry = HarnessRegistry(descriptors_dir)
    adapter = registry.get_adapter("claude-code", db, user_id)

    assert isinstance(adapter, ClaudeCodeAdapter)
    assert adapter.db is db
    assert adapter.user_id == user_id


def test_registry_get_adapter_returns_none_for_stub_harness(
    tmp_path: Path, db: SykeDB, user_id: str
) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "codex", status="stub")

    registry = HarnessRegistry(descriptors_dir)

    assert registry.get_adapter("codex", db, user_id) is None


def test_registry_get_adapter_warns_for_unimplemented_active_harness(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "cursor", status="active")

    registry = HarnessRegistry(descriptors_dir)

    with caplog.at_level(logging.WARNING):
        adapter = registry.get_adapter("cursor", db, user_id)

    assert adapter is None
    assert "active but has no adapter implementation" in caplog.text


def test_registry_health_summary_returns_all_sources(tmp_path: Path) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code", status="active")
    _write_descriptor(descriptors_dir, "codex", status="stub")
    _write_descriptor(descriptors_dir, "cursor", status="planned")

    registry = HarnessRegistry(descriptors_dir)

    assert registry.health_summary() == {
        "claude-code": "active",
        "codex": "stub",
        "cursor": "planned",
    }


def test_registry_logs_validation_warnings_without_failing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "codex", format_cluster="binary", status="stub")

    with caplog.at_level(logging.WARNING):
        registry = HarnessRegistry(descriptors_dir)

    assert [desc.source for desc in registry.list_harnesses()] == ["codex"]
    assert "Unknown format_cluster 'binary'" in caplog.text


def test_health_check_stub_harness(tmp_path: Path) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "codex", status="stub")

    registry = HarnessRegistry(descriptors_dir)
    health = registry.check_health("codex")

    assert isinstance(health, HarnessHealth)
    assert health.source == "codex"
    assert health.status == "stub"
    assert health.files_found == 0


def test_health_check_planned_harness(tmp_path: Path) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "cursor", status="planned")

    registry = HarnessRegistry(descriptors_dir)
    health = registry.check_health("cursor")

    assert health.source == "cursor"
    assert health.status == "planned"
    assert health.files_found == 0


def test_health_check_not_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code", status="active")
    monkeypatch.setenv("HOME", str(tmp_path))

    registry = HarnessRegistry(descriptors_dir)
    health = registry.check_health("claude-code")

    assert health.status == "not_installed"
    assert health.files_found == 0
    assert health.error == "No configured roots exist"


def test_health_check_no_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code", status="active")
    harness_root = tmp_path / ".harness"
    harness_root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))

    registry = HarnessRegistry(descriptors_dir)
    health = registry.check_health("claude-code")

    assert health.status == "no_data"
    assert health.files_found == 0
    assert health.details["roots"] == [str(harness_root.resolve())]


def test_health_check_healthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code", status="active")
    harness_root = tmp_path / ".harness"
    harness_root.mkdir()
    session_file = harness_root / "session.jsonl"
    _ = session_file.write_text(
        '{"type":"user","message":{"content":[{"type":"text","text":"hello"}]},"timestamp":"2026-03-14T12:00:00Z"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    registry = HarnessRegistry(descriptors_dir)
    health = registry.check_health("claude-code")

    assert health.status == "healthy"
    assert health.files_found == 1
    assert health.latest_file_mtime is not None
    assert health.latest_file_mtime == session_file.stat().st_mtime
    assert health.details["latest_file"] == str(session_file.resolve())


def test_check_all_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    descriptors_dir = tmp_path / "descriptors"
    _write_descriptor(descriptors_dir, "claude-code", status="active")
    _write_descriptor(descriptors_dir, "codex", status="stub")
    _write_descriptor(descriptors_dir, "cursor", status="planned")
    harness_root = tmp_path / ".harness"
    harness_root.mkdir()
    _ = (harness_root / "session.jsonl").write_text(
        '{"type":"user","message":{"content":[{"type":"text","text":"hello"}]},"timestamp":"2026-03-14T12:00:00Z"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    registry = HarnessRegistry(descriptors_dir)
    health_map = registry.check_all_health()

    assert set(health_map) == {"claude-code", "codex", "cursor"}
    assert health_map["claude-code"].status == "healthy"
    assert health_map["codex"].status == "stub"
    assert health_map["cursor"].status == "planned"
