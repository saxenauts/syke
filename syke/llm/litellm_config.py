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


def _resolve_base_model(prefix: str, model_name: str) -> str | None:
    """Find the correct LiteLLM cost map key for a model.

    The routing model name (e.g. azure/Kimi-K2.5) often doesn't match
    the cost map key (e.g. azure_ai/kimi-k2.5). Setting base_model
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
            "moonshot",
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
    model_name = provider_config.get("model", "gpt-5")
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
        api_version = provider_config.get("api_version")
        if api_version:
            litellm_params["api_version"] = api_version

    # Claude Code sends Anthropic-specific params that non-Anthropic providers
    # reject. LiteLLM's drop_params only covers OpenAI-known params.
    # Tracks: https://github.com/BerriAI/litellm/issues/22963
    drop_params = ["output_config", "prompt_cache_key"]

    model_lower = model_name.lower()
    is_kimi = "kimi" in model_lower or "moonshot" in model_lower
    model_info: dict[str, object] = {}
    if is_kimi:
        drop_params.extend(["parallel_tool_calls", "strict", "thinking"])
        model_info["supports_parallel_tool_calls"] = False
        model_info["supports_function_calling"] = True
        # Kimi on Azure returns empty streams when tools are present.
        # Force non-streaming — LiteLLM converts the response back to SSE.
        litellm_params["stream"] = False

    litellm_params["additional_drop_params"] = drop_params

    model_entry: dict[str, object] = {
        "model_name": "*",
        "litellm_params": litellm_params,
    }
    if model_info:
        model_entry["model_info"] = model_info

    config = {
        "model_list": [model_entry],
        "litellm_settings": {
            "drop_params": True,
            "modify_params": True,
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
