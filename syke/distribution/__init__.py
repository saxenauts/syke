"""Distribution orchestration for downstream agent surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from syke.distribution.context_files import distribute_memex, install_skill

if TYPE_CHECKING:
    from syke.db import SykeDB


@dataclass
class DistributionRefreshResult:
    memex_path: Path | None = None
    claude_include_ready: bool = False
    codex_memex_ready: bool = False
    skill_paths: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def status_lines(self) -> list[tuple[str, str, str | None]]:
        lines: list[tuple[str, str, str | None]] = []
        if self.memex_path is not None:
            lines.append(("memex", "exported", str(self.memex_path)))
        else:
            lines.append(("memex", "pending", "no memex available yet"))

        if self.skill_paths:
            count = len(self.skill_paths)
            lines.append(
                (
                    "capabilities",
                    "registered",
                    f"{count} file{'s' if count != 1 else ''}",
                )
            )
        else:
            lines.append(("capabilities", "none", "no capability surfaces detected"))

        for warning in self.warnings:
            lines.append(("distribution", "warning", warning))

        return lines


def refresh_distribution(
    db: SykeDB, user_id: str, *, memex_updated: bool = True
) -> DistributionRefreshResult:
    """Refresh the downstream memex and capability surfaces agents rely on."""
    result = DistributionRefreshResult()

    if memex_updated:
        try:
            result.memex_path = distribute_memex(db, user_id)
        except Exception as exc:
            result.warnings.append(f"memex export failed: {exc}")

    try:
        result.skill_paths = install_skill(user_id)
    except Exception as exc:
        result.warnings.append(f"skill install failed: {exc}")

    return result


__all__ = ["DistributionRefreshResult", "refresh_distribution"]
