from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Protocol, cast

import pytest
from pydantic import ValidationError


class DiscoverRootLike(Protocol):
    priority: int
    path: str


class DiscoverConfigLike(Protocol):
    roots: list[DiscoverRootLike]


class TurnConfigLike(Protocol):
    content_parser: str


class DescriptorLike(Protocol):
    source: str
    format_cluster: str
    status: str
    discover: DiscoverConfigLike | None
    session: object | None
    turn: TurnConfigLike | None

    def expand_external_id(self, **values: object) -> str: ...


descriptor_module = importlib.import_module("syke.observe.descriptor")
HarnessDescriptor = cast(Callable[..., DescriptorLike], descriptor_module.HarnessDescriptor)
load_all_descriptors = cast(
    Callable[[Path], list[DescriptorLike]], descriptor_module.load_all_descriptors
)
load_descriptor = cast(Callable[[Path], DescriptorLike], descriptor_module.load_descriptor)
validate_descriptor = cast(
    Callable[[DescriptorLike], list[str]], descriptor_module.validate_descriptor
)


def _write_toml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(dedent(content).strip() + "\n", encoding="utf-8")


def test_load_descriptor_valid_descriptor(tmp_path: Path):
    descriptor_path = tmp_path / "ok.toml"
    _write_toml(
        descriptor_path,
        """
        spec_version = 1
        source = "claude-code"
        format_cluster = "jsonl"
        status = "active"

        [discover]
        roots = [
          { path = "~/.claude/projects", include = ["**/*.jsonl"], priority = 20 },
          { path = "~/.claude/transcripts", include = ["*.jsonl"], priority = 10 },
        ]

        [session]
        scope = "file"
        id_field = "sessionId"
        start_time = { first_timestamp = "timestamp" }

        [turn]
        match = { field = "type", values = ["user", "assistant"] }
        role_field = "type"
        content_parser = "extract_text_content"
        tool_parser = "extract_tool_blocks"
        timestamp_field = "timestamp"

        [metadata]
        fields = [{ key = "model", path = "message.model" }]

        [external_id]
        template = "{source}:{session_id}:turn:{sequence_index}"
        """,
    )

    descriptor = load_descriptor(descriptor_path)

    assert descriptor.source == "claude-code"
    assert descriptor.status == "active"
    assert descriptor.discover is not None
    assert len(descriptor.discover.roots) == 2
    assert descriptor.turn is not None
    assert descriptor.turn.content_parser == "extract_text_content"


def test_load_descriptor_missing_required_fields_raises(tmp_path: Path):
    descriptor_path = tmp_path / "invalid.toml"
    _write_toml(
        descriptor_path,
        """
        spec_version = 1
        format_cluster = "jsonl"
        status = "active"
        """,
    )

    with pytest.raises(ValidationError):
        _ = load_descriptor(descriptor_path)


def test_validate_descriptor_unknown_format_cluster_warns():
    desc = HarnessDescriptor(spec_version=1, source="x", format_cluster="binary", status="stub")

    warnings = validate_descriptor(desc)

    assert any("Unknown format_cluster 'binary'" in warning for warning in warnings)


def test_validate_descriptor_unknown_parser_name_warns(tmp_path: Path):
    descriptor_path = tmp_path / "unknown-parser.toml"
    _write_toml(
        descriptor_path,
        """
        spec_version = 1
        source = "claude-code"
        format_cluster = "jsonl"
        status = "active"

        [discover]
        roots = [{ path = "~/.claude/projects", include = ["*.jsonl"], priority = 1 }]

        [session]
        scope = "file"
        id_fallback = "$file.stem"

        [turn]
        role_field = "type"
        content_parser = "not_a_parser"
        timestamp_field = "timestamp"
        """,
    )

    descriptor = load_descriptor(descriptor_path)
    warnings = validate_descriptor(descriptor)

    assert any("Unknown parser name in turn.content_parser" in warning for warning in warnings)


def test_stub_descriptor_loads_with_minimal_fields(tmp_path: Path):
    descriptor_path = tmp_path / "stub.toml"
    _write_toml(
        descriptor_path,
        """
        spec_version = 1
        source = "future-harness"
        format_cluster = "cloud_api"
        status = "stub"
        """,
    )

    descriptor = load_descriptor(descriptor_path)

    assert descriptor.status == "stub"
    assert descriptor.discover is None
    assert descriptor.session is None
    assert descriptor.turn is None


def test_external_id_template_expansion_works():
    desc = HarnessDescriptor(
        spec_version=1,
        source="claude-code",
        format_cluster="jsonl",
        status="stub",
        external_id={"template": "{source}:{session_id}:turn:{sequence_index}"},
    )

    external_id = desc.expand_external_id(session_id="abc123", sequence_index=7)

    assert external_id == "claude-code:abc123:turn:7"


def test_external_id_template_missing_value_raises():
    desc = HarnessDescriptor(
        spec_version=1,
        source="claude-code",
        format_cluster="jsonl",
        status="stub",
        external_id={"template": "{source}:{session_id}:turn:{sequence_index}"},
    )

    with pytest.raises(ValueError, match="Missing external_id template values"):
        _ = desc.expand_external_id(session_id="abc123")


def test_discover_multiple_roots_round_trip(tmp_path: Path):
    descriptor_path = tmp_path / "roots.toml"
    _write_toml(
        descriptor_path,
        """
        spec_version = 1
        source = "claude-code"
        format_cluster = "jsonl"
        status = "active"

        [discover]
        roots = [
          { path = "~/.claude/projects", include = ["**/*.jsonl"], priority = 20 },
          { path = "~/.claude/transcripts", include = ["*.jsonl"], priority = 10 },
        ]

        [session]
        scope = "file"
        id_fallback = "$file.stem"

        [turn]
        role_field = "type"
        content_parser = "extract_text_content"
        timestamp_field = "timestamp"
        """,
    )

    descriptor = load_descriptor(descriptor_path)

    assert descriptor.discover is not None
    assert [root.priority for root in descriptor.discover.roots] == [20, 10]
    assert [root.path for root in descriptor.discover.roots] == [
        "~/.claude/projects",
        "~/.claude/transcripts",
    ]


def test_load_all_descriptors_from_directory(tmp_path: Path):
    descriptors_dir = tmp_path / "descriptors"
    _write_toml(
        descriptors_dir / "a.toml",
        """
        spec_version = 1
        source = "a"
        format_cluster = "jsonl"
        status = "stub"
        """,
    )
    _write_toml(
        descriptors_dir / "b.toml",
        """
        spec_version = 1
        source = "b"
        format_cluster = "json"
        status = "stub"
        """,
    )
    _ = (descriptors_dir / "ignored.txt").write_text("hello", encoding="utf-8")

    descriptors = load_all_descriptors(descriptors_dir)

    assert [desc.source for desc in descriptors] == ["a", "b"]


def test_active_descriptor_requires_discover_session_turn(tmp_path: Path):
    descriptor_path = tmp_path / "active-missing.toml"
    _write_toml(
        descriptor_path,
        """
        spec_version = 1
        source = "claude-code"
        format_cluster = "jsonl"
        status = "active"
        """,
    )

    with pytest.raises(ValidationError, match="status=active requires sections"):
        _ = load_descriptor(descriptor_path)


def test_metadata_field_requires_path_or_first_or_parser(tmp_path: Path):
    descriptor_path = tmp_path / "metadata-invalid.toml"
    _write_toml(
        descriptor_path,
        """
        spec_version = 1
        source = "claude-code"
        format_cluster = "jsonl"
        status = "active"

        [discover]
        roots = [{ path = "~/.claude/projects", include = ["*.jsonl"], priority = 1 }]

        [session]
        scope = "file"
        id_fallback = "$file.stem"

        [turn]
        role_field = "type"
        content_parser = "extract_text_content"
        timestamp_field = "timestamp"

        [metadata]
        fields = [{ key = "model" }]
        """,
    )

    with pytest.raises(ValidationError, match="metadata field requires"):
        _ = load_descriptor(descriptor_path)


def test_load_all_repo_descriptors():
    desc_dir = Path(__file__).parent.parent / "syke" / "ingestion" / "descriptors"
    descriptors = load_all_descriptors(desc_dir)
    for d in descriptors:
        assert d.status in ("active", "stub", "planned", "research", "deprecated")

    warnings_by_source = {
        descriptor.source: validate_descriptor(descriptor) for descriptor in descriptors
    }
    assert warnings_by_source == {descriptor.source: [] for descriptor in descriptors}
