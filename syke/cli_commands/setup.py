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
    from syke.runtime.locator import (
        ensure_syke_launcher,
        resolve_background_syke_runtime,
        resolve_syke_runtime,
    )

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    runtime = resolve_background_syke_runtime() if start_daemon_after else resolve_syke_runtime()
    launcher = ensure_syke_launcher(runtime)
    cmd = [str(launcher), "--user", user_id, "sync"]
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
            cwd=str(runtime.working_directory) if runtime.working_directory else os.getcwd(),
        )
    return LOG_PATH


def _run_agent_setup(
    user_id: str, cli_provider: str | None, skip_daemon: bool
) -> dict[str, object]:
    """Non-interactive agent setup. Returns structured JSON result."""
    from syke.cli_support.exit_codes import SykeRuntimeException

    try:
        inspect_info = build_setup_inspect_payload(user_id=user_id, cli_provider=cli_provider)
    except Exception as exc:
        return {"status": "failed", "error": str(exc), "exit_code": 1}

    # Check Pi runtime (suppress console output)
    import logging as _logging

    syke_logger = _logging.getLogger("syke")
    for h in syke_logger.handlers:
        if isinstance(h, _logging.StreamHandler) and not isinstance(h, _logging.FileHandler):
            h.setLevel(_logging.CRITICAL)
    try:
        from syke.llm.pi_client import ensure_pi_binary, get_pi_version

        ensure_pi_binary()
        get_pi_version(install=False)
    except (SykeRuntimeException, Exception) as exc:
        return {
            "status": "needs_runtime",
            "error": str(exc),
            "next_steps": ["Install Node.js >= 18, then: syke setup --agent"],
            "exit_code": 1,
        }

    # Check provider
    provider = cast(dict[str, object], inspect_info["provider"])
    if not provider.get("configured"):
        detected = [
            {
                "source": cast(str, s["source"]),
                "files": cast(int, s["files_found"]),
                "format": cast(str, s.get("format_cluster", "")),
            }
            for s in cast(list[dict[str, object]], inspect_info.get("sources") or [])
            if s.get("detected")
        ]
        return {
            "status": "needs_provider",
            "user": user_id,
            "detected_sources": detected,
            "instructions": (
                "Syke needs an LLM provider to synthesize memory. "
                "Ask the user which provider they use and get their API key. "
                "Then run: syke auth set <provider> --api-key <KEY> --use\n"
                "Common providers: anthropic, openai, azure-openai-responses, "
                "kimi-coding, openrouter.\n"
                "For Azure, also pass: --base-url https://<resource>.openai.azure.com/openai/v1 "
                "--model <model>\n"
                "After auth is configured, run: syke setup --agent"
            ),
            "next_steps": [
                "syke auth set <provider> --api-key <KEY> --use",
                "syke setup --agent",
            ],
            "exit_code": 0,
        }

    provider_id = cast(str, provider.get("id"))
    model_id = cast(str, provider.get("model", ""))

    # Verify provider connection
    try:
        handshake = verify_setup_provider_connection(provider_id, model_id)
    except Exception as exc:
        return {
            "status": "failed",
            "error": f"Provider verification failed: {exc}",
            "next_steps": [
                "syke auth status --json",
                "syke setup --agent",
            ],
            "exit_code": 1,
        }

    # Handle managed install (macOS source checkout)
    daemon_after = not skip_daemon
    daemon_info = cast(dict[str, object], inspect_info["daemon"])
    if (
        daemon_after
        and not daemon_info.get("installable")
        and daemon_info.get("platform") == "Darwin"
        and _is_source_install()
    ):
        try:
            run_managed_checkout_install(
                user_id=user_id, installer="auto", restart_daemon=False, prompt=False
            )
            daemon_info = setup_daemon_viability_payload()
        except Exception:
            pass  # Non-fatal — setup continues without daemon

    # Select all detected sources
    selected = [
        cast(str, s["source"])
        for s in cast(list[dict[str, object]], inspect_info.get("sources") or [])
        if s.get("detected")
    ]

    # Launch background onboarding
    log_path = _launch_background_onboarding(
        user_id=user_id,
        selected_sources=selected,
        start_daemon_after=daemon_after and bool(daemon_info.get("installable")),
    )

    total_files = sum(
        cast(int, s.get("files_found", 0))
        for s in cast(list[dict[str, object]], inspect_info.get("sources") or [])
        if s.get("detected") and cast(str, s["source"]) in selected
    )

    est = max(2, total_files // 1500 + 3)
    return {
        "status": "complete",
        "user": user_id,
        "provider": {"id": provider_id, "model": model_id},
        "handshake": handshake,
        "sources_ingesting": selected,
        "total_files": total_files,
        "estimated_minutes": est,
        "daemon": "started" if daemon_after and daemon_info.get("installable") else "skipped",
        "monitor": str(log_path),
        "instructions": (
            "Setup is complete. Background ingestion and synthesis are running now. "
            f"This takes about {est} minutes. "
            "The user can start using syke ask and syke record immediately — "
            "answers improve as ingestion completes. "
            "Do NOT run syke setup again. "
            "Check progress with: syke status --json"
        ),
        "next_steps": [
            'syke ask "what am I working on?"',
            "syke status --json",
        ],
        "exit_code": 0,
    }


@click.command(
    short_help="Review and apply local memory setup.",
    help=(
        "Inspect current setup state, then apply the approved local memory plan.\n\n"
        "Agents: use --agent for non-interactive JSON setup. "
        "If the response says needs_provider, run "
        "'syke auth set <provider> --api-key <KEY> --use' first, then retry."
    ),
)
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
    "--agent",
    "agent_mode",
    is_flag=True,
    help="Non-interactive agent mode. Returns JSON, acts on current state.",
)
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
    agent_mode: bool,
    selected_sources_cli: tuple[str, ...],
) -> None:
    """Inspect current setup state, then apply the approved local memory plan."""
    from syke.llm.env import resolve_provider

    user_id = ctx.obj["user"]
    if agent_mode:
        result = _run_agent_setup(user_id, ctx.obj.get("provider"), skip_daemon)
        click.echo(json.dumps(result, indent=2))
        ctx.exit(result.get("exit_code", 0))
        return

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
    if not provider_id or not model_id:
        raise SykeAuthException("Setup requires a provider and model before ingest can begin.")
    if not interactive_provider_selected:
        handshake = run_setup_stage(
            f"Verifying {provider_id}/{model_id}…",
            lambda: verify_setup_provider_connection(provider_id, model_id),
        )
        console.print(f"  [green]✓[/green] {provider_id}/{model_id} connected")
        if handshake:
            console.print(f"    [dim]{handshake}[/dim]")

    daemon_after_onboarding = not skip_daemon
    daemon_info = cast(dict[str, object], inspect_info["daemon"])
    if (
        daemon_after_onboarding
        and not daemon_info.get("installable")
        and daemon_info.get("platform") == "Darwin"
        and _is_source_install()
    ):
        if yes or click.confirm(
            "\nInstall a background-safe build? (standard for dev checkouts on macOS)",
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

    import time

    from syke.cli_support.render import SetupStatus

    log_path = _launch_background_onboarding(
        user_id=user_id,
        selected_sources=selected_sources,
        start_daemon_after=daemon_after_onboarding,
    )

    with SetupStatus("Activating"):
        time.sleep(1.5)

    console.print("\n[bold green]✓ Setup complete[/bold green]\n")

    render_section("Ready now")
    console.print('  syke ask "what am I working on?"')
    console.print('  syke record "TODO: finish the API endpoint"')
    console.print('  syke ask "what are my open TODOs this week?"')

    render_section("Building in background")
    source_inventory = {
        cast(str, s["source"]): s
        for s in cast(list[dict[str, object]], inspect_info.get("sources") or [])
    }
    total_files = 0
    for src in selected_sources:
        inv = source_inventory.get(src, {})
        files = cast(int, inv.get("files_found", 0))
        fmt = cast(str, inv.get("format_cluster", ""))
        unit = "db" if fmt == "sqlite" else "files"
        console.print(f"  [dim]…[/dim] {src}  {files:,} {unit}")
        total_files += files
    console.print("  [dim]…[/dim] synthesizing your first memex")
    console.print("  [dim]…[/dim] installing skill files to detected harnesses")
    if daemon_after_onboarding:
        console.print("  [dim]…[/dim] background sync starts after onboarding")
    est_minutes = max(2, total_files // 1500 + 3)
    console.print(f"\n  [dim]Estimated: ~{est_minutes} minutes[/dim]")

    render_section("What happens next")
    console.print("  Syke watches your agent sessions across harnesses and builds")
    console.print("  a living context called MEMEX — a map of your current work.")
    console.print("  Every connected harness gets a skill file and a live memex.")
    console.print()
    console.print("  [dim]Your agents already know how to use Syke via the skill file.[/dim]")
    console.print("  [dim]You can also use it directly:[/dim]")
    console.print()
    console.print('    syke ask "…"       [dim]deep recall across all sessions[/dim]')
    console.print('    syke record "…"    [dim]save notes, decisions, TODOs[/dim]')
    console.print("    syke context       [dim]read the current memex[/dim]")
    console.print("    syke status        [dim]check what's connected[/dim]")

    render_section("Monitor")
    console.print(f"  tail -f {log_path}")
    console.print("  syke status")

    console.print()
    console.print("[dim]Try it now:[/dim]")
    console.print('  syke ask "what are my open threads?"')

    if sys.stdin.isatty() and not yes:
        click.pause("\nPress any key to close setup.")
