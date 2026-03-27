"""Provider registry — data-only specs for each supported provider."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    base_url: str | None = None
    token_env_var: str | None = None
    api_mode: str = "pi"
    pi_provider: str | None = None
    pi_api_key_env_var: str | None = None

    @property
    def needs_proxy(self) -> bool:
        """Legacy compatibility property; Pi-native providers do not need local translation proxies."""
        return self.api_mode in ("litellm", "codex")

    @property
    def requires_litellm(self) -> bool:
        """Legacy compatibility property; LiteLLM routing was removed."""
        return self.api_mode == "litellm"


PROVIDERS: dict[str, ProviderSpec] = {
    "openrouter": ProviderSpec(
        id="openrouter",
        base_url="https://openrouter.ai/api",
        token_env_var="SYKE_OPENROUTER_API_KEY",
        pi_provider="openrouter",
        pi_api_key_env_var="OPENROUTER_API_KEY",
    ),
    "zai": ProviderSpec(
        id="zai",
        base_url="https://api.z.ai/api/anthropic",
        token_env_var="SYKE_ZAI_API_KEY",
        pi_provider="zai",
        pi_api_key_env_var="ZAI_API_KEY",
    ),
    "kimi": ProviderSpec(
        id="kimi",
        base_url="https://api.kimi.com/coding",
        token_env_var="SYKE_KIMI_API_KEY",
        pi_provider="kimi-coding",
        pi_api_key_env_var="KIMI_API_KEY",
    ),
    "codex": ProviderSpec(
        id="codex",
        api_mode="codex",
        pi_provider="openai-codex",
    ),
    "azure": ProviderSpec(
        id="azure",
        token_env_var="AZURE_API_KEY",
        pi_provider="azure-openai-responses",
        pi_api_key_env_var="AZURE_OPENAI_API_KEY",
    ),
    "openai": ProviderSpec(
        id="openai",
        token_env_var="OPENAI_API_KEY",
        pi_provider="openai",
        pi_api_key_env_var="OPENAI_API_KEY",
    ),
    "ollama": ProviderSpec(
        id="ollama",
        base_url="http://localhost:11434",
    ),
    "vllm": ProviderSpec(
        id="vllm",
    ),
    "llama-cpp": ProviderSpec(
        id="llama-cpp",
    ),
}
