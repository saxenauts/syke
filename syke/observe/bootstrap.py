from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from syke.config import user_data_dir
from syke.observe.catalog import iter_discovered_files
from syke.observe.factory import connect_source, get_seed_adapter_path
from syke.observe.registry import (
    HarnessRegistry,
    get_deployed_adapter_path,
    set_dynamic_adapters_dir,
)
from syke.observe.validator import validate_adapter

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
    requested = set(sources) if sources is not None else None
    results: list[BootstrapResult] = []

    for spec in registry.active_harnesses():
        if requested is not None and spec.source not in requested:
            continue

        logger.info("Bootstrap source %s: checking source data", spec.source)
        source_files = iter_discovered_files(spec)
        if not source_files:
            results.append(BootstrapResult(spec.source, "skipped", "no source data found"))
            continue

        adapter_path = get_deployed_adapter_path(spec.source, dynamic_adapters_dir=adapters_dir)
        if adapter_path is not None:
            validation = validate_adapter(spec.source, adapter_path, source_files)
            (adapters_dir / spec.source).mkdir(parents=True, exist_ok=True)
            (adapters_dir / spec.source / "validation.json").write_text(
                validation.to_json(),
                encoding="utf-8",
            )
            if validation.ok:
                logger.info("Bootstrap source %s: using deployed adapter", spec.source)
                results.append(BootstrapResult(spec.source, "existing", validation.summary))
                continue

        seed_path = get_seed_adapter_path(spec.source)
        if seed_path is not None:
            logger.info("Bootstrap source %s: validating shipped seed", spec.source)
            validation = validate_adapter(spec.source, seed_path, source_files)
            if validation.ok:
                target_dir = adapters_dir / spec.source
                target_dir.mkdir(parents=True, exist_ok=True)
                target_adapter = target_dir / "adapter.py"
                target_adapter.write_text(seed_path.read_text(encoding="utf-8"), encoding="utf-8")
                (target_dir / "validation.json").write_text(validation.to_json(), encoding="utf-8")
                logger.info("Bootstrap source %s: deployed shipped seed", spec.source)
                results.append(BootstrapResult(spec.source, "generated", validation.summary))
                continue

        logger.info("Bootstrap source %s: running factory agent", spec.source)
        ok, message = connect_source(spec, adapters_dir=adapters_dir, llm_fn=llm_fn)
        if ok:
            results.append(BootstrapResult(spec.source, "generated", message))
        else:
            results.append(BootstrapResult(spec.source, "failed", message))

    return results
