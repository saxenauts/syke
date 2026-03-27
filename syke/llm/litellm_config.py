"""Removed LiteLLM translation config surface.

Syke is Pi-native and no longer generates LiteLLM proxy configs.
"""

from __future__ import annotations

from pathlib import Path

_REMOVAL_MESSAGE = (
    "LiteLLM translation was removed. Syke now routes providers directly through Pi-native "
    "runtime settings."
)


def validate_litellm_model(model_name: str) -> None:
    del model_name
    raise RuntimeError(_REMOVAL_MESSAGE)


def generate_litellm_config(
    provider_id: str,
    provider_config: dict[str, str],
    auth_token: str | None,
) -> str:
    del provider_id
    del provider_config
    del auth_token
    raise RuntimeError(_REMOVAL_MESSAGE)


def write_litellm_config(
    provider_id: str,
    provider_config: dict[str, str],
    auth_token: str | None,
    path: Path | None = None,
) -> Path:
    del provider_id
    del provider_config
    del auth_token
    del path
    raise RuntimeError(_REMOVAL_MESSAGE)
