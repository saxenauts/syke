from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DiscoverRoot:
    path: str
    include: list[str] = field(default_factory=list)
    priority: int = 0


@dataclass(frozen=True)
class DiscoverConfig:
    roots: list[DiscoverRoot]


@dataclass(frozen=True)
class SourceSpec:
    source: str
    format_cluster: str
    discover: DiscoverConfig
    artifact_hints: tuple[str, ...] = ()
    status: str = "active"


_CATALOG: tuple[SourceSpec, ...] = (
    SourceSpec(
        source="claude-code",
        format_cluster="jsonl",
        artifact_hints=("jsonl", "transcript"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(path="~/.claude/projects", include=["**/*.jsonl"], priority=20),
                DiscoverRoot(path="~/.claude/transcripts", include=["*.jsonl"], priority=10),
            ]
        ),
    ),
    SourceSpec(
        source="codex",
        format_cluster="mixed",
        artifact_hints=("sqlite", "jsonl", "history", "index", "archive"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.codex",
                    include=["**/*.jsonl", "**/*.db", "**/*.sqlite", "config.toml"],
                    priority=20,
                ),
            ]
        ),
    ),
    SourceSpec(
        source="opencode",
        format_cluster="sqlite",
        artifact_hints=("sqlite",),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.local/share/opencode",
                    include=["*.db", "*.sqlite"],
                    priority=20,
                )
            ]
        ),
    ),
    SourceSpec(
        source="cursor",
        format_cluster="mixed",
        artifact_hints=("json", "jsonl", "sqlite", "logs"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.cursor",
                    include=["**/*.jsonl", "**/*.json", "**/*.db", "**/*.sqlite", "**/*.log"],
                    priority=20,
                )
            ]
        ),
    ),
    SourceSpec(
        source="copilot",
        format_cluster="mixed",
        artifact_hints=("json", "jsonl", "sqlite", "logs"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.config/github-copilot",
                    include=["**/*.jsonl", "**/*.json", "**/*.db", "**/*.sqlite", "**/*.log"],
                    priority=20,
                )
            ]
        ),
    ),
    SourceSpec(
        source="antigravity",
        format_cluster="mixed",
        artifact_hints=("json", "sqlite", "logs"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.gemini/antigravity",
                    include=["**/*.json", "**/*.db", "**/*.sqlite", "**/*.log"],
                    priority=20,
                ),
                DiscoverRoot(
                    path="~/.antigravity",
                    include=["**/*.json", "**/*.db", "**/*.sqlite", "**/*.log"],
                    priority=10,
                ),
            ]
        ),
    ),
    SourceSpec(
        source="hermes",
        format_cluster="mixed",
        artifact_hints=("sqlite", "json"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.hermes",
                    include=["state.db", "sessions/**/*.json", "*.json", "*.db"],
                    priority=20,
                )
            ]
        ),
    ),
    SourceSpec(
        source="gemini-cli",
        format_cluster="mixed",
        artifact_hints=("json", "sqlite", "logs"),
        discover=DiscoverConfig(
            roots=[
                DiscoverRoot(
                    path="~/.gemini",
                    include=["**/*.jsonl", "**/*.json", "**/*.db", "**/*.sqlite", "**/*.log"],
                    priority=20,
                )
            ]
        ),
    ),
)


def active_sources() -> tuple[SourceSpec, ...]:
    return _CATALOG


def get_source(source: str) -> SourceSpec | None:
    for spec in _CATALOG:
        if spec.source == source:
            return spec
    return None


def _resolve_root_path(raw_path: str, *, home: Path | None = None) -> Path:
    if home is not None and raw_path.startswith("~/"):
        return home / raw_path[2:]
    return Path(raw_path).expanduser()


def iter_discovered_files(spec: SourceSpec, *, home: Path | None = None) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for root in spec.discover.roots:
        root_path = _resolve_root_path(root.path, home=home)
        if root_path.is_file():
            try:
                resolved = root_path.resolve()
            except OSError:
                continue
            if resolved not in seen:
                seen.add(resolved)
                files.append(resolved)
            continue
        if not root_path.exists() or not root_path.is_dir():
            continue
        for pattern in root.include or ["**/*"]:
            for match in root_path.glob(pattern):
                if not match.is_file():
                    continue
                try:
                    resolved = match.resolve()
                except OSError:
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                files.append(resolved)
    return sorted(files)


def discovered_roots(spec: SourceSpec, *, home: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    for root in spec.discover.roots:
        root_path = _resolve_root_path(root.path, home=home)
        if root_path.exists():
            try:
                roots.append(root_path.resolve())
            except OSError:
                continue
    return roots


def primary_root(spec: SourceSpec, *, home: Path | None = None) -> Path | None:
    roots = discovered_roots(spec, home=home)
    if not roots:
        return None
    return roots[0]
