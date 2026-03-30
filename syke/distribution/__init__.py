"""Distribution orchestration for downstream agent surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from syke.config import CLAUDE_GLOBAL_MD
from syke.distribution.context_files import (
    distribute_memex,
    ensure_claude_include,
    install_skill,
)
from syke.distribution.harness import install_all
from syke.distribution.harness.base import AdapterResult

if TYPE_CHECKING:
    from syke.db import SykeDB


@dataclass
class DistributionRefreshResult:
    memex_path: Path | None = None
    claude_include_ready: bool = False
    skill_paths: list[Path] = field(default_factory=list)
    harness_results: dict[str, AdapterResult] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def refresh_distribution(db: SykeDB, user_id: str) -> DistributionRefreshResult:
    """Refresh the downstream read surfaces agents rely on."""
    from syke.memory.memex import get_memex_for_injection

    result = DistributionRefreshResult()
    memex_content = get_memex_for_injection(db, user_id)

    try:
        result.memex_path = distribute_memex(db, user_id)
    except Exception as exc:
        result.warnings.append(f"memex export failed: {exc}")

    if result.memex_path is not None and CLAUDE_GLOBAL_MD.parent.exists():
        try:
            result.claude_include_ready = ensure_claude_include(user_id)
        except Exception as exc:
            result.warnings.append(f"Claude include failed: {exc}")

    try:
        result.skill_paths = install_skill()
    except Exception as exc:
        result.warnings.append(f"skill install failed: {exc}")

    try:
        result.harness_results = install_all(memex=memex_content)
    except Exception as exc:
        result.warnings.append(f"harness refresh failed: {exc}")

    return result


__all__ = ["DistributionRefreshResult", "refresh_distribution"]
