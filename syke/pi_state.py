"""Syke-owned Pi state helpers.

Syke keeps Pi credentials and provider/model defaults under a dedicated
state root so it does not depend on the user's global ~/.pi/agent.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from syke.config import SYKE_HOME

_PI_AGENT_DIR_ENV = "SYKE_PI_AGENT_DIR"


def get_pi_agent_dir() -> Path:
    root = os.getenv(_PI_AGENT_DIR_ENV)
    if root:
        return Path(root).expanduser().resolve()
    return (SYKE_HOME / "pi-agent").resolve()


def get_pi_auth_path() -> Path:
    return get_pi_agent_dir() / "auth.json"


def get_pi_settings_path() -> Path:
    return get_pi_agent_dir() / "settings.json"


def get_pi_models_path() -> Path:
    return get_pi_agent_dir() / "models.json"


def ensure_pi_agent_dir() -> Path:
    root = get_pi_agent_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_pi_agent_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {"PI_CODING_AGENT_DIR": str(ensure_pi_agent_dir())}
    if extra:
        env.update(extra)
    return env


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(default)
    return raw if isinstance(raw, dict) else dict(default)


def _write_json(path: Path, data: dict[str, Any], *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_pi_auth() -> dict[str, Any]:
    return _load_json(get_pi_auth_path(), {})


def save_pi_auth(data: dict[str, Any]) -> None:
    _write_json(
        get_pi_auth_path(),
        data,
        mode=stat.S_IRUSR | stat.S_IWUSR,
    )


def get_credential(provider_id: str) -> dict[str, Any] | None:
    auth = load_pi_auth()
    credential = auth.get(provider_id)
    return credential if isinstance(credential, dict) else None


def has_credential(provider_id: str) -> bool:
    return get_credential(provider_id) is not None


def list_credential_providers() -> list[str]:
    return sorted(load_pi_auth())


def set_api_key(provider_id: str, key: str) -> None:
    auth = load_pi_auth()
    auth[provider_id] = {"type": "api_key", "key": key}
    save_pi_auth(auth)


def remove_credential(provider_id: str) -> bool:
    auth = load_pi_auth()
    if provider_id not in auth:
        return False
    del auth[provider_id]
    save_pi_auth(auth)
    return True


def load_pi_settings() -> dict[str, Any]:
    return _load_json(get_pi_settings_path(), {})


def save_pi_settings(data: dict[str, Any]) -> None:
    _write_json(get_pi_settings_path(), data)


def get_default_provider() -> str | None:
    value = load_pi_settings().get("defaultProvider")
    return value if isinstance(value, str) and value else None


def set_default_provider(provider_id: str | None) -> None:
    settings = load_pi_settings()
    if provider_id:
        settings["defaultProvider"] = provider_id
    else:
        settings.pop("defaultProvider", None)
    save_pi_settings(settings)


def get_default_model() -> str | None:
    value = load_pi_settings().get("defaultModel")
    return value if isinstance(value, str) and value else None


def set_default_model(model_id: str | None) -> None:
    settings = load_pi_settings()
    if model_id:
        settings["defaultModel"] = model_id
    else:
        settings.pop("defaultModel", None)
    save_pi_settings(settings)


def load_pi_models() -> dict[str, Any]:
    return _load_json(get_pi_models_path(), {})


def save_pi_models(data: dict[str, Any]) -> None:
    _write_json(get_pi_models_path(), data)


def upsert_provider_override(
    provider_id: str,
    *,
    base_url: str | None = None,
    api: str | None = None,
    api_key: str | None = None,
    auth_header: bool | None = None,
    models: list[dict[str, Any]] | None = None,
) -> None:
    payload = load_pi_models()
    providers = payload.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        payload["providers"] = providers

    provider = providers.get(provider_id)
    if not isinstance(provider, dict):
        provider = {}
        providers[provider_id] = provider

    if base_url:
        provider["baseUrl"] = base_url
    if api:
        provider["api"] = api
    if api_key:
        provider["apiKey"] = api_key
    if auth_header is not None:
        provider["authHeader"] = auth_header
    if models is not None:
        provider["models"] = models

    save_pi_models(payload)


def get_provider_override(provider_id: str) -> dict[str, Any] | None:
    payload = load_pi_models()
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        return None
    provider = providers.get(provider_id)
    return provider if isinstance(provider, dict) else None


def get_provider_base_url(provider_id: str) -> str | None:
    provider = get_provider_override(provider_id) or {}
    value = provider.get("baseUrl")
    return value if isinstance(value, str) and value else None


def remove_provider_override(provider_id: str) -> bool:
    payload = load_pi_models()
    providers = payload.get("providers")
    if not isinstance(providers, dict) or provider_id not in providers:
        return False
    del providers[provider_id]
    if providers:
        payload["providers"] = providers
        save_pi_models(payload)
    else:
        path = get_pi_models_path()
        if path.exists():
            path.unlink()
    return True
