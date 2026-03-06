"""Provider registry — data-only specs for each supported provider."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    base_url: str | None = None
    token_env_var: str | None = None
    needs_proxy: bool = False

    @property
    def is_claude_login(self) -> bool:
        return self.id == "claude-login"


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
        needs_proxy=True,
    ),
}
