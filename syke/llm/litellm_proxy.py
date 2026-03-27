"""Removed LiteLLM translation proxy surface."""

from __future__ import annotations

from pathlib import Path

_REMOVAL_MESSAGE = (
    "LiteLLM proxy runtime was removed. Syke now uses Pi-native providers directly."
)


def start_litellm_proxy(config_path: str | Path, port: int | None = None) -> int:
    del config_path
    del port
    raise RuntimeError(_REMOVAL_MESSAGE)


def stop_litellm_proxy() -> None:
    return None


def is_litellm_proxy_running() -> bool:
    return False
