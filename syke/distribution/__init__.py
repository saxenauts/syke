"""Distribution orchestration for downstream agent surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from syke.config import CLAUDE_GLOBAL_MD, CODEX_GLOBAL_AGENTS
from syke.distribution.context_files import (
    distribute_memex,
    ensure_claude_include,
    ensure_codex_memex_reference,
    install_skill,
)

if TYPE_CHECKING:
    from syke.db import SykeDB


@dataclass
class DistributionRefreshResult:
    memex_path: Path | None = None
    claude_include_ready: bool = False
    codex_memex_ready: bool = False
    skill_paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def refresh_distribution(db: SykeDB, user_id: str) -> DistributionRefreshResult:
    """Refresh the downstream read surfaces agents rely on."""
    result = DistributionRefreshResult()

    try:
        result.memex_path = distribute_memex(db, user_id)
    except Exception as exc:
        result.warnings.append(f"memex export failed: {exc}")

    if result.memex_path is not None and CLAUDE_GLOBAL_MD.parent.exists():
        try:
            result.claude_include_ready = ensure_claude_include(user_id)
        except Exception as exc:
            result.warnings.append(f"Claude include failed: {exc}")

    if result.memex_path is not None and CODEX_GLOBAL_AGENTS.parent.exists():
        try:
            result.codex_memex_ready = ensure_codex_memex_reference(user_id)
        except Exception as exc:
            result.warnings.append(f"Codex memex reference failed: {exc}")

    try:
        result.skill_paths = install_skill()
    except Exception as exc:
        result.warnings.append(f"skill install failed: {exc}")

    return result


__all__ = ["DistributionRefreshResult", "refresh_distribution"]
