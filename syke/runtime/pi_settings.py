"""Pi workspace settings generation for Syke.

Syke keeps provider/auth state in its Pi-owned agent directory. The workspace
`.pi/settings.json` only carries runtime-local concerns such as session
storage, startup quieting, and thinking defaults.
"""

from __future__ import annotations

import json
from pathlib import Path

from syke.config import SYNC_THINKING
from syke.pi_state import build_pi_agent_env


def _thinking_level_from_budget(thinking_budget: int) -> str:
    if thinking_budget <= 0:
        return "off"
    if thinking_budget <= 1024:
        return "minimal"
    if thinking_budget <= 4096:
        return "low"
    if thinking_budget <= 12000:
        return "medium"
    if thinking_budget <= 32000:
        return "high"
    return "xhigh"


def configure_pi_workspace(
    workspace_root: Path,
    *,
    session_dir: Path | None = None,
    provider=None,
    model_override: str | None = None,
    thinking_budget: int | None = None,
) -> dict[str, str]:
    """Write project-local Pi settings and return env overrides for the Pi process."""
    _ = (provider, model_override)

    pi_dir = workspace_root / ".pi"
    pi_dir.mkdir(parents=True, exist_ok=True)

    settings: dict[str, object] = {
        "defaultThinkingLevel": _thinking_level_from_budget(thinking_budget or SYNC_THINKING),
        "quietStartup": True,
    }
    if session_dir is not None:
        settings["sessionDir"] = str(session_dir)

    settings_path = pi_dir / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return build_pi_agent_env()
