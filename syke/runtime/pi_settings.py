"""Pi workspace settings generation for Syke.

Syke keeps provider/auth state in its Pi-owned agent directory. The workspace
`.pi/settings.json` only carries runtime-local concerns such as session
storage, startup quieting, and thinking defaults.
"""

from __future__ import annotations

import json
from pathlib import Path

from syke.config import SYNC_THINKING_LEVEL
from syke.pi_state import build_pi_agent_env


def _normalize_thinking_level(level: str | None) -> str:
    if level in {"off", "minimal", "low", "medium", "high", "xhigh"}:
        return level
    return "medium"


def configure_pi_workspace(
    workspace_root: Path,
    *,
    session_dir: Path | None = None,
    provider=None,
    model_override: str | None = None,
    thinking_level: str | None = None,
) -> dict[str, str]:
    """Write project-local Pi settings and return env overrides for the Pi process."""
    _ = (provider, model_override)

    pi_dir = workspace_root / ".pi"
    pi_dir.mkdir(parents=True, exist_ok=True)

    settings: dict[str, object] = {
        "defaultThinkingLevel": _normalize_thinking_level(thinking_level or SYNC_THINKING_LEVEL),
        "quietStartup": True,
    }
    if session_dir is not None:
        settings["sessionDir"] = str(session_dir)

    settings_path = pi_dir / "settings.json"
    # Merge with existing settings to preserve provider/model overrides (e.g., from replay)
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
            existing.update(settings)
            settings = existing
        except (json.JSONDecodeError, OSError):
            pass
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return build_pi_agent_env()
