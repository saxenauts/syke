"""Auth command group for the Syke CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import cast

import click
from rich.console import Console
from rich.markup import escape

from syke.cli_support.auth_flow import (
    ensure_setup_pi_runtime,
    resolve_activation_model,
    run_interactive_provider_flow,
    verify_provider_activation,
)
from syke.cli_support.exit_codes import SykeAuthException, SykeRuntimeException
from syke.cli_support.providers import describe_provider, provider_payload, render_provider_summary
from syke.cli_support.render import render_section, render_setup_line
from syke.cli_support.setup_support import run_setup_stage
from syke.llm.env import evaluate_provider_readiness

console = Console()


def _ensure_auth_runtime() -> None:
    from syke.llm.pi_client import ensure_pi_binary

    try:
        ensure_pi_binary()
    except (OSError, RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise SykeRuntimeException(
            "Pi runtime is unavailable. Install Node.js (>= 18) and rerun `syke setup`."
        ) from exc


@click.group(invoke_without_command=True)
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Inspect or change the provider Syke will run with."""
    if ctx.invoked_subcommand is None:
        if sys.stdin.isatty():
            run_setup_stage("Checking Pi runtime...", ensure_setup_pi_runtime)
            flow = run_interactive_provider_flow()
            if flow.status != "selected":
                return
            provider = run_setup_stage(
                "Loading provider summary...",
                lambda: provider_payload(ctx.obj.get("provider")),
            )
            console.print(f"\n[bold]syke auth[/bold]  [dim]{ctx.obj['user']}[/dim]")
            render_provider_summary(provider, indent="  ")
            return
        ctx.invoke(auth_status)


@auth.command("status", short_help="Show resolved provider, auth source, model, and endpoint.")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def auth_status(ctx: click.Context, use_json: bool) -> None:
    from syke.llm.pi_client import get_pi_provider_catalog
    from syke.pi_state import get_default_provider, list_credential_providers, load_pi_models

    active = get_default_provider()
    selected_provider_id = ctx.obj.get("provider") or active
    selected = run_setup_stage(
        "Loading provider status...",
        lambda: provider_payload(selected_provider_id),
    )

    configured_pids: set[str] = set(list_credential_providers())
    models_payload = load_pi_models()
    provider_overrides = models_payload.get("providers")
    if isinstance(provider_overrides, dict):
        configured_pids.update(pid for pid in provider_overrides if isinstance(pid, str))
    if active:
        configured_pids.add(active)

    catalog = get_pi_provider_catalog()
    providers_payload = run_setup_stage(
        "Loading configured providers...",
        lambda: [
            describe_provider(pid, selection_source="Pi settings" if pid == active else None)
            for pid in sorted(configured_pids)
        ],
    )

    if use_json:
        click.echo(
            json.dumps(
                {
                    "ok": True,
                    "selected_provider": selected,
                    "active_provider": active,
                    "configured_providers": providers_payload,
                    "available_providers": [
                        entry.id for entry in catalog if entry.id not in configured_pids
                    ],
                },
                indent=2,
            )
        )
        return

    console.print(f"\n[bold]syke auth[/bold]  [dim]{ctx.obj['user']}[/dim]")
    if active:
        render_setup_line("active provider", active, detail="Pi settings")
    else:
        render_setup_line("active provider", "(none)")

    render_provider_summary(selected, indent="  ")

    if configured_pids:
        render_section("Configured Providers")
        for info in providers_payload:
            detail = (
                f"auth {info['auth_source']} • model {info['model']} • endpoint {info['endpoint']}"
            )
            status = "active" if info["id"] == active else "configured"
            if not info.get("configured"):
                status = "unready"
                detail = cast(str, info.get("error") or detail)
            render_setup_line(cast(str, info["id"]), status, detail=detail)

    unconfigured = [entry.id for entry in catalog if entry.id not in configured_pids]
    if unconfigured:
        render_section("Available Providers")
        render_setup_line("available", ", ".join(unconfigured))


@auth.command("set", short_help="Store provider credentials and config.")
@click.argument("provider")
@click.option("--api-key", default=None, help="API key / auth token (required for cloud providers)")
@click.option("--endpoint", default=None, help="API endpoint URL / base URL override")
@click.option("--base-url", default=None, help="Base URL override")
@click.option("--model", default=None, help="Model name (e.g. gpt-5, deepseek-r1)")
@click.option(
    "--api-version",
    default=None,
    help="Provider API version (advanced; env/runtime only)",
)
@click.option(
    "--use",
    "set_active",
    is_flag=True,
    default=False,
    help="Also make this the active provider",
)
@click.pass_context
def auth_set(
    ctx: click.Context,
    provider: str,
    api_key: str | None,
    endpoint: str | None,
    base_url: str | None,
    model: str | None,
    api_version: str | None,
    set_active: bool,
) -> None:
    from syke.llm.pi_client import get_pi_provider_catalog
    from syke.pi_state import (
        set_api_key,
        set_default_model,
        set_default_provider,
        upsert_provider_override,
    )

    _ensure_auth_runtime()
    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    is_known_provider = provider in catalog

    if api_version:
        raise click.UsageError(
            "--api-version is not persisted in Syke's Pi-owned state. "
            "Use Pi-native environment configuration instead."
        )

    if not is_known_provider and (not model or not (base_url or endpoint)):
        valid = ", ".join(sorted(catalog))
        raise click.UsageError(
            f"Unknown provider '{provider}'. Choose one of Pi's built-ins ({valid}) "
            "or provide both --model and --base-url/--endpoint for a custom provider."
        )

    if api_key:
        set_api_key(provider, api_key)

    effective_base_url = endpoint or base_url
    if effective_base_url or not is_known_provider:
        override_api = None if is_known_provider else "openai-completions"
        override_api_key = None if api_key else ("local" if not is_known_provider else None)
        override_models = None
        if not is_known_provider:
            override_models = [{"id": model}]
        upsert_provider_override(
            provider,
            base_url=effective_base_url,
            api=override_api,
            api_key=override_api_key,
            models=override_models,
        )

    if set_active:
        if is_known_provider:
            status = evaluate_provider_readiness(provider)
            if not status.ready:
                raise SykeAuthException(f"Stored partial config for {provider}. {status.detail}")
        selected_model = resolve_activation_model(provider, explicit_model=model)
        verify_provider_activation(provider, selected_model)
        set_default_model(selected_model)
        set_default_provider(provider)
        console.print(
            f"[green]✓[/green] Config stored and [bold]{provider}[/bold] set as active provider."
        )
    else:
        console.print(f"[green]✓[/green] Config stored for [bold]{provider}[/bold].")


@auth.command("login")
@click.argument("provider")
@click.option(
    "--use",
    "set_active",
    is_flag=True,
    default=False,
    help="Also make this the active provider",
)
@click.pass_context
def auth_login(ctx: click.Context, provider: str, set_active: bool) -> None:
    from syke.llm.pi_client import get_pi_provider_catalog, run_pi_oauth_login
    from syke.pi_state import set_default_model, set_default_provider

    _ensure_auth_runtime()
    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    entry = catalog.get(provider)
    if entry is None:
        valid = ", ".join(sorted(catalog))
        raise click.UsageError(f"Unknown provider '{provider}'. Valid: {valid}")
    if not entry.oauth:
        raise click.UsageError(
            f"{provider} does not advertise Pi-native OAuth login. Use `syke auth set ...` instead."
        )

    try:
        use_local_browser = False
        if sys.stdin.isatty():
            use_local_browser = click.confirm(
                "\n  Use this machine's browser for sign-in?",
                default=True,
            )
        run_pi_oauth_login(provider, manual=not use_local_browser)
    except Exception as exc:
        raise SykeAuthException(f"Pi login failed: {escape(str(exc))}") from exc

    if set_active:
        selected_model = resolve_activation_model(provider)
        verify_provider_activation(provider, selected_model)
        set_default_model(selected_model)
        set_default_provider(provider)
    console.print(f"[green]✓[/green] Pi login completed for [bold]{provider}[/bold].")


@auth.command("use")
@click.argument("provider")
@click.pass_context
def auth_use(ctx: click.Context, provider: str) -> None:
    """Set the active LLM provider."""
    from syke.llm.pi_client import get_pi_provider_catalog
    from syke.pi_state import set_default_model, set_default_provider

    _ensure_auth_runtime()
    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    if provider not in catalog:
        valid = ", ".join(sorted(catalog))
        raise click.UsageError(f"Unknown provider '{provider}'. Valid: {valid}")

    status = evaluate_provider_readiness(provider)
    if not status.ready:
        raise SykeAuthException(f"{provider} is not ready. {status.detail}")

    selected_model = resolve_activation_model(provider)
    verify_provider_activation(provider, selected_model)
    set_default_model(selected_model)
    set_default_provider(provider)
    console.print(f"[green]✓[/green] Active provider set to [bold]{provider}[/bold].")


@auth.command("unset")
@click.argument("provider")
@click.pass_context
def auth_unset(ctx: click.Context, provider: str) -> None:
    """Remove stored credentials for a provider."""
    from syke.pi_state import (
        get_default_provider,
        remove_credential,
        set_default_model,
        set_default_provider,
    )

    removed = remove_credential(provider)
    active_cleared = False
    if get_default_provider() == provider:
        set_default_provider(None)
        set_default_model(None)
        active_cleared = True

    if removed and active_cleared:
        console.print(
            f"[green]✓[/green] Credentials removed for [bold]{provider}[/bold]."
            " Active provider cleared."
        )
    elif removed:
        console.print(f"[green]✓[/green] Credentials removed for [bold]{provider}[/bold].")
    elif active_cleared:
        console.print(
            f"[green]✓[/green] Active provider [bold]{provider}[/bold] cleared."
            " No stored credentials remained."
        )
    else:
        console.print(f"[dim]No credentials stored for {provider}.[/dim]")
