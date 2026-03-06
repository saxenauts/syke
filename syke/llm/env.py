"""Provider resolution and env building for ClaudeAgentOptions."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from syke.llm.providers import PROVIDERS, ProviderSpec

log = logging.getLogger(__name__)

_DEFAULT_PROVIDER = "claude-login"


def _claude_login_available() -> bool:
    claude_dir = Path.home() / ".claude"
    return bool(
        shutil.which("claude")
        and claude_dir.is_dir()
        and any(claude_dir.glob("*.json"))
    )


def resolve_provider(
    cli_provider: str | None = None,
) -> ProviderSpec:
    """Resolve which provider to use. Precedence: CLI flag > env > auth.json > auto-detect.

    Phase 1: CLI flag, SYKE_PROVIDER env, and claude-login auto-detect.
    Phase 2 adds auth.json lookup.
    """
    provider_id = cli_provider or os.getenv("SYKE_PROVIDER")

    if provider_id:
        spec = PROVIDERS.get(provider_id)
        if spec is None:
            valid = ", ".join(sorted(PROVIDERS))
            raise ValueError(
                f"Unknown provider {provider_id!r}. Valid providers: {valid}"
            )
        return spec

    if _claude_login_available():
        return PROVIDERS[_DEFAULT_PROVIDER]

    msg = (
        "No provider configured. Run `syke auth set <provider> --api-key <key>`"
        " or `claude login` for Claude. See `syke doctor` for details."
    )
    raise RuntimeError(msg)


def build_agent_env(provider: ProviderSpec | None = None) -> dict[str, str]:
    """Build env dict for ClaudeAgentOptions(env=...).

    For claude-login: neutralizes any stray ANTHROPIC_API_KEY.
    For other providers: sets ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN + ANTHROPIC_API_KEY="".
    """
    if provider is None:
        provider = resolve_provider()

    env: dict[str, str] = {}

    if provider.is_claude_login:
        env["ANTHROPIC_API_KEY"] = ""
        return env

    if provider.base_url:
        env["ANTHROPIC_BASE_URL"] = provider.base_url

    token = _resolve_token(provider)
    if token:
        env["ANTHROPIC_AUTH_TOKEN"] = token

    env["ANTHROPIC_API_KEY"] = ""
    return env


def _resolve_token(provider: ProviderSpec) -> str | None:
    """Resolve auth token for a provider. Precedence: env var > auth.json (Phase 2)."""
    if provider.token_env_var:
        val = os.getenv(provider.token_env_var)
        if val:
            return val

    return None
