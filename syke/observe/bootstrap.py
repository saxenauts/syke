"""Install adapter markdown guides into the workspace."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from syke.observe.catalog import active_sources
from syke.observe.seeds import get_seed_adapter_md_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BootstrapResult:
    source: str
    status: Literal["installed", "existing", "skipped"]
    detail: str


def ensure_adapters(
    workspace_root: Path,
    *,
    selected_sources: tuple[str, ...] | None = None,
) -> list[BootstrapResult]:
    """Install adapter markdowns into workspace/adapters/."""
    adapters_dir = workspace_root / "adapters"
    adapters_dir.mkdir(parents=True, exist_ok=True)

    results: list[BootstrapResult] = []
    selected_set = set(selected_sources) if selected_sources is not None else None

    for spec in active_sources():
        if selected_set is not None and spec.source not in selected_set:
            continue
        md_src = get_seed_adapter_md_path(spec.source)
        if md_src is None:
            results.append(BootstrapResult(spec.source, "skipped", "no adapter markdown seed"))
            continue

        target_md = adapters_dir / f"{spec.source}.md"

        if target_md.exists():
            results.append(BootstrapResult(spec.source, "existing", str(target_md)))
        else:
            target_md.write_text(md_src.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("Installed adapter markdown for %s", spec.source)
            results.append(BootstrapResult(spec.source, "installed", str(target_md)))

    return results
