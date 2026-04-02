"""Observe Factory v2 — one skill-driven adapter factory."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from string import Template

from syke.observe.catalog import (
    DiscoverConfig,
    DiscoverRoot,
    SourceSpec,
    active_sources,
    discovered_roots,
    get_source,
    iter_discovered_files,
)
from syke.observe.seeds import get_seed_adapter_path
from syke.observe.validator import ValidationResult, validate_adapter
from syke.runtime.sandbox import write_sandbox_config
from syke.runtime.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

_SKILL_PATH = Path(__file__).parent / "skills" / "factory.md"


def discover(home: Path | None = None) -> list[dict]:
    """Scan filesystem for cataloged harness installations."""
    results: list[dict] = []
    for spec in active_sources():
        roots = discovered_roots(spec, home=home)
        if not roots:
            continue
        files = iter_discovered_files(spec, home=home)
        results.append(
            {
                "source": spec.source,
                "path": roots[0],
                "format": spec.format_cluster,
                "roots": roots,
                "files_found": len(files),
            }
        )
    return results


def connect_source(
    spec: SourceSpec,
    *,
    adapters_dir: Path,
    llm_fn=None,
) -> tuple[bool, str]:
    roots = discovered_roots(spec)
    if not roots:
        return False, "No source data found"

    if llm_fn is None:
        from syke.llm.simple import build_llm_fn

        llm_fn = build_llm_fn(timeout_seconds=600.0, extra_read_roots=roots)

    output_path = _factory_output_path(spec.source)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    prompt = _build_factory_prompt(spec, roots=roots, output_path=output_path)
    logger.info("Factory source %s: running factory skill", spec.source)
    try:
        llm_result = llm_fn(prompt)
        logger.info("Factory source %s: skill completed", spec.source)
    finally:
        write_sandbox_config(WORKSPACE_ROOT)

    if not output_path.is_file():
        summary = llm_result.strip() if isinstance(llm_result, str) else ""
        if summary:
            return False, f"Factory agent did not produce adapter.py ({summary[:200]})"
        return False, "Factory agent did not produce adapter.py"

    source_paths = iter_discovered_files(spec)
    result = validate_adapter(spec.source, output_path, source_paths)
    if not result.ok:
        logger.warning("Factory source %s: validation failed: %s", spec.source, result.summary)
        return False, result.summary

    _deploy_adapter(spec.source, output_path, result, adapters_dir)
    return True, result.summary


def connect(
    path: Path | str,
    llm_fn=None,
    adapters_dir: Path | None = None,
    full_class: bool = False,
    source_name_override: str | None = None,
) -> tuple[bool, str]:
    """Generate or repair an Observe adapter for a local harness path."""
    _ = full_class
    source_name = source_name_override
    if source_name is None:
        source_name = Path(path).name.lstrip(".") or "unknown"

    spec = get_source(source_name)
    if spec is None:
        spec = SourceSpec(
            source=source_name,
            format_cluster="mixed",
            discover=DiscoverConfig(roots=[DiscoverRoot(path=str(Path(path).expanduser()))]),
            artifact_hints=("mixed",),
        )
    if adapters_dir is None:
        adapters_dir = Path(path).expanduser().resolve().parent / ".syke-adapters"
    adapters_dir.mkdir(parents=True, exist_ok=True)
    return connect_source(spec, adapters_dir=adapters_dir, llm_fn=llm_fn)


def load_validation_result(source: str, *, adapters_dir: Path) -> ValidationResult | None:
    validation_path = adapters_dir / source / "validation.json"
    if not validation_path.is_file():
        return None
    try:
        raw = json.loads(validation_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    try:
        return ValidationResult(**raw)
    except TypeError:
        return None
def _build_factory_prompt(spec: SourceSpec, *, roots: list[Path], output_path: Path) -> str:
    skill = _SKILL_PATH.read_text(encoding="utf-8")
    roots_block = "\n".join(f"- {root}" for root in roots)
    body = Template(skill).safe_substitute(
        source_name=spec.source,
        source_roots=roots_block,
        output_path=str(output_path),
    )
    return (
        f"source_name: {spec.source}\n"
        f"source_roots:\n{roots_block}\n"
        f"output_path: {output_path}\n\n"
        f"{body}"
    )


def _factory_output_path(source: str) -> Path:
    from syke.runtime.workspace import WORKSPACE_ROOT

    return WORKSPACE_ROOT / "scratch" / "observe_factory" / source / "adapter.py"


def _deploy_adapter(
    source: str,
    output_path: Path,
    validation: ValidationResult,
    adapters_dir: Path,
) -> None:
    target_dir = adapters_dir / source
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(output_path, target_dir / "adapter.py")
    (target_dir / "validation.json").write_text(validation.to_json(), encoding="utf-8")


__all__ = [
    "connect",
    "connect_source",
    "discover",
    "get_seed_adapter_path",
    "load_validation_result",
]
