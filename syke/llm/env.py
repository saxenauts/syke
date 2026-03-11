"""Provider resolution and env building for ClaudeAgentOptions."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from syke.llm.providers import PROVIDERS, ProviderSpec

if TYPE_CHECKING:
    from syke.llm.auth_store import AuthStore

log = logging.getLogger(__name__)

_DEFAULT_PROVIDER = "claude-login"


def _claude_login_available() -> bool:
    claude_dir = Path.home() / ".claude"
    return bool(shutil.which("claude") and claude_dir.is_dir() and any(claude_dir.glob("*.json")))


def _get_auth_store() -> AuthStore:
    from syke.llm.auth_store import AuthStore  # noqa: F811 — runtime import

    return AuthStore()


def resolve_provider(
    cli_provider: str | None = None,
) -> ProviderSpec:
    """Resolve which provider to use.

    Precedence: CLI flag > SYKE_PROVIDER env > auth.json active_provider > auto-detect claude-login > fail.
    """
    # 1. CLI flag
    provider_id = cli_provider

    # 2. Env var
    if not provider_id:
        provider_id = os.getenv("SYKE_PROVIDER")

    # 3. auth.json active_provider
    if not provider_id:
        store = _get_auth_store()
        provider_id = store.get_active_provider()

    if provider_id:
        spec = PROVIDERS.get(provider_id)
        if spec is None:
            valid = ", ".join(sorted(PROVIDERS))
            raise ValueError(f"Unknown provider {provider_id!r}. Valid providers: {valid}")
        return spec

    # 4. Auto-detect claude-login
    if _claude_login_available():
        return PROVIDERS[_DEFAULT_PROVIDER]

    # 5. Fail with actionable message
    msg = (
        "No provider configured. Run `syke auth set <provider> --api-key <key>`"
        " or `claude login` for Claude. See `syke doctor` for details."
    )
    raise RuntimeError(msg)


def build_agent_env(provider: ProviderSpec | None = None) -> dict[str, str]:
    """Build env dict for ClaudeAgentOptions(env=...).

    For claude-login: neutralizes any stray ANTHROPIC_API_KEY.
    For codex: starts local translator proxy, sets ANTHROPIC_BASE_URL to localhost.
    For other providers: sets ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN + ANTHROPIC_API_KEY="".
    """
    if provider is None:
        provider = resolve_provider()

    env: dict[str, str] = {}

    if provider.is_claude_login:
        env["ANTHROPIC_API_KEY"] = ""
        return env

    if provider.api_mode == "codex":
        return _build_codex_env()

    if provider.api_mode == "litellm":
        return _build_litellm_env(provider)

    if provider.base_url:
        env["ANTHROPIC_BASE_URL"] = provider.base_url

    token = _resolve_token(provider)
    if token:
        env["ANTHROPIC_AUTH_TOKEN"] = token

    env["ANTHROPIC_API_KEY"] = ""
    return env


def _build_codex_env() -> dict[str, str]:
    """Start the Codex translator proxy and return env pointing to it."""
    from syke.llm.codex_auth import ensure_valid_token
    from syke.llm.codex_proxy import start_codex_proxy

    creds = ensure_valid_token()
    if creds is None:
        raise RuntimeError(
            "Codex credentials not found or expired. Run `codex login`, then `syke login codex`."
        )
    if not creds.account_id:
        raise RuntimeError(
            "Codex credentials missing account_id. Re-run `codex login` to get a fresh token."
        )

    port = start_codex_proxy(creds.access_token, creds.account_id)
    log.info("Codex proxy active on port %d", port)

    return {
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
        "ANTHROPIC_API_KEY": "sk-ant-api03-codex-proxy-placeholder-000000000000",
        "ANTHROPIC_AUTH_TOKEN": "codex-proxy",
    }


def _build_litellm_env(provider: ProviderSpec) -> dict[str, str]:
    """Start the LiteLLM proxy and return env pointing to it."""
    from syke.llm.litellm_config import write_litellm_config
    from syke.llm.litellm_proxy import start_litellm_proxy

    provider_config = _resolve_provider_config(provider)
    auth_token = _resolve_token(provider)

    config_path = write_litellm_config(provider.id, provider_config, auth_token)
    port = start_litellm_proxy(config_path)
    log.info("LiteLLM proxy active on port %d for provider %s", port, provider.id)

    return {
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}",
        "ANTHROPIC_API_KEY": "sk-syke-local-proxy",
    }


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
    """Resolve provider-specific config. Precedence: config.toml base > env var overrides.

    Merges [providers.<name>] settings from config.toml with standard env var overrides.
    Env vars take precedence over config.toml values.

    Args:
        provider: ProviderSpec to resolve config for.

    Returns:
        Dict of provider config settings (non-secret, e.g. endpoint, base_url, api_version).
    """
    from syke.config import CFG

    # Standard env var overrides per provider
    ENV_VAR_OVERRIDES = {
        "azure": {"AZURE_API_BASE": "endpoint", "AZURE_API_VERSION": "api_version"},
        "azure-ai": {"AZURE_AI_API_BASE": "base_url"},
        "openai": {"OPENAI_BASE_URL": "base_url"},
        "ollama": {"OLLAMA_HOST": "base_url"},
        "vllm": {"VLLM_API_BASE": "base_url"},
        "llama-cpp": {"LLAMA_CPP_API_BASE": "base_url"},
    }

    # Start with config.toml [providers.<name>] as base
    base = dict(CFG.providers.get(provider.id, {}))

    # Apply env var overrides for this provider
    overrides = ENV_VAR_OVERRIDES.get(provider.id, {})
    for env_var, config_key in overrides.items():
        val = os.getenv(env_var)
        if val:
            base[config_key] = val

    return base
