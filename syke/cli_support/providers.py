"""Provider-resolution helpers extracted from the CLI."""

from __future__ import annotations

import os

from rich.console import Console
from rich.markup import escape

from syke.llm.env import evaluate_provider_readiness

console = Console()


def provider_payload(cli_provider: str | None = None) -> dict[str, object]:
    from syke.llm.env import resolve_provider

    try:
        provider = resolve_provider(cli_provider=cli_provider)
        return describe_provider(provider.id, selection_source=resolve_source(cli_provider))
    except (ValueError, RuntimeError) as exc:
        return {
            "configured": False,
            "id": None,
            "source": None,
            "base_url": None,
            "runtime_provider": None,
            "auth_source": None,
            "auth_configured": False,
            "model": None,
            "model_source": None,
            "endpoint": None,
            "endpoint_source": None,
            "error": str(exc),
        }


def describe_provider(
    provider_id: str, *, selection_source: str | None = None
) -> dict[str, object]:
    from syke.llm.pi_client import get_pi_provider_catalog
    from syke.pi_state import (
        get_credential,
        get_default_model,
        get_default_provider,
        get_pi_auth_path,
        get_pi_models_path,
        get_provider_base_url,
        get_provider_override,
    )

    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    entry = catalog.get(provider_id)
    if entry is None:
        return {
            "configured": False,
            "id": provider_id,
            "source": selection_source,
            "base_url": None,
            "runtime_provider": None,
            "auth_source": None,
            "auth_configured": False,
            "model": None,
            "model_source": None,
            "endpoint": None,
            "endpoint_source": None,
            "error": f"Unknown provider {provider_id!r} in Pi catalog",
        }

    readiness = evaluate_provider_readiness(provider_id)
    credential = get_credential(provider_id)
    default_provider = get_default_provider()
    default_model = get_default_model()
    endpoint_override = get_provider_base_url(provider_id)
    provider_override = get_provider_override(provider_id) or {}
    available_models = tuple(getattr(entry, "available_models", ()))
    override_has_request_auth = bool(
        provider_override.get("apiKey")
        or provider_override.get("headers")
        or provider_override.get("authHeader")
    )

    if credential is not None:
        auth_source = str(get_pi_auth_path())
        auth_configured = True
        if credential.get("type") == "oauth":
            auth_source = f"{auth_source} (oauth)"
    elif override_has_request_auth:
        auth_source = f"{get_pi_models_path()} (request config)"
        auth_configured = True
    elif available_models:
        auth_source = "catalog only (not daemon-safe)"
        auth_configured = False
    elif entry.oauth:
        auth_source = "Pi native login"
        auth_configured = False
    else:
        auth_source = "missing"
        auth_configured = False

    if default_provider == provider_id and default_model:
        model = default_model
        model_source = "Pi settings defaultModel"
    elif entry.default_model:
        model = entry.default_model
        model_source = "Pi provider default"
    else:
        model = None
        model_source = None

    if endpoint_override:
        endpoint = endpoint_override
        endpoint_source = "Pi models.json baseUrl"
    elif getattr(entry, "requires_base_url", False):
        if provider_endpoint_configured(provider_id):
            endpoint = "Pi env/resource config"
            endpoint_source = "Pi env/config"
        else:
            endpoint = None
            endpoint_source = "required in Pi config"
    elif entry.models:
        endpoint = "provider default"
        endpoint_source = "Pi built-in/default"
    else:
        endpoint = None
        endpoint_source = None

    return {
        "configured": readiness.ready,
        "id": provider_id,
        "source": selection_source,
        "base_url": endpoint,
        "runtime_provider": provider_id,
        "auth_source": auth_source,
        "auth_configured": auth_configured,
        "model": model,
        "model_source": model_source,
        "endpoint": endpoint,
        "endpoint_source": endpoint_source,
        "error": None if readiness.ready else readiness.detail,
    }


def render_provider_summary(provider_info: dict[str, object], *, indent: str = "") -> None:
    """Print the currently selected runtime provider in a compact, explicit form."""
    if not provider_info.get("configured"):
        error = provider_info.get("error") or "provider not configured"
        console.print(f"{indent}[yellow]Provider unavailable:[/yellow] {escape(str(error))}")
        return

    source = provider_info.get("source")
    source_suffix = f" [dim]({source})[/dim]" if source else ""
    console.print(
        f"{indent}[bold]Runtime[/bold]: [cyan]{provider_info['id']}[/cyan]{source_suffix}"
    )
    console.print(f"{indent}  auth: [cyan]{provider_info.get('auth_source') or 'missing'}[/cyan]")
    console.print(
        f"{indent}  model: [cyan]{provider_info.get('model') or '(none)'}[/cyan]"
        f" [dim]({provider_info.get('model_source') or 'unknown'})[/dim]"
    )
    console.print(
        f"{indent}  endpoint: [cyan]{provider_info.get('endpoint') or '(none)'}[/cyan]"
        f" [dim]({provider_info.get('endpoint_source') or 'unknown'})[/dim]"
    )


def resolve_source(cli_provider: str | None) -> str:
    if cli_provider:
        return "CLI --provider flag"
    if os.getenv("SYKE_PROVIDER"):
        return "SYKE_PROVIDER env"
    from syke.pi_state import get_default_provider

    if get_default_provider():
        return "Pi settings"
    return "unknown"


def provider_endpoint_configured(provider_id: str) -> bool:
    from syke.pi_state import get_provider_base_url

    if get_provider_base_url(provider_id):
        return True
    if provider_id == "azure-openai-responses":
        return bool(
            os.getenv("AZURE_OPENAI_BASE_URL")
            or os.getenv("AZURE_OPENAI_RESOURCE_NAME")
        )
    return False
