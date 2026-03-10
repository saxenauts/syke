"""LiteLLM proxy config YAML generation from Syke provider settings."""

from __future__ import annotations

from pathlib import Path

import yaml

# Provider model prefix mapping — LiteLLM requires these prefixes
_MODEL_PREFIXES: dict[str, str] = {
    "azure": "azure",
    "openai": "openai",
    "ollama": "ollama",
    "vllm": "openai",  # vLLM speaks OpenAI API
    "llama-cpp": "openai",  # llama.cpp speaks OpenAI API
}

# Where each provider's endpoint lives in provider_config dict
_API_BASE_KEYS: dict[str, str] = {
    "azure": "endpoint",
    "openai": "base_url",
    "ollama": "base_url",
    "vllm": "base_url",
    "llama-cpp": "base_url",
}


def generate_litellm_config(
    provider_id: str,
    provider_config: dict[str, str],
    auth_token: str | None,
) -> str:
    """Generate LiteLLM proxy config YAML string.

    Args:
        provider_id: Provider ID (e.g., "azure", "ollama")
        provider_config: Non-secret provider settings from config.toml
                         (endpoint, base_url, model, api_version, etc.)
        auth_token: API key/token from auth.json. None for local providers.

    Returns:
        YAML string suitable for LiteLLM proxy --config flag.
    """
    prefix = _MODEL_PREFIXES.get(provider_id, provider_id)
    model_name = provider_config.get("model", "gpt-4o")
    upstream_model = f"{prefix}/{model_name}"

    litellm_params: dict[str, object] = {"model": upstream_model}

    # Add api_base if available
    api_base_key = _API_BASE_KEYS.get(provider_id)
    if api_base_key:
        api_base = provider_config.get(api_base_key)
        if api_base:
            litellm_params["api_base"] = api_base

    # Add api_key only if provided (local providers like ollama don't need it)
    if auth_token:
        litellm_params["api_key"] = auth_token

    # Azure-specific: api_version
    if provider_id == "azure":
        api_version = provider_config.get("api_version")
        if api_version:
            litellm_params["api_version"] = api_version

    config = {
        "model_list": [
            {
                "model_name": "*",  # wildcard — accept any model name from Claude CLI
                "litellm_params": litellm_params,
            }
        ],
        "general_settings": {"master_key": None},
    }

    return yaml.dump(config, default_flow_style=False, allow_unicode=True)


def write_litellm_config(
    provider_id: str,
    provider_config: dict[str, str],
    auth_token: str | None,
    path: Path | None = None,
) -> Path:
    """Write LiteLLM proxy config YAML to disk.

    Args:
        provider_id: Provider ID (e.g., "azure", "ollama")
        provider_config: Non-secret provider settings from config.toml
        auth_token: API key/token from auth.json. None for local providers.
        path: Where to write the config. Defaults to ~/.syke/litellm_config.yaml

    Returns:
        Path where the config was written.
    """
    if path is None:
        path = Path.home() / ".syke" / "litellm_config.yaml"

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_litellm_config(provider_id, provider_config, auth_token))
    return path
