"""Self-observation gate status.

Rollout traces in ``syke.db`` are the canonical self-observation substrate.
This module now only exposes the runtime gate that enables or disables
self-observation capture.
"""

from __future__ import annotations

import os


def _self_observation_disabled() -> bool:
    value = os.environ.get("SYKE_DISABLE_SELF_OBSERVATION", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def self_observation_status() -> dict[str, object]:
    disabled = _self_observation_disabled()
    return {
        "ok": not disabled,
        "enabled": not disabled,
        "disabled_by_env": disabled,
        "env_var": "SYKE_DISABLE_SELF_OBSERVATION",
        "detail": (
            "Self-observation trace capture disabled by SYKE_DISABLE_SELF_OBSERVATION"
            if disabled
            else "Self-observation trace capture enabled"
        ),
    }


__all__ = ["self_observation_status"]
