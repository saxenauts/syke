"""Removed Codex translation proxy surface."""

from __future__ import annotations

from typing import Any

from syke.config_file import expand_path

_DEFAULT_MODEL = "gpt-5.3-codex"
_REMOVAL_MESSAGE = (
    "Codex translation proxy was removed. Syke now uses Pi-native Codex provider routing."
)


def _read_codex_model() -> str:
    """Read model from ~/.codex/config.toml, fall back to default."""
    try:
        path = expand_path("~/.codex") / "config.toml"
        if not path.exists():
            return _DEFAULT_MODEL
        import tomllib

        with open(path, "rb") as f:
            cfg = tomllib.load(f)
        model = str(cfg.get("model", "")).strip()
        if not model:
            return _DEFAULT_MODEL
        return model if "-codex" in model else f"{model}-codex"
    except Exception:
        return _DEFAULT_MODEL


def translate_request(body: dict[str, Any]) -> dict[str, Any]:
    del body
    raise RuntimeError(_REMOVAL_MESSAGE)


class AnthropicSSEBuilder:
    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        del model
        raise RuntimeError(_REMOVAL_MESSAGE)


def translate_sse_event(event_type: str, data: dict[str, Any], builder: AnthropicSSEBuilder) -> str:
    del event_type
    del data
    del builder
    raise RuntimeError(_REMOVAL_MESSAGE)


def start_codex_proxy(access_token: str, account_id: str = "") -> int:
    del access_token
    del account_id
    raise RuntimeError(_REMOVAL_MESSAGE)


def stop_codex_proxy() -> None:
    return None


def get_codex_proxy_port() -> int | None:
    return None
