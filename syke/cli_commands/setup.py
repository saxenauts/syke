"""Setup command for the Syke CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import click
from rich.console import Console

from syke.cli_support.auth_flow import (
    ensure_setup_pi_runtime,
    run_interactive_provider_flow,
    verify_setup_provider_connection,
)
from syke.cli_support.exit_codes import SykeAuthException
from syke.cli_support.installers import run_managed_checkout_install
from syke.cli_support.providers import provider_payload
from syke.cli_support.render import render_section
from syke.cli_support.setup_support import (
    build_setup_inspect_payload,
    choose_setup_sources_interactive,
    render_setup_inspect_summary,
    run_setup_stage,
    setup_daemon_viability_payload,
)
from syke.config import _is_source_install

console = Console()


def _launch_background_onboarding(
    *,
    user_id: str,
    selected_sources: list[str],
    start_daemon_after: bool,
) -> Path:
    from syke.daemon.daemon import LOG_PATH

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "syke", "--user", user_id, "sync"]
    for source in selected_sources:
        cmd.extend(["--source", source])
    if start_daemon_after:
        cmd.append("--start-daemon-after")

    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            cwd=os.getcwd(),
        )
    return LOG_PATH


@click.command(short_help="Review and apply local memory setup.")
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help=(
        "Auto-consent non-auth confirmations; requires an already configured "
        "provider unless --provider is set"
    ),
)
@click.option(
    "--json", "use_json", is_flag=True, help="Inspect setup state as JSON without side effects"
)
@click.option("--skip-daemon", is_flag=True, help="Skip daemon install (testing only)")
@click.option(
    "--source",
    "selected_sources_cli",
    multiple=True,
    help="Only connect selected detected source(s). Repeatable.",
)
@click.pass_context
def setup(
    ctx: click.Context,
    yes: bool,
    use_json: bool,
    skip_daemon: bool,
    selected_sources_cli: tuple[str, ...],
) -> None:
    """Inspect current setup state, then apply the approved local memory plan."""
    from syke.llm.env import resolve_provider

    user_id = ctx.obj["user"]
    if use_json:
        click.echo(
            json.dumps(
                build_setup_inspect_payload(
                    user_id=user_id,
                    cli_provider=ctx.obj.get("provider"),
                ),
                indent=2,
            )
        )
        return

    console.print(f"\n[bold]syke setup[/bold]  [dim]{user_id}[/dim]")

    cli_provider = ctx.obj.get("provider")
    inspect_info = run_setup_stage(
        "Preparing setup plan...",
        lambda: build_setup_inspect_payload(
            user_id=user_id,
            cli_provider=cli_provider,
        ),
    )
    render_setup_inspect_summary(inspect_info)
    if not yes and not click.confirm("\nApply this setup plan?"):
        console.print("\n  [dim]No changes made.[/dim]")
        return

    detected_sources = [
        cast(dict[str, object], item)["source"]
        for item in cast(list[dict[str, object]], inspect_info.get("sources") or [])
        if cast(dict[str, object], item).get("detected")
    ]
    selected_sources = detected_sources
    if selected_sources_cli:
        requested = list(dict.fromkeys(selected_sources_cli))
        unknown = [source for source in requested if source not in detected_sources]
        if unknown:
            raise click.UsageError(
                f"Requested source(s) not detected during setup: {', '.join(unknown)}"
            )
        selected_sources = requested
    elif not yes and detected_sources:
        selected_sources = choose_setup_sources_interactive(
            cast(list[dict[str, object]], inspect_info.get("sources") or [])
        )

    render_section("Sources")
    if selected_sources:
        skipped_sources = [source for source in detected_sources if source not in selected_sources]
        console.print(f"  [green]✓[/green] {', '.join(selected_sources)}")
        if skipped_sources:
            console.print(f"  [dim]· skipped: {', '.join(skipped_sources)}[/dim]")
    elif detected_sources:
        console.print(f"  [dim]· none selected (skipped: {', '.join(detected_sources)})[/dim]")
    else:
        console.print("  [dim]· none detected[/dim]")

    render_section("Runtime")
    run_setup_stage("Checking Pi runtime…", ensure_setup_pi_runtime)

    render_section("Provider")
    has_provider = False
    interactive_provider_selected = False

    if cli_provider:
        try:
            provider = resolve_provider(cli_provider=cli_provider)
            has_provider = True
            console.print(f"  [green]✓[/green]  Provider: [bold]{provider.id}[/bold]")
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
        except RuntimeError as exc:
            raise SykeAuthException(str(exc)) from exc
    elif not yes and sys.stdin.isatty():
        flow = run_interactive_provider_flow()
        has_provider = flow.status == "selected"
        interactive_provider_selected = has_provider
    elif cast(dict[str, object], inspect_info["provider"]).get("configured"):
        has_provider = True
    else:
        raise SykeAuthException(
            "Setup requires a configured provider. Run `syke auth set <provider> ... --use`, "
            "`syke auth login <provider> --use`, or rerun setup interactively."
        )

    if not has_provider:
        raise SykeAuthException(
            "Setup requires a configured provider. Run `syke auth set <provider> ... --use`, "
            "`syke auth login <provider> --use`, or rerun setup interactively."
        )

    provider_info = provider_payload(ctx.obj.get("provider"))
    if provider_info.get("configured") and not interactive_provider_selected:
        pid = cast(str, provider_info.get("id", "unknown"))
        mid = cast(str, provider_info.get("model", ""))
        auth = cast(str, provider_info.get("auth_source", ""))
        console.print(f"  [green]✓[/green] {pid}  {mid}  [dim]{auth}[/dim]")

    provider_id = cast(str | None, provider_info.get("id"))
    model_id = cast(str | None, provider_info.get("model"))
    handshake = ""
    if not provider_id or not model_id:
        raise SykeAuthException("Setup requires a provider and model before ingest can begin.")
    if not interactive_provider_selected:
        handshake = run_setup_stage(
            f"Verifying {provider_id}/{model_id}…",
            lambda: verify_setup_provider_connection(provider_id, model_id),
        )
        console.print(f"  [green]✓[/green] {provider_id}/{model_id} connected")

    daemon_after_onboarding = not skip_daemon
    daemon_info = cast(dict[str, object], inspect_info["daemon"])
    if (
        daemon_after_onboarding
        and not daemon_info.get("installable")
        and daemon_info.get("platform") == "Darwin"
        and _is_source_install()
    ):
        if yes or click.confirm(
            "\nThis checkout is not launchd-safe on macOS. Install a managed tool build "
            "for this checkout so background sync can run?",
            default=True,
        ):
            try:
                run_setup_stage(
                    "Installing launchd-safe managed build...",
                    lambda: run_managed_checkout_install(
                        user_id=user_id,
                        installer="auto",
                        restart_daemon=False,
                        prompt=False,
                    ),
                )
                daemon_info = setup_daemon_viability_payload()
            except click.ClickException as exc:
                daemon_info = {
                    **daemon_info,
                    "detail": str(exc),
                    "remediation": (
                        "Install a managed build with `syke install-current` or fix the "
                        "local installer tooling, then rerun setup."
                    ),
                }
    if (
        not yes
        and daemon_after_onboarding
        and daemon_info.get("installable")
        and not daemon_info.get("running")
        and not click.confirm("\nEnable background sync after onboarding?", default=True)
    ):
        daemon_after_onboarding = False
        console.print("  [dim]Background sync will stay off after onboarding.[/dim]")

    log_path = _launch_background_onboarding(
        user_id=user_id,
        selected_sources=selected_sources,
        start_daemon_after=daemon_after_onboarding,
    )

    console.print("\n[bold green]✓ Setup complete[/bold green]\n")

    render_section("Ready now")
    console.print('  syke ask "…"       [dim]query your memory[/dim]')
    console.print('  syke record "…"    [dim]save a note[/dim]')

    render_section("Building in background")
    sources_label = ", ".join(selected_sources) if selected_sources else "none"
    console.print(f"  [dim]…[/dim] ingesting {len(selected_sources)} source(s): {sources_label}")
    console.print("  [dim]…[/dim] synthesizing first memex")
    console.print(
        "  [dim]…[/dim] installing skill file to detected agent harnesses"
    )
    if daemon_after_onboarding:
        console.print("  [dim]…[/dim] background sync starts after onboarding")
    console.print(
        "\n  [dim]Syke keeps all your agents in sync. Once onboarding finishes,"
        "\n  every connected harness gets a skill file and a live memex.[/dim]"
    )

    render_section("Monitor")
    console.print(f"  tail -f {log_path}")
    console.print("  syke status")
