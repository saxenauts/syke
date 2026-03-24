"""LiteLLM proxy config YAML generation from Syke provider settings."""

from __future__ import annotations

from pathlib import Path

import yaml

# Provider model prefix mapping — LiteLLM requires these prefixes
_MODEL_PREFIXES: dict[str, str] = {
    "azure": "azure",
    "azure-ai": "azure_ai",
    "openai": "openai",
    "ollama": "ollama",
    "vllm": "openai",  # vLLM speaks OpenAI API
    "llama-cpp": "openai",  # llama.cpp speaks OpenAI API
}

# Where each provider's endpoint lives in provider_config dict
_API_BASE_KEYS: dict[str, str] = {
    "azure": "endpoint",
    "azure-ai": "base_url",
    "openai": "base_url",
    "ollama": "base_url",
    "vllm": "base_url",
    "llama-cpp": "base_url",
}

_UNSUPPORTED_LITELLM_MODELS = ("kimi", "moonshot")


def validate_litellm_model(model_name: str) -> None:
    model_lower = model_name.lower()
    if any(name in model_lower for name in _UNSUPPORTED_LITELLM_MODELS):
        raise ValueError(
            "Kimi/Moonshot models are no longer supported through LiteLLM providers. "
            "Use the direct 'kimi' provider instead."
        )


def _resolve_base_model(prefix: str, model_name: str) -> str | None:
    """Find the correct LiteLLM cost map key for a model.

    The routing model name (e.g. azure/Phi-4) often doesn't match
    the cost map key (e.g. azure_ai/phi-4). Setting base_model
    tells LiteLLM which price entry to use without affecting routing.
    """
    try:
        import litellm
    except ImportError:
        return None

    routing_key = f"{prefix}/{model_name}"
    if routing_key in litellm.model_cost:
        return None

    model_lower = model_name.lower()
    candidates = [
        f"{p}/{model_lower}"
        for p in (
            "azure_ai",
            "azure",
            "openrouter",
            "zai",
            "together_ai",
            "fireworks_ai",
            "bedrock",
            "deepinfra",
        )
    ]
    for candidate in candidates:
        if candidate in litellm.model_cost:
            return candidate

    return None


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
    model_name = provider_config.get("model", "gpt-5.4-mini")
    validate_litellm_model(model_name)
    upstream_model = f"{prefix}/{model_name}"

    litellm_params: dict[str, object] = {"model": upstream_model}

    base_model = _resolve_base_model(prefix, model_name)
    if base_model:
        litellm_params["base_model"] = base_model

    # Add api_base if available
    api_base_key = _API_BASE_KEYS.get(provider_id)
    if api_base_key:
        api_base = provider_config.get(api_base_key)
        if api_base:
            litellm_params["api_base"] = api_base

    # Add api_key only if provided (local providers like ollama don't need it)
    if auth_token:
        litellm_params["api_key"] = auth_token

    if provider_id in ("azure", "azure-ai"):
        # Skip api_version for reasoning models — the Responses API
        # (routed via _enable_azure_responses_api) uses the v1 path
        # which doesn't accept dated api_version params.
        is_reasoning_model = any(r in model_name.lower() for r in ("gpt-5", "o1", "o3", "o4"))
        if not is_reasoning_model:
            api_version = provider_config.get("api_version")
            if api_version:
                litellm_params["api_version"] = api_version

    # Claude Code sends Anthropic-specific params that non-Anthropic providers
    # reject. LiteLLM's drop_params only covers OpenAI-known params.
    # Tracks: https://github.com/BerriAI/litellm/issues/22963
    drop_params = ["output_config", "prompt_cache_key"]

    litellm_params["additional_drop_params"] = drop_params

    model_entry: dict[str, object] = {
        "model_name": "*",
        "litellm_params": litellm_params,
    }

    config = {
        "model_list": [model_entry],
        "litellm_settings": {
            "drop_params": True,
            "modify_params": True,
            "reasoning_auto_summary": True,
            "num_retries": 3,
            "retry_after": 15,
            "request_timeout": 300,
        },
        "general_settings": {"master_key": "sk-syke-local-proxy"},
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
