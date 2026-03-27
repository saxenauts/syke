"""Provider resolution and Pi runtime env building.

Syke no longer builds Claude SDK subprocess environments. This module now
resolves the active provider and translates Syke auth/config into the env vars
Pi expects for its native provider system.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from syke.llm.providers import PROVIDERS, ProviderSpec

log = logging.getLogger(__name__)


def _get_auth_store():
    from syke.llm.auth_store import AuthStore  # runtime import to avoid import cycles

    return AuthStore()


def resolve_provider(cli_provider: str | None = None) -> ProviderSpec:
    """Resolve which provider to use.

    Precedence: CLI flag > SYKE_PROVIDER env > auth.json active_provider > fail.
    """
    provider_id = cli_provider or os.getenv("SYKE_PROVIDER")

    if not provider_id:
        store = _get_auth_store()
        provider_id = store.get_active_provider()

    if not provider_id:
        raise RuntimeError(
            "No provider configured. Run `syke auth use <provider>` or "
            "`syke auth set <provider> --api-key <key>`."
        )

    spec = PROVIDERS.get(provider_id)
    if spec is None:
        valid = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown provider {provider_id!r}. Valid providers: {valid}")
    return spec


def _claude_login_available() -> bool:
    """Legacy compatibility check for repos still surfacing claude-login."""
    claude_dir = Path.home() / ".claude"
    return shutil.which("claude") is not None and claude_dir.is_dir() and any(claude_dir.glob("*.json"))


def build_pi_runtime_env(provider: ProviderSpec | None = None) -> dict[str, str]:
    """Build env vars for the Pi subprocess."""
    provider = provider or resolve_provider()
    provider_config = _resolve_provider_config(provider)
    token = _resolve_token(provider)

    env: dict[str, str] = {}

    if provider.pi_api_key_env_var and token:
        env[provider.pi_api_key_env_var] = token

    if provider.id == "openai":
        base_url = provider_config.get("base_url")
        if base_url:
            env["OPENAI_BASE_URL"] = base_url

    if provider.id == "azure":
        endpoint = provider_config.get("endpoint") or provider_config.get("base_url")
        if endpoint:
            env["AZURE_OPENAI_BASE_URL"] = _normalize_azure_pi_base_url(endpoint)
        env["AZURE_OPENAI_API_VERSION"] = _normalize_azure_pi_api_version(
            provider_config.get("api_version")
        )

    if provider.id in {"vllm", "llama-cpp"} and token:
        # Custom Pi workspace providers use a neutral API key env name.
        env["SYKE_PI_API_KEY"] = token

    return env


def _normalize_azure_pi_base_url(endpoint: str) -> str:
    """Translate legacy Syke Azure config into Pi's Responses base URL shape."""
    split = urlsplit(endpoint.strip())
    path = split.path.rstrip("/")

    if "/deployments/" in path:
        raise RuntimeError(
            "Azure deployment URLs are not supported by Pi-native runtime. "
            "Configure the Azure resource endpoint instead."
        )

    if path.endswith("/responses"):
        path = path[: -len("/responses")]

    if path in {"", "/"}:
        path = "/openai/v1"
    elif path == "/openai":
        path = "/openai/v1"
    elif path == "/openai/v1":
        pass

    return urlunsplit((split.scheme, split.netloc, path, "", ""))


def _normalize_azure_pi_api_version(api_version: str | None) -> str:
    """Pi's Azure Responses runtime is standardized on the v1 API contract."""
    if api_version and api_version.strip().lower() == "v1":
        return "v1"
    return "v1"


def build_agent_env(provider: ProviderSpec | None = None) -> dict[str, str]:
    """Compatibility alias for callers that still import the old name."""
    return build_pi_runtime_env(provider)


def _resolve_token(provider: ProviderSpec) -> str | None:
    """Resolve auth token. Precedence: provider-specific env var > auth.json."""
    if provider.token_env_var:
        val = os.getenv(provider.token_env_var)
        if val:
            return val

    store = _get_auth_store()
    token = store.get_token(provider.id)
    if token:
        return token

    return None


def _resolve_provider_config(provider: ProviderSpec) -> dict[str, str]:
    """Resolve provider-specific config. Precedence: config.toml base > env var overrides."""
    from syke.config import CFG

    env_var_overrides = {
        "azure": {"AZURE_API_BASE": "endpoint", "AZURE_API_VERSION": "api_version"},
        "azure-ai": {"AZURE_AI_API_BASE": "base_url", "AZURE_AI_API_VERSION": "api_version"},
        "openai": {"OPENAI_BASE_URL": "base_url"},
        "ollama": {"OLLAMA_HOST": "base_url"},
        "vllm": {"VLLM_API_BASE": "base_url"},
        "llama-cpp": {"LLAMA_CPP_API_BASE": "base_url"},
    }

    base = dict(CFG.providers.get(provider.id, {}))
    for env_var, config_key in env_var_overrides.get(provider.id, {}).items():
        val = os.getenv(env_var)
        if val:
            base[config_key] = val
    return base
