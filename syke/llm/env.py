"""Provider resolution and Pi runtime env building.

Syke resolves provider/model defaults from its Pi-owned agent state and lets
Pi handle built-in provider semantics, auth precedence, and runtime behavior.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from syke.llm.pi_client import get_pi_provider_catalog
from syke.pi_state import (
    build_pi_agent_env,
    get_credential,
    get_default_model,
    get_default_provider,
    get_provider_base_url,
)


@dataclass(frozen=True)
class ProviderSelection:
    id: str


@dataclass(frozen=True)
class ProviderReadiness:
    provider_id: str
    ready: bool
    detail: str


def _catalog_by_id() -> dict[str, object]:
    return {entry.id: entry for entry in get_pi_provider_catalog()}


def resolve_provider(cli_provider: str | None = None) -> ProviderSelection:
    """Resolve which provider to use.

    Precedence: CLI flag > SYKE_PROVIDER env > Pi settings defaultProvider > fail.
    """
    provider_id = cli_provider or os.getenv("SYKE_PROVIDER") or get_default_provider()
    if not provider_id:
        raise RuntimeError(
            "No provider configured. Run `syke setup`, `syke auth use <provider>`, "
            "or `syke auth set <provider> ... --use`."
        )

    catalog = _catalog_by_id()
    if provider_id not in catalog:
        valid = ", ".join(sorted(catalog))
        raise ValueError(f"Unknown provider {provider_id!r}. Valid providers: {valid}")
    return ProviderSelection(id=provider_id)


def build_pi_runtime_env(provider: ProviderSelection | None = None) -> dict[str, str]:
    """Build env vars for the Pi subprocess."""
    _ = provider or resolve_provider()
    return build_pi_agent_env()


def _resolve_provider_config(provider: ProviderSelection | str) -> dict[str, str]:
    provider_id = provider.id if isinstance(provider, ProviderSelection) else provider
    config: dict[str, str] = {}
    default_provider = get_default_provider()
    default_model = get_default_model()
    if default_provider == provider_id and default_model:
        config["model"] = default_model
    return config


def _has_api_key_credential(provider_id: str) -> bool:
    credential = get_credential(provider_id)
    return bool(isinstance(credential, dict) and credential.get("type") == "api_key")


def _has_oauth_credential(provider_id: str) -> bool:
    credential = get_credential(provider_id)
    return bool(isinstance(credential, dict) and credential.get("type") == "oauth")


def evaluate_provider_readiness(provider_id: str) -> ProviderReadiness:
    """Return whether a provider is ready to be marked active and why."""
    catalog = _catalog_by_id()
    entry = catalog.get(provider_id)
    if entry is None:
        raise ValueError(f"Unknown provider {provider_id!r}")

    models = getattr(entry, "models", ())
    available_models = getattr(entry, "available_models", ())
    oauth = bool(getattr(entry, "oauth", False))
    default_provider = get_default_provider()
    default_model = get_default_model()

    if bool(getattr(entry, "requires_base_url", False)) and not get_provider_base_url(provider_id):
        return ProviderReadiness(
            provider_id,
            False,
            "Configure a base URL/resource endpoint in Pi config before selecting a model.",
        )

    if available_models:
        if default_provider == provider_id and default_model and default_model not in models:
            return ProviderReadiness(
                provider_id,
                False,
                f"Configured default model {default_model!r} is not available for {provider_id!r}.",
            )
        return ProviderReadiness(provider_id, True, "Pi runtime configured")

    if oauth:
        return ProviderReadiness(
            provider_id,
            False,
            f"Run `syke auth login {provider_id}` or use Pi's `/login` flow.",
        )

    if not models:
        return ProviderReadiness(
            provider_id,
            False,
            f"No models configured for {provider_id!r}. Add Pi-native provider config first.",
        )

    if _has_api_key_credential(provider_id):
        return ProviderReadiness(
            provider_id,
            False,
            f"Credentials exist for {provider_id!r}, but Pi reports no available models. "
            "Check provider configuration or endpoint overrides.",
        )

    if _has_oauth_credential(provider_id):
        return ProviderReadiness(
            provider_id,
            False,
            f"OAuth credentials exist for {provider_id!r}, but Pi reports no available models.",
        )

    return ProviderReadiness(
        provider_id,
        False,
        f"No auth configured for {provider_id!r}. Run `syke auth set {provider_id} ... --use` "
        f"or `syke auth login {provider_id}`.",
    )
