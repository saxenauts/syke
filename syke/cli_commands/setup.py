"""Setup command extracted from the monolithic CLI."""

from __future__ import annotations

import json
import sys
from typing import cast

import click
from rich.console import Console

from syke.cli_support.auth_flow import (
    ensure_setup_pi_runtime,
    run_interactive_provider_flow,
    verify_setup_provider_connection,
)
from syke.cli_support.context import get_db, observe_registry
from syke.cli_support.daemon_state import wait_for_daemon_startup
from syke.cli_support.exit_codes import SykeAuthException, SykeDataException
from syke.cli_support.providers import provider_payload, render_provider_summary
from syke.cli_support.render import (
    SetupStatus,
    render_section,
    render_setup_line,
    render_setup_source_result,
)
from syke.cli_support.setup_support import (
    build_setup_inspect_payload,
    choose_setup_sources_interactive,
    render_setup_inspect_summary,
    run_setup_stage,
)
from syke.config import _is_source_install

console = Console()


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

    console.print(f"\n[bold]Syke Setup[/bold] — user: [cyan]{user_id}[/cyan]")

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
        console.print("\n[dim]Inspection only. No changes made.[/dim]")
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

    render_section("Step 1 · Sources")
    if selected_sources:
        render_setup_line("selected", ", ".join(selected_sources))
        skipped_sources = [source for source in detected_sources if source not in selected_sources]
        if skipped_sources:
            render_setup_line("skipped", ", ".join(skipped_sources))
    elif detected_sources:
        render_setup_line("selected", "none")
        render_setup_line("skipped", ", ".join(detected_sources))
    else:
        render_setup_line("selected", "none detected")

    render_section("Step 2 · Pi agent runtime")
    run_setup_stage("Checking Pi runtime...", ensure_setup_pi_runtime)

    render_section("Step 3 · Provider")
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
        console.print(
            "  [green]✓[/green]  Keeping active provider:"
            f" [bold]{cast(dict[str, object], inspect_info['provider'])['id']}[/bold]"
        )
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
        render_provider_summary(provider_info, indent="  ")

    provider_id = cast(str | None, provider_info.get("id"))
    model_id = cast(str | None, provider_info.get("model"))
    if not provider_id or not model_id:
        raise SykeAuthException("Setup requires a provider and model before ingest can begin.")
    render_section("Step 3b · Verify provider connection")
    run_setup_stage(
        f"Checking {provider_id}/{model_id}...",
        lambda: verify_setup_provider_connection(provider_id, model_id),
    )

    render_section("Step 4 · Connect Sources")
    db = get_db(user_id)

    try:
        existing_total_before = db.count_events(user_id)
        had_memex_before = bool(db.get_memex(user_id))
        ingested_count = 0
        synthesis_started = False
        synthesis_ready_now = False

        def _source_msg(name: str, source_key: str, new_count: int, unit: str = "events") -> None:
            existing = db.count_events(user_id, source=source_key)
            if new_count > 0:
                render_setup_source_result(
                    name,
                    "ingested",
                    f"+{new_count} new {unit}, {existing} total",
                )
            elif existing > 0:
                render_setup_source_result(name, "ingested", f"up to date, {existing} {unit}")
            else:
                render_setup_source_result(name, "ingested", f"{new_count} {unit}")

        from syke.metrics import MetricsTracker
        from syke.observe.bootstrap import ensure_adapters

        _bootstrap_results = run_setup_stage(
            "Connecting selected sources...",
            lambda: ensure_adapters(user_id, sources=selected_sources or None),
        )
        _ingestible_sources = {
            _result.source
            for _result in _bootstrap_results
            if _result.status in {"existing", "generated"}
        }
        failed_bootstraps = [
            _result
            for _result in _bootstrap_results
            if _result.source in detected_sources and _result.status == "failed"
        ]
        bootstrap_by_source = {result.source: result for result in _bootstrap_results}
        if _bootstrap_results:
            render_section("Source Results")
        for _bootstrap in _bootstrap_results:
            status_label = {
                "existing": "connected",
                "generated": "connected",
                "skipped": "skipped",
                "failed": "failed",
            }.get(_bootstrap.status, _bootstrap.status)
            render_setup_source_result(_bootstrap.source, status_label, _bootstrap.detail)

        if (
            selected_sources
            and not _ingestible_sources
            and failed_bootstraps
            and existing_total_before == 0
        ):
            raise SykeDataException(
                "Setup could not bootstrap any selected sources. Fix the warnings above and rerun."
            )

        setup_registry = observe_registry(user_id)
        with SetupStatus("Ingesting selected sources...") as ingest_progress:
            for _desc in setup_registry.active_harnesses():
                _src = _desc.source
                if _src not in _ingestible_sources:
                    continue
                _adapter = setup_registry.get_adapter(_src, db, user_id)
                if _adapter is None:
                    continue
                ingest_progress.update(f"{_src} · ingesting events")
                render_setup_source_result(_src, "ingesting", "reading source events")
                try:
                    tracker = MetricsTracker(user_id)
                    with tracker.track(f"ingest_{_src}") as metrics:
                        _result = _adapter.ingest()
                        metrics.events_processed = _result.events_count
                    ingest_progress.update(f"{_src} · +{_result.events_count} events")
                    _source_msg(_src, _src, _result.events_count, "events")
                    ingested_count += _result.events_count
                except Exception as e:
                    console.print(f"  [yellow]WARN[/yellow]  {_src}: {e}")

        total_in_db = db.count_events(user_id)
        if total_in_db == 0 and ingested_count == 0:
            console.print("[yellow]No data sources found to ingest.[/yellow]")

        if has_provider and (ingested_count > 0 or (not had_memex_before and total_in_db > 0)):
            render_section("Step 5 · Initial Synthesis")
            try:
                from syke.llm.backends.pi_synthesis import pi_synthesize

                synthesis_started = True
                with SetupStatus("Running initial synthesis...") as synthesis_progress:
                    synthesis_progress.update("preparing workspace")
                    synthesis_result = pi_synthesize(
                        db,
                        user_id,
                        force=True,
                        first_run=not had_memex_before,
                        progress=synthesis_progress.update,
                    )
                if synthesis_result.get("status") == "completed":
                    memex_updated = bool(synthesis_result.get("memex_updated"))
                    turns = synthesis_result.get("num_turns") or 0
                    console.print(
                        "  [green]OK[/green]  Initial synthesis complete"
                        f" ({turns} turn{'s' if turns != 1 else ''})"
                    )
                    if memex_updated:
                        synthesis_ready_now = True
                else:
                    reason = (
                        synthesis_result.get("error")
                        or synthesis_result.get("reason")
                        or synthesis_result.get("status")
                    )
                    console.print(
                        "  [yellow]WARN[/yellow]  Initial synthesis did not complete:"
                        f" {reason}"
                    )
                    console.print("  [dim]Background sync will retry.[/dim]")
            except Exception as e:
                console.print(f"  [yellow]WARN[/yellow]  Initial synthesis failed: {e}")
                console.print("  [dim]Background sync will retry.[/dim]")

        render_section("Step 6 · Distribution")
        try:
            from syke.distribution import refresh_distribution

            distribution_result = run_setup_stage(
                "Refreshing downstream capability surfaces...",
                lambda: refresh_distribution(db, user_id),
            )
            for key, status, detail in distribution_result.status_lines():
                render_setup_line(key, status, detail=detail)
        except Exception as e:
            console.print(f"  [yellow]WARN[/yellow]  Distribution refresh failed: {e}")

        daemon_started = False
        daemon_info = cast(dict[str, object], inspect_info["daemon"])
        if (
            not skip_daemon
            and not daemon_info.get("installable")
            and daemon_info.get("platform") == "Darwin"
            and _is_source_install()
        ):
            if yes or click.confirm(
                "\nThis checkout is not launchd-safe on macOS. Install a managed tool build "
                "for this checkout so background sync can run?",
                default=True,
            ):
                from syke.cli import _run_managed_checkout_install

                try:
                    run_setup_stage(
                        "Installing launchd-safe managed build...",
                        lambda: _run_managed_checkout_install(
                            user_id=user_id,
                            installer="auto",
                            restart_daemon=False,
                            prompt=False,
                        ),
                    )
                    from syke.cli_support.setup_support import setup_daemon_viability_payload

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
            and not skip_daemon
            and daemon_info.get("installable")
            and not daemon_info.get("running")
            and not click.confirm("\nEnable background sync after setup?", default=True)
        ):
            skip_daemon = True
            console.print("  [dim]Skipping background sync for now.[/dim]")

        if not skip_daemon:
            render_section("Step 7 · Background Sync")
            try:
                from syke.daemon.daemon import install_and_start, is_running

                if not daemon_info.get("installable") and not daemon_info.get("running"):
                    raise click.ClickException(
                        cast(
                            str,
                            daemon_info.get("detail")
                            or "Background sync is not installable on this machine.",
                        )
                    )
                running, pid = is_running()
                if running:
                    render_setup_line("daemon", "running", detail=f"PID {pid}")
                    daemon_started = True
                else:
                    run_setup_stage(
                        "Enabling background sync...",
                        lambda: install_and_start(user_id, interval=900),
                    )
                    readiness = wait_for_daemon_startup(user_id)
                    ipc = cast(dict[str, object], readiness["ipc"])
                    if readiness.get("running") and ipc.get("ok"):
                        daemon_started = True
                        render_setup_line("daemon", "enabled", detail="syncs every 15 minutes")
                    elif readiness.get("running"):
                        daemon_started = True
                        render_setup_line(
                            "daemon",
                            "starting",
                            detail=cast(
                                str,
                                ipc.get("detail")
                                or "daemon process is up; warm ask is not ready yet",
                            ),
                        )
                    else:
                        render_setup_line(
                            "daemon",
                            "registered",
                            detail="background service registered; health not confirmed yet",
                        )
            except Exception as e:
                render_setup_line("daemon", "failed", detail=str(e))
                console.print("  [dim]Manual start: syke daemon start[/dim]")

        console.print("\n[bold green]Setup Complete[/bold green]")
        render_setup_line("provider", provider_id or "(none)")
        render_setup_line("model", model_id or "(none)")
        render_setup_line(
            "sources selected",
            ", ".join(selected_sources) if selected_sources else "none",
        )
        render_setup_line("events", f"{total_in_db} total", detail=f"+{ingested_count} new")
        if selected_sources:
            render_section("Connected Sources")
            for source in selected_sources:
                bootstrap = bootstrap_by_source.get(source)
                if bootstrap is None:
                    render_setup_source_result(source, "not run")
                    continue
                status_label = {
                    "existing": "connected",
                    "generated": "connected",
                    "skipped": "skipped",
                    "failed": "failed",
                }.get(bootstrap.status, bootstrap.status)
                render_setup_source_result(source, status_label, bootstrap.detail)
        if synthesis_ready_now:
            render_setup_line("synthesis", "completed", detail="memex ready now")
        elif synthesis_started:
            render_setup_line("synthesis", "retrying", detail="background sync will continue")
        elif has_provider:
            render_setup_line("synthesis", "pending", detail="run syke sync anytime")
        else:
            render_setup_line("synthesis", "blocked", detail="configure a provider first")
        if daemon_started or daemon_info.get("running"):
            render_setup_line("background sync", "enabled")
        elif skip_daemon:
            render_setup_line("background sync", "skipped")
        else:
            render_setup_line("background sync", "not enabled")

        console.print()
        next_commands = ["syke doctor", 'syke ask "..."', "syke context"]
        if daemon_started or daemon_info.get("running"):
            next_commands.append("syke daemon status")
        console.print(f"[dim]Next: {', '.join(next_commands)}[/dim]")
    finally:
        db.close()
