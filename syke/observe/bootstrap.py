from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from syke.config import user_data_dir
from syke.config_file import expand_path
from syke.observe.descriptor import HarnessDescriptor
from syke.observe.factory import connect as factory_connect
from syke.observe.registry import (
    _ADAPTER_REGISTRY,
    HarnessRegistry,
    get_adapter_class,
    set_dynamic_adapters_dir,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapResult:
    source: str
    status: Literal["existing", "generated", "skipped", "failed"]
    detail: str


def ensure_adapters(
    user_id: str,
    *,
    sources: list[str] | None = None,
    llm_fn=None,
    registry: HarnessRegistry | None = None,
) -> list[BootstrapResult]:
    adapters_dir = user_data_dir(user_id) / "adapters"
    adapters_dir.mkdir(parents=True, exist_ok=True)
    set_dynamic_adapters_dir(adapters_dir)

    registry = registry or HarnessRegistry(dynamic_adapters_dir=adapters_dir)
    if registry.dynamic_adapters_dir is None:
        registry = HarnessRegistry(
            registry.descriptors_dir,
            dynamic_adapters_dir=adapters_dir,
        )
    requested = set(sources) if sources is not None else None

    cached_llm_fn = llm_fn
    llm_init_attempted = llm_fn is not None
    results: list[BootstrapResult] = []

    for descriptor in registry.active_harnesses():
        if requested is not None and descriptor.source not in requested:
            continue

        adapter_dir = adapters_dir / descriptor.source
        if (adapter_dir / "adapter.py").is_file() and not (
            adapter_dir / "descriptor.toml"
        ).is_file():
            _write_descriptor_artifact(descriptor, adapters_dir, registry.descriptors_dir)
            _ADAPTER_REGISTRY.pop(descriptor.source, None)

        if (
            get_adapter_class(
                descriptor.source,
                dynamic_adapters_dir=registry.dynamic_adapters_dir,
            )
            is not None
        ):
            results.append(
                BootstrapResult(descriptor.source, "existing", "adapter already present")
            )
            continue

        candidate = _select_bootstrap_path(descriptor)
        if candidate is None:
            results.append(BootstrapResult(descriptor.source, "skipped", "no source data found"))
            continue

        if cached_llm_fn is None and not llm_init_attempted:
            llm_init_attempted = True
            try:
                from syke.llm.simple import build_llm_fn

                cached_llm_fn = build_llm_fn()
            except Exception as exc:
                logger.info("Observe bootstrap LLM unavailable: %s", exc)

        ok, message = factory_connect(
            candidate,
            llm_fn=cached_llm_fn,
            adapters_dir=adapters_dir,
            full_class=descriptor.prefers_full_adapter(),
            source_name_override=descriptor.source,
        )
        if not ok and descriptor.prefers_full_adapter() and cached_llm_fn is None:
            ok, fallback_message = factory_connect(
                candidate,
                llm_fn=None,
                adapters_dir=adapters_dir,
                full_class=False,
                source_name_override=descriptor.source,
            )
            if ok:
                message = f"{fallback_message} (fallback adapter)"

        if not ok:
            results.append(BootstrapResult(descriptor.source, "failed", message))
            continue

        _write_descriptor_artifact(descriptor, adapters_dir, registry.descriptors_dir)
        _ADAPTER_REGISTRY.pop(descriptor.source, None)
        results.append(BootstrapResult(descriptor.source, "generated", message))

    return results


def _select_bootstrap_path(descriptor: HarnessDescriptor) -> Path | None:
    discover = descriptor.discover
    if discover is None:
        return None

    best: tuple[float, Path] | None = None
    for root in discover.roots:
        root_path = expand_path(root.path)
        matches = _matching_files(root_path, root.include)
        if not matches:
            continue
        latest_mtime = max(match.stat().st_mtime for match in matches)
        candidate = root_path
        if best is None or latest_mtime > best[0]:
            best = (latest_mtime, candidate)

    return best[1] if best is not None else None


def _matching_files(root_path: Path, patterns: list[str]) -> list[Path]:
    if root_path.is_file():
        return [root_path]
    if not root_path.exists() or not root_path.is_dir():
        return []

    matches: list[Path] = []
    search_patterns = patterns or ["*"]
    for pattern in search_patterns:
        for match in root_path.glob(pattern):
            if match.is_file():
                matches.append(match)
    return sorted(set(matches))


def _write_descriptor_artifact(
    descriptor: HarnessDescriptor,
    adapters_dir: Path,
    descriptors_dir: Path,
) -> None:
    target_dir = adapters_dir / descriptor.source
    target_dir.mkdir(parents=True, exist_ok=True)

    source_descriptor = descriptors_dir / f"{descriptor.source}.toml"
    target_descriptor = target_dir / "descriptor.toml"
    if source_descriptor.is_file():
        shutil.copyfile(source_descriptor, target_descriptor)
        return

    target_descriptor.write_text(_render_discover_descriptor(descriptor), encoding="utf-8")


def _render_discover_descriptor(descriptor: HarnessDescriptor) -> str:
    lines = ["[discover]"]
    for root in descriptor.discover.roots if descriptor.discover is not None else []:
        lines.append("[[discover.roots]]")
        lines.append(f'path = "{root.path}"')
        include_items = ", ".join(f'"{item}"' for item in root.include)
        lines.append(f"include = [{include_items}]")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
