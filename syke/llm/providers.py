"""Provider registry — data-only specs for each supported provider."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    base_url: str | None = None
    token_env_var: str | None = None
    api_mode: str = "anthropic"

    @property
    def is_claude_login(self) -> bool:
        return self.id == "claude-login"

    @property
    def needs_proxy(self) -> bool:
        """True for providers that need a local translation proxy (litellm or codex)."""
        return self.api_mode in ("litellm", "codex")

    @property
    def requires_litellm(self) -> bool:
        """True for providers that use LiteLLM as the translation layer."""
        return self.api_mode == "litellm"


PROVIDERS: dict[str, ProviderSpec] = {
    "claude-login": ProviderSpec(
        id="claude-login",
    ),
    "openrouter": ProviderSpec(
        id="openrouter",
        base_url="https://openrouter.ai/api",
        token_env_var="SYKE_OPENROUTER_API_KEY",
    ),
    "zai": ProviderSpec(
        id="zai",
        base_url="https://api.z.ai/api/anthropic",
        token_env_var="SYKE_ZAI_API_KEY",
    ),
    "kimi": ProviderSpec(
        id="kimi",
        base_url="https://api.kimi.com/coding",
        token_env_var="SYKE_KIMI_API_KEY",
    ),
    "codex": ProviderSpec(
        id="codex",
        api_mode="codex",
    ),
    "azure": ProviderSpec(
        id="azure",
        token_env_var="AZURE_API_KEY",
        api_mode="litellm",
    ),
    "openai": ProviderSpec(
        id="openai",
        token_env_var="OPENAI_API_KEY",
        api_mode="litellm",
    ),
    "ollama": ProviderSpec(
        id="ollama",
        base_url="http://localhost:11434",
        api_mode="litellm",
    ),
    "vllm": ProviderSpec(
        id="vllm",
        api_mode="litellm",
    ),
    "llama-cpp": ProviderSpec(
        id="llama-cpp",
        api_mode="litellm",
    ),
}
