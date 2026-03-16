"""Simple prompt → string LLM callable for one-shot code generation."""

from __future__ import annotations

import logging
from collections.abc import Callable

from syke.llm.env import resolve_provider, _resolve_token, _resolve_provider_config
from syke.llm.providers import ProviderSpec

log = logging.getLogger(__name__)


def build_llm_fn(model: str | None = None) -> Callable[[str], str]:
    """Build a prompt → string callable using the active provider.

    Handles both Anthropic-native providers (kimi, zai, openrouter, claude-login)
    and LiteLLM-proxied providers (azure, openai, ollama) without starting a proxy.
    """
    provider = resolve_provider()
    token = _resolve_token(provider)

    if provider.api_mode == "litellm":
        return _build_litellm_fn(provider, token, model)
    else:
        return _build_anthropic_fn(provider, token, model)


def _build_litellm_fn(
    provider: ProviderSpec, token: str | None, model: str | None
) -> Callable[[str], str]:
    """LiteLLM SDK direct call for azure, openai, ollama, etc."""
    import litellm

    from syke.llm.litellm_config import _MODEL_PREFIXES

    config = _resolve_provider_config(provider)
    prefix = _MODEL_PREFIXES.get(provider.id, provider.id)
    model_name = model or config.get("model", "gpt-5")
    litellm_model = f"{prefix}/{model_name}"

    api_base = config.get("endpoint") or config.get("base_url")
    api_version = config.get("api_version")

    log.info("LLM callable: %s via litellm (%s)", litellm_model, provider.id)

    def call(prompt: str) -> str:
        response = litellm.completion(
            model=litellm_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            api_key=token,
            api_base=api_base,
            api_version=api_version,
        )
        return response.choices[0].message.content

    return call


def _build_anthropic_fn(
    provider: ProviderSpec, token: str | None, model: str | None
) -> Callable[[str], str]:
    """Anthropic SDK direct call for kimi, zai, openrouter, claude-login."""
    from anthropic import Anthropic

    kwargs: dict[str, str] = {}
    if provider.base_url:
        kwargs["base_url"] = provider.base_url
    if token:
        kwargs["api_key"] = token

    client = Anthropic(**kwargs)
    model_name = model or "sonnet"

    log.info("LLM callable: %s via anthropic SDK (%s)", model_name, provider.id)

    def call(prompt: str) -> str:
        msg = client.messages.create(
            model=model_name,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    return call
