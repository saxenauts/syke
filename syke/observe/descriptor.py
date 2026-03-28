from __future__ import annotations

import tomllib
from pathlib import Path
from string import Formatter
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SUPPORTED_FORMAT_CLUSTERS = {
    "jsonl",
    "json",
    "sqlite",
    "multi_file",
    "markdown",
    "cloud_api",
}

ALLOWED_PARSER_NAMES = {
    "extract_text_content",
    "extract_tool_blocks",
    "read_jsonl",
    "parse_timestamp",
    "read_json",
    "extract_field",
    "normalize_role",
}


class DiscoverRoot(BaseModel):
    path: str
    include: list[str] = Field(default_factory=list)
    priority: int = 0


class DiscoverConfig(BaseModel):
    roots: list[DiscoverRoot]


class SessionStartTimeConfig(BaseModel):
    first_timestamp: str


class SessionParentConfig(BaseModel):
    field: str


class SessionConfig(BaseModel):
    scope: Literal["file", "directory", "query", "api"]
    id_field: str | None = None
    id_fallback: str | None = None
    start_time: SessionStartTimeConfig | None = None
    parent_session_id: SessionParentConfig | None = None

    @model_validator(mode="after")
    def ensure_id_source(self) -> SessionConfig:
        if not self.id_field and not self.id_fallback:
            raise ValueError("session.id_field or session.id_fallback is required")
        return self


class TurnMatchConfig(BaseModel):
    field: str
    values: list[str]


class TurnConfig(BaseModel):
    match: TurnMatchConfig | None = None
    role_field: str
    content_parser: str
    tool_parser: str | None = None
    timestamp_field: str


class MetadataFieldConfig(BaseModel):
    key: str
    path: str | None = None
    first: str | None = None
    parser: str | None = None

    @model_validator(mode="after")
    def require_path_or_first(self) -> MetadataFieldConfig:
        if not self.path and not self.first and not self.parser:
            raise ValueError("metadata field requires at least one of path, first, or parser")
        return self


class MetadataConfig(BaseModel):
    fields: list[MetadataFieldConfig] = Field(default_factory=list)


class ExternalIDConfig(BaseModel):
    template: str

    def render(self, **values: object) -> str:
        placeholders = {
            field_name for _, field_name, _, _ in Formatter().parse(self.template) if field_name
        }
        missing = sorted(name for name in placeholders if name not in values)
        if missing:
            names = ", ".join(missing)
            raise ValueError(f"Missing external_id template values: {names}")
        return self.template.format(**values)


class HarnessDescriptor(BaseModel):
    spec_version: int
    source: str
    format_cluster: str
    status: Literal["active", "stub", "planned", "research", "deprecated"] = "active"
    adapter_kind: Literal["auto", "parse_line", "observe_class"] = "auto"

    discover: DiscoverConfig | None = None
    session: SessionConfig | None = None
    turn: TurnConfig | None = None
    metadata: MetadataConfig = Field(default_factory=MetadataConfig)
    external_id: ExternalIDConfig | None = None

    @model_validator(mode="after")
    def validate_status_requirements(self) -> HarnessDescriptor:
        if self.status == "active":
            required_sections: dict[str, object | None] = {
                "discover": self.discover,
                "session": self.session,
                "turn": self.turn,
            }
            missing = [name for name, value in required_sections.items() if value is None]
            if missing:
                section_names = ", ".join(missing)
                raise ValueError(f"status=active requires sections: {section_names}")
        return self

    def expand_external_id(self, **values: object) -> str:
        if self.external_id is None:
            raise ValueError("Descriptor has no [external_id] template")
        base_values: dict[str, object] = {"source": self.source}
        base_values.update(values)
        return self.external_id.render(**base_values)

    def prefers_full_adapter(self) -> bool:
        return self.adapter_kind == "observe_class"


def load_descriptor(path: Path) -> HarnessDescriptor:
    with path.open("rb") as fp:
        raw = tomllib.load(fp)
    return HarnessDescriptor.model_validate(raw)


def load_all_descriptors(directory: Path) -> list[HarnessDescriptor]:
    if not directory.exists():
        return []

    descriptors: list[HarnessDescriptor] = []
    for path in sorted(directory.glob("*.toml")):
        descriptors.append(load_descriptor(path))
    return descriptors


def validate_descriptor(desc: HarnessDescriptor) -> list[str]:
    warnings: list[str] = []

    if desc.format_cluster not in SUPPORTED_FORMAT_CLUSTERS:
        supported = ", ".join(sorted(SUPPORTED_FORMAT_CLUSTERS))
        warnings.append(
            f"Unknown format_cluster '{desc.format_cluster}'. Supported values: {supported}"
        )

    parser_fields: list[tuple[str, str | None]] = []
    if desc.turn is not None:
        parser_fields.extend(
            [
                ("turn.content_parser", desc.turn.content_parser),
                ("turn.tool_parser", desc.turn.tool_parser),
            ]
        )
    if desc.metadata.fields:
        parser_fields.extend(
            [
                (f"metadata.fields[{idx}].parser", field.parser)
                for idx, field in enumerate(desc.metadata.fields)
            ]
        )

    for field_path, parser_name in parser_fields:
        if parser_name and parser_name not in ALLOWED_PARSER_NAMES:
            warnings.append(f"Unknown parser name in {field_path}: '{parser_name}'")

    return warnings


__all__ = [
    "ALLOWED_PARSER_NAMES",
    "SUPPORTED_FORMAT_CLUSTERS",
    "HarnessDescriptor",
    "load_descriptor",
    "load_all_descriptors",
    "validate_descriptor",
]
