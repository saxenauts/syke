"""Pi state helpers.

Syke uses Pi's native `~/.pi/agent` state by default and only overrides the
state root via `SYKE_PI_AGENT_DIR` for tests or explicitly isolated runs.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import traceback
from pathlib import Path
from typing import Any

_PI_AGENT_DIR_ENV = "SYKE_PI_AGENT_DIR"
_PI_STATE_AUDIT_PATH_ENV = "SYKE_PI_STATE_AUDIT_PATH"


def get_pi_agent_dir() -> Path:
    root = os.getenv(_PI_AGENT_DIR_ENV)
    if root:
        return Path(root).expanduser().resolve()
    return (Path.home() / ".pi" / "agent").resolve()


def get_pi_auth_path() -> Path:
    return get_pi_agent_dir() / "auth.json"


def get_pi_settings_path() -> Path:
    return get_pi_agent_dir() / "settings.json"


def get_pi_models_path() -> Path:
    return get_pi_agent_dir() / "models.json"


def get_pi_state_audit_path() -> Path:
    override = os.getenv(_PI_STATE_AUDIT_PATH_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".config" / "syke" / "pi-state-audit.log").resolve()


def ensure_pi_agent_dir() -> Path:
    root = get_pi_agent_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_pi_agent_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    if os.getenv(_PI_AGENT_DIR_ENV):
        env["PI_CODING_AGENT_DIR"] = str(ensure_pi_agent_dir())
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


def _audit_stack() -> list[str]:
    frames = traceback.extract_stack(limit=12)[:-2]
    kept: list[str] = []
    for frame in reversed(frames):
        if frame.filename.endswith("pi_state.py"):
            continue
        kept.append(f"{frame.filename}:{frame.lineno}:{frame.name}")
        if len(kept) >= 5:
            break
    kept.reverse()
    return kept


def _append_pi_state_audit(
    *,
    event: str,
    path: Path,
    before: dict[str, Any],
    after: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    from datetime import UTC, datetime

    audit_path = get_pi_state_audit_path()
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        "path": str(path),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "cwd": os.getcwd(),
        "argv": list(os.sys.argv),
        "before": before,
        "after": after,
        "metadata": metadata or {},
        "stack": _audit_stack(),
    }
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def load_pi_auth() -> dict[str, Any]:
    return _load_json(get_pi_auth_path(), {})


def save_pi_auth(data: dict[str, Any], *, reason: str = "save_pi_auth") -> None:
    path = get_pi_auth_path()
    before = load_pi_auth()
    _write_json(
        path,
        data,
        mode=stat.S_IRUSR | stat.S_IWUSR,
    )
    _append_pi_state_audit(event=reason, path=path, before=before, after=data)


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
    save_pi_auth(auth, reason="set_api_key")


def remove_credential(provider_id: str) -> bool:
    auth = load_pi_auth()
    if provider_id not in auth:
        return False
    del auth[provider_id]
    save_pi_auth(auth, reason="remove_credential")
    if get_default_provider() == provider_id:
        set_default_provider(None)
        set_default_model(None)
    return True


def load_pi_settings() -> dict[str, Any]:
    return _load_json(get_pi_settings_path(), {})


def save_pi_settings(data: dict[str, Any], *, reason: str = "save_pi_settings") -> None:
    path = get_pi_settings_path()
    before = load_pi_settings()
    _write_json(path, data)
    _append_pi_state_audit(event=reason, path=path, before=before, after=data)


def get_default_provider() -> str | None:
    value = load_pi_settings().get("defaultProvider")
    return value if isinstance(value, str) and value else None


def set_default_provider(provider_id: str | None) -> None:
    settings = load_pi_settings()
    if provider_id:
        settings["defaultProvider"] = provider_id
    else:
        settings.pop("defaultProvider", None)
    save_pi_settings(settings, reason="set_default_provider")


def get_default_model() -> str | None:
    value = load_pi_settings().get("defaultModel")
    return value if isinstance(value, str) and value else None


def set_default_model(model_id: str | None) -> None:
    settings = load_pi_settings()
    if model_id:
        settings["defaultModel"] = model_id
    else:
        settings.pop("defaultModel", None)
    save_pi_settings(settings, reason="set_default_model")


def load_pi_models() -> dict[str, Any]:
    return _load_json(get_pi_models_path(), {})


def save_pi_models(data: dict[str, Any], *, reason: str = "save_pi_models") -> None:
    path = get_pi_models_path()
    before = load_pi_models()
    _write_json(path, data)
    _append_pi_state_audit(event=reason, path=path, before=before, after=data)


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

    save_pi_models(payload, reason="upsert_provider_override")


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
        save_pi_models(payload, reason="remove_provider_override")
    else:
        path = get_pi_models_path()
        if path.exists():
            path.unlink()
    return True
