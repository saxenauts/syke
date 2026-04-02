"""Click CLI for Syke."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from syke import __version__
from syke.cli_commands.ask import ask as _ask_cmd
from syke.cli_commands.daemon import daemon_run as _daemon_run_cmd
from syke.cli_commands.daemon import daemon_start as _daemon_start_cmd
from syke.cli_commands.daemon import daemon_status_cmd as _daemon_status_cmd
from syke.cli_commands.daemon import daemon_stop as _daemon_stop_cmd
from syke.cli_commands.daemon import logs as _daemon_logs_cmd
from syke.cli_commands.daemon import self_update as _self_update_cmd
from syke.cli_commands.maintenance import sync as _sync_cmd
from syke.cli_commands.record import record as _record_cmd
from syke.cli_commands.setup import setup as _setup_cmd
from syke.cli_commands.status import (
    connect as _connect_cmd,
)
from syke.cli_commands.status import context as _context_cmd
from syke.cli_commands.status import doctor as _doctor_cmd
from syke.cli_commands.status import observe as _observe_cmd
from syke.cli_commands.status import (
    status as _status_cmd,
)
from syke.config import (
    DEFAULT_USER,
    PROJECT_ROOT,
    _is_source_install,
    user_events_db_path,
    user_syke_db_path,
)
from syke.db import SykeDB
from syke.llm.backends import AskEvent
from syke.llm.env import evaluate_provider_readiness

console = Console()

PRIMARY_COMMANDS = (
    "setup",
    "ask",
    "context",
    "record",
    "status",
    "sync",
    "auth",
    "doctor",
)

ADVANCED_COMMANDS = (
    "daemon",
    "config",
    "connect",
    "cost",
    "observe",
    "self-update",
    "install-current",
)

ASK_RESULT_OPTIONAL_FIELDS = (
    "transport",
    "ipc_fallback",
    "ipc_error",
    "ipc_attempt_ms",
    "daemon_pid",
    "ipc_roundtrip_ms",
    "ipc_socket_path",
)


@dataclass(frozen=True)
class _FlowChoice:
    status: str
    value: str | None = None


class SykeGroup(click.Group):
    """Top-level CLI group with product-oriented help sections."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        available = list(super().list_commands(ctx))
        ordered: list[str] = []
        for name in (*PRIMARY_COMMANDS, *ADVANCED_COMMANDS):
            if name in available and name not in ordered:
                ordered.append(name)
        for name in available:
            if name not in ordered:
                ordered.append(name)
        return ordered

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        commands: dict[str, click.Command] = {}
        for subcommand in self.list_commands(ctx):
            cmd = self.get_command(ctx, subcommand)
            if cmd is None or cmd.hidden:
                continue
            commands[subcommand] = cmd

        if not commands:
            return

        def _rows(names: tuple[str, ...]) -> list[tuple[str, str]]:
            rows: list[tuple[str, str]] = []
            for name in names:
                cmd = commands.get(name)
                if cmd is None:
                    continue
                rows.append((name, cmd.get_short_help_str(formatter.width) or ""))
            return rows

        primary_rows = _rows(PRIMARY_COMMANDS)
        advanced_rows = _rows(ADVANCED_COMMANDS)
        other_rows = [
            (name, cmd.get_short_help_str(formatter.width) or "")
            for name, cmd in commands.items()
            if name not in PRIMARY_COMMANDS and name not in ADVANCED_COMMANDS
        ]

        for title, rows in (
            ("Primary Commands", primary_rows),
            ("Advanced Commands", advanced_rows),
            ("Other Commands", other_rows),
        ):
            if not rows:
                continue
            with formatter.section(title):
                formatter.write_dl(rows)


@dataclass
class _JsonlAskEventCoalescer:
    emit_line: Callable[[dict[str, object]], None]
    pending_type: str | None = None
    pending_parts: list[str] = field(default_factory=list)

    def push(self, event: AskEvent) -> None:
        if event.type in {"thinking", "text"}:
            if self.pending_type == event.type:
                self.pending_parts.append(event.content)
                return
            self.flush()
            self.pending_type = event.type
            self.pending_parts = [event.content]
            return

        self.flush()
        self.emit_line(
            {
                "type": event.type,
                "content": event.content,
                "metadata": event.metadata,
            }
        )

    def flush(self) -> None:
        if self.pending_type is None:
            return
        content = "".join(self.pending_parts)
        if content:
            self.emit_line(
                {
                    "type": self.pending_type,
                    "content": content,
                    "metadata": None,
                }
            )
        self.pending_type = None
        self.pending_parts.clear()


def _build_ask_result_payload(
    *,
    question: str,
    answer: str | None,
    provider: str,
    metadata: dict[str, object] | None,
    ok: bool,
    error: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": ok,
        "question": question,
        "answer": answer,
        "provider": provider,
        "duration_ms": metadata.get("duration_ms") if isinstance(metadata, dict) else None,
        "cost_usd": metadata.get("cost_usd") if isinstance(metadata, dict) else None,
        "input_tokens": metadata.get("input_tokens") if isinstance(metadata, dict) else None,
        "output_tokens": metadata.get("output_tokens") if isinstance(metadata, dict) else None,
        "tool_calls": metadata.get("tool_calls") if isinstance(metadata, dict) else None,
        "error": error
        if error is not None
        else metadata.get("error")
        if isinstance(metadata, dict)
        else None,
    }
    if isinstance(metadata, dict):
        for key in ASK_RESULT_OPTIONAL_FIELDS:
            if key in metadata:
                payload[key] = metadata.get(key)
    return payload


def get_db(user_id: str) -> SykeDB:
    """Get an initialized DB for a user."""
    return SykeDB(user_syke_db_path(user_id), event_db_path=user_events_db_path(user_id))


def _observe_registry(user_id: str):
    from syke.config import user_data_dir
    from syke.observe.registry import HarnessRegistry

    return HarnessRegistry(dynamic_adapters_dir=user_data_dir(user_id) / "adapters")


@click.group(
    cls=SykeGroup,
    invoke_without_command=True,
    epilog='\b\nExamples:\n  syke setup\n  syke ask "What changed this week?"\n  syke context',
)
@click.option("--user", "-u", default=DEFAULT_USER, help="User ID")
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
@click.option("--provider", "-p", default=None, help="Override LLM provider for this invocation")
@click.version_option(__version__)
@click.pass_context
def cli(ctx: click.Context, user: str, verbose: bool, provider: str | None) -> None:
    """Syke — Local memory for your AI tools."""
    ctx.ensure_object(dict)
    ctx.obj["user"] = user
    ctx.obj["verbose"] = verbose
    ctx.obj["provider"] = provider

    if provider:
        os.environ["SYKE_PROVIDER"] = provider

    from syke.metrics import setup_logging

    setup_logging(user, verbose=verbose)

    if ctx.invoked_subcommand is None:
        _show_dashboard(ctx.obj["user"])


def _provider_payload(cli_provider: str | None = None) -> dict[str, object]:
    from syke.llm.env import resolve_provider

    try:
        provider = resolve_provider(cli_provider=cli_provider)
        return _describe_provider(provider.id, selection_source=_resolve_source(cli_provider))
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


def _describe_provider(
    provider_id: str, *, selection_source: str | None = None
) -> dict[str, object]:
    """Return a human- and machine-readable provider summary without secrets."""
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
        auth_source = "Pi agent auth/config"
        auth_configured = True
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
        if _provider_endpoint_configured(provider_id):
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


def _render_provider_summary(provider_info: dict[str, object], *, indent: str = "") -> None:
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


def _render_setup_line(
    label: str,
    value: str,
    *,
    detail: str | None = None,
    indent: str = "  ",
) -> None:
    suffix = f" [dim]({detail})[/dim]" if detail else ""
    console.print(f"{indent}{label}: {value}{suffix}")


def _render_setup_source_result(source: str, status: str, detail: str | None = None) -> None:
    _render_setup_line(source, status, detail=detail)


def _redact_secret(value: str) -> str:
    if not value:
        return "***"
    return f"*** ({len(value)} chars)"


def _run_setup_stage(label: str, fn):
    with console.status(f"[bold]{label}[/bold]", spinner="dots"):
        return fn()


def _render_section(title: str) -> None:
    console.print(f"\n[bold]{title}[/bold]")


def _daemon_payload() -> dict[str, object]:
    import platform

    from syke.daemon.daemon import is_running, launchd_metadata

    running, pid = is_running()
    payload: dict[str, object] = {
        "running": False,
        "registered": False,
        "pid": pid,
        "detail": "not running",
    }

    if platform.system() == "Darwin":
        launchd = launchd_metadata()
        if launchd.get("registered"):
            payload["registered"] = True
            payload["stale"] = bool(launchd.get("stale"))
            payload["stale_reasons"] = cast(list[str], launchd.get("stale_reasons") or [])
            payload["last_exit_status"] = launchd.get("last_exit_status")
            payload["launcher_path"] = launchd.get("program_path")
            if running and pid is not None:
                payload["running"] = True
                payload["detail"] = f"launchd registered, PID {pid}"
            elif launchd.get("stale"):
                payload["detail"] = "launchd stale: " + "; ".join(
                    cast(list[str], launchd.get("stale_reasons") or [])
                )
            else:
                exit_status = launchd.get("last_exit_status")
                if exit_status is None:
                    exit_status = "?"
                payload["detail"] = f"launchd registered (last exit: {exit_status})"
            return payload

    if running and pid is not None:
        payload["running"] = True
        payload["detail"] = f"PID {pid}"
    return payload


def _trust_payload(user_id: str) -> dict[str, list[dict[str, str]]]:
    import platform

    from syke.config import CODEX_GLOBAL_AGENTS, SKILLS_DIRS, user_data_dir
    from syke.daemon.daemon import LOG_PATH, PLIST_PATH
    from syke.pi_state import (
        get_pi_agent_dir,
        get_pi_auth_path,
        get_pi_models_path,
        get_pi_settings_path,
    )

    sources: list[dict[str, str]] = []
    registry = _observe_registry(user_id)
    for desc in registry.active_harnesses():
        if desc.discover is None:
            continue
        for root in desc.discover.roots:
            sources.append(
                {
                    "source": desc.source,
                    "path": str(Path(root.path).expanduser()),
                }
            )

    targets: list[dict[str, str]] = [
        {"kind": "user_data", "path": str(user_data_dir(user_id))},
        {"kind": "workspace", "path": str(Path.home() / ".syke" / "workspace")},
        {"kind": "pi_agent_dir", "path": str(get_pi_agent_dir())},
        {"kind": "pi_auth", "path": str(get_pi_auth_path())},
        {"kind": "pi_settings", "path": str(get_pi_settings_path())},
        {"kind": "pi_models", "path": str(get_pi_models_path())},
        {"kind": "launcher", "path": str(Path.home() / ".syke" / "bin" / "syke")},
        {"kind": "daemon_log", "path": str(LOG_PATH)},
        {"kind": "memex_export", "path": str(user_data_dir(user_id) / "MEMEX.md")},
        {"kind": "memex_include", "path": str(Path.home() / ".claude" / "CLAUDE.md")},
        {"kind": "codex_agents", "path": str(CODEX_GLOBAL_AGENTS)},
    ]
    targets.extend({"kind": "skill_dir", "path": str(path)} for path in SKILLS_DIRS)

    if platform.system() == "Darwin":
        targets.append({"kind": "launch_agent", "path": str(PLIST_PATH)})
    else:
        targets.append({"kind": "cron", "path": "user crontab"})

    return {"sources": sources, "targets": targets}


def _setup_source_inventory(user_id: str) -> list[dict[str, object]]:
    from datetime import UTC, datetime

    sources: list[dict[str, object]] = []
    registry = _observe_registry(user_id)

    for desc in registry.active_harnesses():
        files_found = 0
        detected_paths: list[str] = []
        roots: list[str] = []
        latest_mtime: float | None = None
        if desc.discover is not None:
            for root in desc.discover.roots:
                base = Path(root.path).expanduser()
                roots.append(str(base))
                if not base.exists():
                    continue
                patterns = root.include or ["**/*"]
                for pattern in patterns:
                    try:
                        for match in base.glob(pattern):
                            files_found += 1
                            try:
                                mtime = match.stat().st_mtime
                            except OSError:
                                mtime = None
                            if mtime is not None and (latest_mtime is None or mtime > latest_mtime):
                                latest_mtime = mtime
                            if len(detected_paths) < 3:
                                detected_paths.append(str(match))
                    except OSError:
                        continue

        sources.append(
            {
                "source": desc.source,
                "format_cluster": desc.format_cluster,
                "roots": roots,
                "files_found": files_found,
                "detected": files_found > 0,
                "sample_paths": detected_paths,
                "latest_mtime": latest_mtime,
                "latest_seen": datetime.fromtimestamp(latest_mtime, UTC).isoformat()
                if latest_mtime is not None
                else None,
            }
        )

    sources.sort(
        key=lambda item: (
            not bool(item["detected"]),
            -(item["latest_mtime"] or 0.0),
            cast(str, item["source"]),
        )
    )
    return sources


def _setup_provider_choices() -> list[dict[str, object]]:
    from syke.llm.pi_client import get_pi_provider_catalog
    from syke.pi_state import get_default_provider

    active_provider = get_default_provider()
    choices: list[dict[str, object]] = []
    for entry in get_pi_provider_catalog():
        readiness = evaluate_provider_readiness(entry.id)
        label = entry.oauth_name or entry.id
        choices.append(
            {
                "id": entry.id,
                "label": label,
                "ready": readiness.ready,
                "detail": readiness.detail,
                "active": entry.id == active_provider,
                "oauth": entry.oauth,
                "default_model": entry.default_model,
                "models": list(entry.models),
            }
        )
    return choices


def _setup_runtime_payload() -> dict[str, object]:
    from syke.llm.pi_client import PI_BIN, get_pi_version

    payload: dict[str, object] = {
        "launcher": str(PI_BIN),
        "installed": PI_BIN.exists(),
        "ready": False,
        "version": None,
        "detail": None,
    }

    try:
        payload["version"] = get_pi_version(install=False)
        payload["ready"] = True
        payload["detail"] = "Pi runtime available"
    except (RuntimeError, FileNotFoundError) as exc:
        payload["detail"] = str(exc)

    return payload


def _setup_target_payload(
    *,
    user_id: str,
    cli_provider: str | None,
    provider: dict[str, object],
    daemon: dict[str, object],
) -> list[dict[str, str]]:
    from syke.config import user_data_dir
    from syke.daemon.daemon import LOG_PATH, PLIST_PATH
    from syke.llm.pi_client import PI_BIN
    from syke.pi_state import (
        get_pi_agent_dir,
        get_pi_auth_path,
        get_pi_models_path,
        get_pi_settings_path,
    )
    from syke.runtime.workspace import EVENTS_DB, MEMEX_PATH, SYKE_DB, WORKSPACE_ROOT

    targets = [
        {"kind": "user_data", "path": str(user_data_dir(user_id))},
        {"kind": "events_db", "path": str(user_events_db_path(user_id))},
        {"kind": "syke_db", "path": str(user_syke_db_path(user_id))},
        {"kind": "source_readers_dir", "path": str(user_data_dir(user_id) / "adapters")},
        {"kind": "workspace", "path": str(WORKSPACE_ROOT)},
        {"kind": "workspace_events_db", "path": str(EVENTS_DB)},
        {"kind": "workspace_syke_db", "path": str(SYKE_DB)},
        {"kind": "workspace_memex", "path": str(MEMEX_PATH)},
        {"kind": "pi_launcher", "path": str(PI_BIN)},
        {"kind": "pi_agent_dir", "path": str(get_pi_agent_dir())},
        {"kind": "pi_auth", "path": str(get_pi_auth_path())},
        {"kind": "pi_settings", "path": str(get_pi_settings_path())},
        {"kind": "pi_models", "path": str(get_pi_models_path())},
    ]

    if cli_provider is None and not provider.get("configured"):
        targets.append({"kind": "pi_auth", "path": str(get_pi_auth_path())})
        targets.append({"kind": "pi_settings", "path": str(get_pi_settings_path())})

    if daemon.get("installable") and not daemon.get("running"):
        targets.append({"kind": "daemon_log", "path": str(LOG_PATH)})
        if daemon.get("platform") == "Darwin":
            targets.append({"kind": "launch_agent", "path": str(PLIST_PATH)})
        else:
            targets.append({"kind": "cron", "path": "user crontab"})

    return targets


def _setup_daemon_viability_payload() -> dict[str, object]:
    import platform

    payload = _daemon_payload()
    system = platform.system()
    detail = payload.get("detail")
    installable = True
    remediation: str | None = None

    if system == "Darwin":
        from syke.runtime.locator import resolve_background_syke_runtime

        try:
            runtime = resolve_background_syke_runtime()
            detail = f"launchd-safe runtime: {runtime.target_path or runtime.syke_command[0]}"
        except RuntimeError as exc:
            installable = False
            detail = str(exc)
            remediation = (
                "Run `syke install-current` to create a launchd-safe build, or move/install "
                "Syke outside protected folders. If launchd is stale, run `syke daemon stop` first."
            )
    else:
        if shutil.which("crontab") is None:
            installable = False
            detail = "crontab not found"
            remediation = "Install cron/crontab support or run `syke daemon run` manually."
        else:
            detail = "cron-backed background sync available"

    return {
        "platform": system,
        "running": payload.get("running", False),
        "registered": payload.get("registered", False),
        "installable": installable,
        "detail": detail,
        "remediation": remediation,
    }


def _build_setup_inspect_payload(*, user_id: str, cli_provider: str | None) -> dict[str, object]:
    provider = _provider_payload(cli_provider)
    providers = _setup_provider_choices()
    sources = _setup_source_inventory(user_id)
    trust = _trust_payload(user_id)
    runtime = _setup_runtime_payload()
    daemon = _setup_daemon_viability_payload()
    setup_targets = _setup_target_payload(
        user_id=user_id,
        cli_provider=cli_provider,
        provider=provider,
        daemon=daemon,
    )

    detected_sources = [item["source"] for item in sources if item["detected"]]
    proposed_actions: list[dict[str, object]] = [
        {
            "id": "bootstrap_source_readers",
            "description": "Bootstrap or repair detected source readers before ingest when needed.",
        }
    ]
    consent_points: list[dict[str, object]] = []

    if detected_sources:
        proposed_actions.append(
            {
                "id": "ingest_sources",
                "description": "Ingest detected local sources into the events ledger.",
                "sources": detected_sources,
            }
        )

    proposed_actions.append(
        {
            "id": "initial_synthesis",
            "description": "Run initial synthesis immediately when a provider is ready and setup creates or changes state.",
        }
    )

    if not provider.get("configured"):
        consent_points.append(
            {
                "id": "provider",
                "question": "Choose a provider before synthesis can run.",
                "options": [item["id"] for item in providers],
                "default": None,
            }
        )
    if detected_sources:
        consent_points.append(
            {
                "id": "sources",
                "question": "Choose which detected sources to connect during setup.",
                "options": detected_sources,
                "default": detected_sources,
            }
        )
    if daemon.get("installable") and not daemon.get("running"):
        proposed_actions.append(
            {
                "id": "background_sync",
                "description": "Install background sync so setup stays fresh after the first run.",
            }
        )
        consent_points.append(
            {
                "id": "daemon",
                "question": "Enable background sync after setup?",
                "options": ["yes", "no"],
                "default": "yes",
            }
        )

    return {
        "ok": True,
        "schema_version": 1,
        "mode": "inspect",
        "user": user_id,
        "provider": provider,
        "provider_choices": providers,
        "sources": sources,
        "trust": trust,
        "setup_targets": setup_targets,
        "runtime": runtime,
        "daemon": daemon,
        "proposed_actions": proposed_actions,
        "consent_points": consent_points,
        "next_commands": [
            "syke auth status",
            "syke status --json",
            "syke doctor",
        ],
    }


def _render_setup_inspect_summary(info: dict[str, object]) -> None:
    console.print("\n[bold]Setup plan[/bold]\n")
    _render_provider_summary(cast(dict[str, object], info["provider"]), indent="  ")
    console.print()

    detected_sources = [
        cast(dict[str, object], item)
        for item in cast(list[dict[str, object]], info["sources"])
        if item.get("detected")
    ]
    if detected_sources:
        console.print("  [bold]Detected sources (newest first)[/bold]")
        for item in detected_sources:
            roots = ", ".join(cast(list[str], item["roots"]))
            latest_seen = item.get("latest_seen")
            latest_detail = (
                f"{item['files_found']} files • latest {latest_seen}"
                if isinstance(latest_seen, str) and latest_seen
                else f"{item['files_found']} files"
            )
            _render_setup_line(cast(str, item["source"]), roots, detail=latest_detail, indent="    ")
    else:
        _render_setup_line("sources", "none detected", indent="  ")

    proposed_actions = cast(list[dict[str, object]], info.get("proposed_actions") or [])
    if proposed_actions:
        console.print("\n  [bold]Planned actions[/bold]")
        for action in proposed_actions:
            sources = cast(list[str] | None, action.get("sources"))
            detail = ", ".join(sources) if sources else None
            _render_setup_line(
                cast(str, action["id"]),
                cast(str, action["description"]),
                detail=detail,
                indent="    ",
            )

    daemon = cast(dict[str, object], info["daemon"])
    console.print("\n  [bold]Background sync[/bold]")
    state = "ready" if daemon.get("installable") else "blocked"
    _render_setup_line(
        cast(str, daemon["platform"]),
        state,
        detail=cast(str | None, daemon.get("detail")),
        indent="    ",
    )

    consent_points = cast(list[dict[str, object]], info.get("consent_points") or [])
    if consent_points:
        console.print("\n  [bold]Choices requiring consent[/bold]")
        for item in consent_points:
            default = item.get("default")
            if isinstance(default, list):
                default_suffix = f"default selected: {', '.join(str(value) for value in default)}"
            else:
                default_suffix = f"default: {default}" if default else None
            _render_setup_line(
                cast(str, item["id"]),
                cast(str, item["question"]),
                detail=default_suffix,
                indent="    ",
            )

    console.print("\n  [bold]Planned writes[/bold]")
    console.print("  [dim]Setup only writes these targets after the choices you approve.[/dim]")
    setup_targets = cast(
        list[dict[str, str]],
        info.get("setup_targets")
        or cast(dict[str, object], info.get("trust") or {}).get("targets", []),
    )
    for target in setup_targets:
        _render_setup_line(target["kind"], target["path"], indent="    ")


def _build_status_payload(
    db: SykeDB,
    *,
    user_id: str,
    cli_provider: str | None,
) -> dict[str, object]:
    from syke.daemon.ipc import daemon_ipc_status
    from syke.metrics import runtime_metrics_status
    from syke.observe.trace import self_observation_status

    info = db.get_status(user_id)
    memex = db.get_memex(user_id)
    memory_count = db.count_memories(user_id) if memex else 0
    return {
        "ok": True,
        "user": user_id,
        "initialized": bool(info.get("sources")),
        "provider": _provider_payload(cli_provider),
        "daemon": _daemon_payload(),
        "sources": info.get("sources", {}),
        "total_events": info.get("total_events", 0),
        "latest_event_at": info.get("latest_event_at"),
        "recent_runs": info.get("recent_runs", []),
        "memex": {
            "present": bool(memex),
            "created_at": memex.get("created_at") if memex else None,
            "memory_count": memory_count,
        },
        "runtime_signals": {
            "self_observation": self_observation_status(),
            "daemon_ipc": daemon_ipc_status(user_id),
            **runtime_metrics_status(user_id),
        },
        "trust": _trust_payload(user_id),
    }


@cli.command(short_help="Show provider, daemon, source, and memex status.")
@click.option(
    "--json",
    "use_json",
    is_flag=True,
    help="Output as JSON",
)
@click.pass_context
def status(ctx: click.Context, use_json: bool) -> None:
    """Show provider resolution, source counts, daemon state, and memex status."""
    return _status_cmd.callback(ctx, use_json)


@cli.command()
@click.option("--days", "-d", default=None, type=int, help="Limit to last N days")
@click.option(
    "--json",
    "use_json",
    is_flag=True,
    help="Output as JSON",
)
@click.pass_context
def cost(ctx: click.Context, days: int | None, use_json: bool) -> None:
    """Show cumulative LLM cost and token usage from metrics.jsonl."""
    from datetime import UTC, datetime, timedelta

    from syke.metrics import MetricsTracker

    user_id = ctx.obj["user"]
    tracker = MetricsTracker(user_id)
    runs = tracker._load_all()

    if not runs:
        if use_json:
            click.echo(json.dumps({"total_runs": 0, "total_cost_usd": 0, "runs": []}))
        else:
            console.print("[dim]No metrics recorded yet. Run syke sync or syke ask first.[/dim]")
        return

    # Filter by date if --days specified
    if days is not None:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        runs = [r for r in runs if r.get("started_at", "") >= cutoff]
        if not runs:
            if use_json:
                click.echo(json.dumps({"total_runs": 0, "total_cost_usd": 0, "runs": []}))
            else:
                console.print(f"[dim]No metrics in the last {days} day(s).[/dim]")
            return

    # Aggregate
    total_cost = sum(r.get("cost_usd", 0) for r in runs)
    total_input = sum(r.get("input_tokens", 0) for r in runs)
    total_output = sum(r.get("output_tokens", 0) for r in runs)
    total_thinking = sum(r.get("thinking_tokens", 0) for r in runs)
    total_tokens = total_input + total_output + total_thinking

    by_op: dict[str, dict[str, int | float]] = {}
    for r in runs:
        op = r.get("operation", "unknown")
        if op not in by_op:
            by_op[op] = {"count": 0, "cost_usd": 0.0, "tokens": 0, "errors": 0}
        by_op[op]["count"] += 1
        by_op[op]["cost_usd"] += r.get("cost_usd", 0)
        by_op[op]["tokens"] += (
            r.get("input_tokens", 0) + r.get("output_tokens", 0) + r.get("thinking_tokens", 0)
        )
        if not r.get("success", True):
            by_op[op]["errors"] += 1

    if use_json:
        click.echo(
            json.dumps(
                {
                    "total_runs": len(runs),
                    "total_cost_usd": round(total_cost, 6),
                    "total_tokens": total_tokens,
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "thinking_tokens": total_thinking,
                    "by_operation": by_op,
                },
                indent=2,
            )
        )
        return

    # Header
    period = f"last {days} day(s)" if days else "all time"
    console.print(f"\n[bold]Syke Cost[/bold] — {period}\n")
    console.print(
        f"  Total:  [bold]${total_cost:.4f}[/bold]  ·  {total_tokens:,} tokens  ·  {len(runs)} runs"
    )
    if total_thinking:
        console.print(
            f"  Breakdown:  {total_input:,} in  ·  {total_output:,} out  ·  {total_thinking:,} thinking"
        )
    console.print()

    # By-operation table
    op_table = Table(title="By Operation")
    op_table.add_column("Operation", style="cyan")
    op_table.add_column("Runs", justify="right")
    op_table.add_column("Cost", justify="right", style="green")
    op_table.add_column("Tokens", justify="right")
    op_table.add_column("Errors", justify="right", style="red")

    for op in sorted(by_op, key=lambda k: by_op[k]["cost_usd"], reverse=True):
        d = by_op[op]
        err_str = str(d["errors"]) if d["errors"] else ""
        op_table.add_row(op, str(d["count"]), f"${d['cost_usd']:.4f}", f"{d['tokens']:,}", err_str)

    console.print(op_table)

    # Recent runs (last 10)
    recent = runs[-10:]
    if recent:
        console.print("\n[bold]Recent Runs[/bold]")
        for r in reversed(recent):
            ts = r.get("started_at", "")[:19].replace("T", " ")
            op = r.get("operation", "?")
            usd = r.get("cost_usd", 0)
            tok = r.get("input_tokens", 0) + r.get("output_tokens", 0) + r.get("thinking_tokens", 0)
            dur = r.get("duration_seconds", 0)
            ok = "[green]ok[/green]" if r.get("success", True) else "[red]fail[/red]"
            console.print(f"  {ts}  {ok}  [cyan]{op}[/cyan]  ${usd:.4f}  {tok:,} tok  {dur:.1f}s")
    console.print()


@cli.group(hidden=True)
def ingest() -> None:
    """Ingest data from platforms."""
    pass


@ingest.command("source")
@click.argument("source_name")
@click.option("--yes", "-y", is_flag=True, help="Skip consent prompt")
@click.pass_context
def ingest_source(ctx: click.Context, source_name: str, yes: bool) -> None:
    """Ingest from a registered source (e.g. claude-code, codex, hermes)."""
    from syke.metrics import MetricsTracker

    user_id = ctx.obj["user"]

    if not yes:
        console.print(
            f"\n[bold yellow]This will ingest data from '{source_name}'[/bold yellow]"
            "\nData stays local — never uploaded.\n"
        )
        if not click.confirm("Proceed with ingestion?"):
            console.print("[dim]Cancelled.[/dim]")
            return

    db = get_db(user_id)
    tracker = MetricsTracker(user_id)
    try:
        registry = _observe_registry(user_id)
        adapter = registry.get_adapter(source_name, db, user_id)
        if adapter is None:
            console.print(f"[red]No adapter found for '{source_name}'.[/red]")
            console.print("[dim]Use 'syke connect <path>' to generate one.[/dim]")
            return
        with tracker.track(f"ingest_{source_name}") as metrics:
            result = adapter.ingest()
            metrics.events_processed = result.events_count
        console.print(
            f"[green]{source_name} ingestion complete:[/green] {result.events_count} events"
        )
    finally:
        db.close()


@ingest.command("all")
@click.option("--yes", "-y", is_flag=True, help="Skip consent prompts for private sources")
@click.pass_context
def ingest_all(ctx: click.Context, yes: bool) -> None:
    """Ingest from all available sources via the registry."""
    console.print("[bold]Ingesting from all sources...[/bold]\n")
    user_id = ctx.obj["user"]
    registry = _observe_registry(user_id)
    for desc in registry.active_harnesses():
        try:
            ctx.invoke(ingest_source, source_name=desc.source, yes=yes)
        except (SystemExit, Exception) as e:
            console.print(f"  [yellow]{desc.source} skipped:[/yellow] {e}")
    console.print("\n[bold]All sources processed.[/bold]")


def _detect_install_method() -> str:
    """Detect how syke was installed: 'pipx' | 'pip' | 'uv_tool' | 'uvx' | 'source'."""
    import shutil
    import subprocess

    from syke.runtime.locator import resolve_syke_runtime

    if _is_source_install():
        return "source"

    try:
        runtime = resolve_syke_runtime()
        target = runtime.target_path or Path(runtime.syke_command[0])
    except Exception:
        target = None

    target_str = str(target) if target is not None else ""
    if "/uv/tools/" in target_str:
        return "uv_tool"

    try:
        r = subprocess.run(
            ["uv", "tool", "dir"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if r.returncode == 0 and target is not None:
            tool_dir = Path(r.stdout.strip()).expanduser()
            resolved_target = target.resolve()
            resolved_tool_dir = tool_dir.resolve()
            if resolved_target == resolved_tool_dir or resolved_tool_dir in resolved_target.parents:
                return "uv_tool"
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    try:
        r = subprocess.run(
            ["pipx", "list", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and "syke" in r.stdout:
            return "pipx"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    if shutil.which("syke") is None:
        return "uvx"
    return "pip"


def _daemon_readiness_snapshot(user_id: str) -> dict[str, object]:
    import platform

    from syke.daemon.daemon import cron_is_running, is_running, launchd_metadata
    from syke.daemon.ipc import daemon_ipc_status

    running, pid = is_running()
    snapshot: dict[str, object] = {
        "platform": platform.system(),
        "running": running,
        "pid": pid,
        "ipc": daemon_ipc_status(user_id),
    }

    if snapshot["platform"] == "Darwin":
        snapshot["registered"] = bool(launchd_metadata().get("registered"))
    else:
        registered, _ = cron_is_running()
        snapshot["registered"] = registered

    return snapshot


def _wait_for_daemon_startup(user_id: str, *, timeout_seconds: float = 20.0) -> dict[str, object]:
    import time

    deadline = time.monotonic() + timeout_seconds
    snapshot = _daemon_readiness_snapshot(user_id)
    while time.monotonic() < deadline:
        snapshot = _daemon_readiness_snapshot(user_id)
        if snapshot.get("platform") == "Darwin":
            ipc = cast(dict[str, object], snapshot["ipc"])
            if snapshot.get("running") and ipc.get("ok"):
                break
        elif snapshot.get("registered"):
            break
        time.sleep(0.25)
    return snapshot


def _wait_for_daemon_shutdown(user_id: str, *, timeout_seconds: float = 10.0) -> dict[str, object]:
    import time

    deadline = time.monotonic() + timeout_seconds
    snapshot = _daemon_readiness_snapshot(user_id)
    while time.monotonic() < deadline:
        snapshot = _daemon_readiness_snapshot(user_id)
        if not snapshot.get("running") and not snapshot.get("registered"):
            break
        time.sleep(0.25)
    return snapshot


def _resolve_managed_installer(preferred: str) -> str:
    import shutil

    if preferred != "auto":
        if shutil.which(preferred) is None:
            raise click.ClickException(f"{preferred} is not installed or not on PATH.")
        return preferred

    if shutil.which("uv"):
        return "uv"
    if shutil.which("pipx"):
        return "pipx"
    raise click.ClickException(
        "No managed installer found. Install uv or pipx, then retry this command."
    )


def _run_managed_checkout_install(
    *,
    user_id: str,
    installer: str,
    restart_daemon: bool,
    prompt: bool,
) -> None:
    import subprocess

    from syke.daemon.daemon import install_and_start, is_running, stop_and_unload

    if not _is_source_install():
        raise click.ClickException("This command only works from a source checkout.")

    resolved = _resolve_managed_installer(installer)
    if resolved == "uv":
        cmd = ["uv", "tool", "install", "--force", "--reinstall", "--refresh", "--no-cache", "."]
        summary = "non-editable uv tool build for this checkout"
    else:
        cmd = ["pipx", "install", "--force", "."]
        summary = "non-editable pipx install for this checkout"

    console.print("[bold]Install Current Checkout[/bold]")
    console.print(f"  Checkout:  {PROJECT_ROOT}")
    console.print(f"  Installer: {resolved}")
    console.print(f"  Mode:      {summary}")
    console.print(f"  Command:   {' '.join(cmd)}")
    console.print("  Purpose:   create a launchd-safe managed syke binary for this exact checkout")

    if prompt:
        click.confirm("\nContinue?", abort=True)

    was_running, _ = is_running()
    if was_running and restart_daemon:
        console.print("  Stopping daemon...")
        stop_and_unload()
        stop_snapshot = _wait_for_daemon_shutdown(user_id)
        if stop_snapshot.get("running") or stop_snapshot.get("registered"):
            raise click.ClickException("Daemon did not stop cleanly before reinstall.")

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=False)
    if result.returncode != 0:
        raise click.ClickException("Install failed.")

    console.print("[green]✓[/green] Managed install refreshed.")
    if was_running and restart_daemon:
        console.print("  Restarting daemon...")
        install_and_start(user_id)
        readiness = _wait_for_daemon_startup(user_id)
        ipc = cast(dict[str, object], readiness["ipc"])
        if readiness.get("running") and ipc.get("ok"):
            console.print("[green]✓[/green] Daemon restarted.")
        elif readiness.get("running"):
            raise click.ClickException(
                f"Daemon process restarted, but warm ask is not ready yet: {ipc.get('detail')}"
            )
        raise click.ClickException("Daemon restart did not become healthy after reinstall.")
    elif was_running:
        console.print(
            "[yellow]Daemon still running on the previous process. Restart it to pick up the new build.[/yellow]"
        )


@cli.command("install-current")
@click.option(
    "--installer",
    type=click.Choice(["auto", "uv", "pipx"]),
    default="auto",
    show_default=True,
    help="Managed installer to use for this checkout.",
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.option(
    "--restart-daemon/--no-restart-daemon",
    default=True,
    show_default=True,
    help="Restart the background daemon after installing if it is running.",
)
@click.pass_context
def install_current(ctx: click.Context, installer: str, yes: bool, restart_daemon: bool) -> None:
    """Install this checkout into a managed tool env for background-safe local use."""
    if not _is_source_install():
        raise click.ClickException("`syke install-current` only works from a source checkout.")

    _run_managed_checkout_install(
        user_id=ctx.obj["user"],
        installer=installer,
        restart_daemon=restart_daemon,
        prompt=not yes,
    )


@cli.command(hidden=True)
@click.option("--target", "-t", required=True, type=click.Path(), help="Target directory")
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["memex-md", "user-md"]),
    default="memex-md",
)
@click.pass_context
def inject(ctx: click.Context, target: str, fmt: str) -> None:
    """Inject memex into a target directory."""
    from syke.memory.memex import get_memex_for_injection

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        content = get_memex_for_injection(db, user_id)
        filename = "MEMEX.md" if fmt == "memex-md" else "USER.md"
        target_path = Path(target) / filename
        target_path.write_text(content)
        console.print(f"[green]Memex injected to {target_path}[/green]")
    finally:
        db.close()


@cli.command(short_help="Ask a grounded question over your local memory.")
@click.argument("question")
@click.option("--json", "use_json", is_flag=True, help="Output final result as JSON")
@click.option(
    "--jsonl",
    "use_jsonl",
    is_flag=True,
    help="Stream events and the final result as JSONL",
)
@click.pass_context
def ask(ctx: click.Context, question: str, use_json: bool, use_jsonl: bool) -> None:
    """Ask a grounded question over the local Syke store."""
    return _ask_cmd.callback(ctx, question, use_json, use_jsonl)

    import logging as _logging
    import signal as _signal
    import sys as _sys

    from syke.llm.env import resolve_provider
    from syke.llm.pi_runtime import run_ask

    user_id = ctx.obj["user"]
    db = get_db(user_id)

    if use_json and use_jsonl:
        raise click.UsageError("--json and --jsonl are mutually exclusive.")

    try:
        provider = resolve_provider(cli_provider=ctx.obj.get("provider"))
        provider_label = provider.id
    except Exception:
        provider_label = "unknown"

    _sigterm_fired = False

    def _on_sigterm(signum, frame):
        nonlocal _sigterm_fired
        _sigterm_fired = True
        raise SystemExit(143)

    prev_handler = _signal.signal(_signal.SIGTERM, _on_sigterm)

    try:
        syke_logger = _logging.getLogger("syke")
        saved_levels = {
            h: h.level
            for h in syke_logger.handlers
            if isinstance(h, _logging.StreamHandler) and not isinstance(h, _logging.FileHandler)
        }
        for h in saved_levels:
            h.setLevel(_logging.CRITICAL)

        has_thinking = False
        has_streamed_text = False

        def _emit_json_line(payload: dict[str, object]) -> None:
            _sys.stdout.write(json.dumps(payload) + "\n")
            _sys.stdout.flush()

        jsonl_coalescer = _JsonlAskEventCoalescer(_emit_json_line) if use_jsonl else None
        if use_jsonl:
            _emit_json_line({"type": "status", "phase": "starting", "provider": provider_label})

        def _on_event(event: AskEvent) -> None:
            nonlocal has_thinking, has_streamed_text
            try:
                if use_jsonl:
                    if jsonl_coalescer is not None:
                        jsonl_coalescer.push(event)
                    return
                if use_json:
                    return
                if event.type == "thinking":
                    if not has_thinking:
                        _sys.stderr.write("\033[2;3m")
                        has_thinking = True
                    _sys.stderr.write(event.content)
                    _sys.stderr.flush()
                elif event.type == "text":
                    if has_thinking:
                        _sys.stderr.write("\033[0m\n")
                        _sys.stderr.flush()
                        has_thinking = False
                    has_streamed_text = True
                    _sys.stdout.write(event.content)
                    _sys.stdout.flush()
                elif event.type == "tool_call":
                    if has_thinking:
                        _sys.stderr.write("\033[0m\n")
                        _sys.stderr.flush()
                        has_thinking = False
                    preview = ""
                    inp = event.metadata and event.metadata.get("input")
                    if isinstance(inp, dict):
                        for v in inp.values():
                            if isinstance(v, str) and v:
                                preview = v[:60]
                                break
                    tool_name = event.content.removeprefix("mcp__syke__")
                    label = f"  ↳ {tool_name}({preview})"
                    _sys.stderr.write(f"\033[2m{label}\033[0m\n")
                    _sys.stderr.flush()
            except BrokenPipeError:
                raise

        try:
            answer, cost = run_ask(
                db=db,
                user_id=user_id,
                question=question,
                on_event=_on_event,
            )
        except BrokenPipeError:
            raise SystemExit(0) from None
        except Exception as e:
            if has_thinking and not (use_json or use_jsonl):
                _sys.stderr.write("\033[0m\n")
                _sys.stderr.flush()
            if jsonl_coalescer is not None:
                jsonl_coalescer.flush()
            for h, lvl in saved_levels.items():
                h.setLevel(lvl)
            if use_json or use_jsonl:
                payload = _build_ask_result_payload(
                    question=question,
                    answer=None,
                    provider=provider_label,
                    metadata=None,
                    ok=False,
                    error=str(e),
                )
                if use_jsonl:
                    _emit_json_line({"type": "error", "error": str(e), "provider": provider_label})
                else:
                    _sys.stdout.write(json.dumps(payload) + "\n")
                    _sys.stdout.flush()
                raise SystemExit(1) from e
            _sys.stderr.write(f"\nAsk failed ({provider_label}): {e}\n")
            _sys.stderr.flush()
            raise SystemExit(1) from e
        finally:
            if has_thinking and not (use_json or use_jsonl):
                _sys.stderr.write("\033[0m\n")
                _sys.stderr.flush()
            for h, lvl in saved_levels.items():
                h.setLevel(lvl)

        provider_out = provider_label
        if (
            isinstance(cost, dict)
            and isinstance(cost.get("provider"), str)
            and cost.get("provider")
        ):
            provider_out = cast(str, cost["provider"])
        result_payload = _build_ask_result_payload(
            question=question,
            answer=answer,
            provider=provider_out,
            metadata=cost if isinstance(cost, dict) else None,
            ok=True,
            error=None,
        )

        if use_json:
            _sys.stdout.write(json.dumps(result_payload) + "\n")
            _sys.stdout.flush()
            return
        if use_jsonl:
            if jsonl_coalescer is not None:
                jsonl_coalescer.flush()
            _emit_json_line({"type": "result", **result_payload})
            return

        if not has_streamed_text and answer and answer.strip():
            _sys.stdout.write(f"\n{answer}\n")
            _sys.stdout.flush()
        elif has_streamed_text:
            _sys.stdout.write("\n")
            _sys.stdout.flush()

        if cost:
            duration_ms = cost.get("duration_ms")
            secs = float(duration_ms) / 1000 if isinstance(duration_ms, int | float) else 0.0
            usd_raw = cost.get("cost_usd")
            usd = float(usd_raw) if isinstance(usd_raw, int | float) else 0.0
            input_tokens = cost.get("input_tokens")
            output_tokens = cost.get("output_tokens")
            total_tokens = sum(
                token_count
                for token_count in (input_tokens, output_tokens)
                if isinstance(token_count, int)
            )
            tool_calls = cost.get("tool_calls")
            footer = f"\033[2m{provider_label} · {secs:.1f}s · ${usd:.4f} · {total_tokens} tokens"
            if isinstance(tool_calls, int):
                footer += f" · {tool_calls} tools"
            _sys.stderr.write(f"{footer}\033[0m\n")
    finally:
        _signal.signal(_signal.SIGTERM, prev_handler)
        db.close()


@cli.command()
@click.argument("text", required=False)
@click.option("--tag", "-t", multiple=True, help="Tag(s) for categorization")
@click.option("--source", "-s", default="manual", help="Source label (default: manual)")
@click.option(
    "--title",
    default=None,
    help="Event title (auto-generated from first line if omitted)",
)
@click.option(
    "--json",
    "use_json",
    is_flag=True,
    help="Parse TEXT or stdin as a single JSON event",
)
@click.option(
    "--jsonl",
    "use_jsonl",
    is_flag=True,
    help="Parse stdin as newline-delimited JSON events (batch)",
)
@click.pass_context
def record(
    ctx: click.Context,
    text: str | None,
    tag: tuple[str, ...],
    source: str,
    title: str | None,
    use_json: bool,
    use_jsonl: bool,
) -> None:
    """Record an observation, note, or research dump into Syke.

    Accepts plain text as an argument, or piped stdin for longer content.

    Examples:
      syke record "Prefers concise answers"
      echo "Long research notes..." | syke record
      syke record --json '{"text": "...", "tags": ["work"]}'
      cat events.jsonl | syke record --jsonl
    """
    return _record_cmd.callback(ctx, text, tag, source, title, use_json, use_jsonl)

    from syke.observe.importers import IngestGateway

    user_id = ctx.obj["user"]
    db = get_db(user_id)

    try:
        gw = IngestGateway(db, user_id)

        # --- JSONL batch mode: read lines from stdin ---
        if use_jsonl:
            import json as _json

            if not sys.stdin.isatty():
                lines = sys.stdin.read().strip().splitlines()
            elif text:
                lines = text.strip().splitlines()
            else:
                console.print("[red]--jsonl requires piped input or text argument[/red]")
                raise SystemExit(1)

            events = []
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(_json.loads(line))
                except _json.JSONDecodeError as e:
                    console.print(f"[red]Line {i + 1}: invalid JSON — {e}[/red]")
                    raise SystemExit(1) from None

            if not events:
                console.print("[dim]No events to record.[/dim]")
                return

            result = gw.push_batch(events)
            console.print(
                f"Recorded [green]{result['inserted']}[/green] events"
                f" ({result['duplicates']} duplicates, {result['filtered']} filtered)"
            )
            return

        # --- JSON single mode: parse one structured event ---
        if use_json:
            import json as _json

            raw = text or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
            if not raw:
                console.print("[red]--json requires a JSON string as argument or stdin[/red]")
                raise SystemExit(1)

            try:
                ev = _json.loads(raw)
            except _json.JSONDecodeError as e:
                console.print(f"[red]Invalid JSON: {e}[/red]")
                raise SystemExit(1) from None

            result = cast(
                dict[str, object],
                gw.push(
                    source=ev.get("source", source),
                    event_type=ev.get("event_type", "observation"),
                    title=ev.get("title", ""),
                    content=ev.get("text", ev.get("content", "")),
                    timestamp=ev.get("timestamp"),
                    metadata={"tags": ev.get("tags", list(tag))} if ev.get("tags") or tag else None,
                    external_id=ev.get("external_id"),
                ),
            )
            if result["status"] == "ok":
                event_id = cast(str, result.get("event_id", ""))
                console.print(f"Recorded. [dim]({event_id[:8]})[/dim]")
            elif result["status"] == "duplicate":
                console.print("[dim]Already recorded (duplicate).[/dim]")
            elif result["status"] == "filtered":
                console.print(
                    f"[yellow]Filtered:[/yellow] {result.get('reason', 'content filter')}"
                )
            else:
                console.print(f"[red]Error:[/red] {result.get('error', 'unknown')}")
                raise SystemExit(1)
            return

        # --- Plain text mode: argument or stdin ---
        content = text or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
        if not content:
            console.print("[red]Nothing to record. Pass text as argument or pipe stdin.[/red]")
            console.print('[dim]  syke record "your observation"[/dim]')
            console.print('[dim]  echo "content" | syke record[/dim]')
            raise SystemExit(1)

        # Auto-generate title from first line if not provided
        if not title:
            first_line = content.split("\n")[0].strip()
            title = first_line[:120] if len(first_line) > 120 else first_line

        metadata = cast(dict[str, object] | None, {"tags": list(tag)} if tag else None)

        result = cast(
            dict[str, object],
            gw.push(
                source=source,
                event_type="observation",
                title=title or "",
                content=content,
                metadata=metadata,
            ),
        )

        if result["status"] == "ok":
            event_id = cast(str, result.get("event_id", ""))
            console.print(f"Recorded. [dim]({event_id[:8]})[/dim]")
        elif result["status"] == "duplicate":
            console.print("[dim]Already recorded (duplicate).[/dim]")
        elif result["status"] == "filtered":
            console.print(f"[yellow]Filtered:[/yellow] {result.get('reason', 'content filter')}")
        else:
            console.print(f"[red]Error:[/red] {result.get('error', 'unknown')}")
            raise SystemExit(1)
    finally:
        db.close()


def _term_menu_select(entries: list[str], title: str, default_index: int = 0) -> int | None:
    """Arrow-key selection menu with non-TTY fallback.

    Returns the selected index, or None if the user cancelled / non-interactive.
    """
    import sys

    if not sys.stdin.isatty():
        # Fallback: numbered list for CI / pipes / non-TTY
        for i, entry in enumerate(entries, 1):
            click.echo(f"  [{i}] {entry}")
        try:
            pick = click.prompt(
                "  Select",
                type=click.IntRange(1, len(entries)),
                default=default_index + 1,
            )
            return pick - 1
        except (click.Abort, EOFError):
            return None

    try:
        from simple_term_menu import TerminalMenu

        menu = TerminalMenu(
            entries,
            title=title,
            menu_cursor="  ▸ ",
            menu_cursor_style=("fg_yellow", "bold"),
            menu_highlight_style=("fg_yellow", "bold"),
            cursor_index=default_index,
            cycle_cursor=True,
        )
        result = menu.show()
        if result is None:
            return None
        # show() returns int for single-select, tuple for multi-select
        return result if isinstance(result, int) else result[0]
    except Exception:
        # Terminal doesn't support menus — fall back to numbered list
        for i, entry in enumerate(entries, 1):
            click.echo(f"  [{i}] {entry}")
        try:
            pick = click.prompt(
                "  Select",
                type=click.IntRange(1, len(entries)),
                default=default_index + 1,
            )
            return pick - 1
        except (click.Abort, EOFError):
            return None


def _term_menu_select_many(
    entries: list[str],
    title: str,
    default_indices: list[int] | None = None,
) -> list[int] | None:
    """Multi-select menu with non-TTY fallback."""
    import sys

    default_indices = sorted(set(default_indices or list(range(len(entries)))))

    if not sys.stdin.isatty():
        for i, entry in enumerate(entries, 1):
            marker = "[x]" if (i - 1) in default_indices else "[ ]"
            click.echo(f"  {marker} [{i}] {entry}")
        try:
            raw = click.prompt(
                "  Select sources (comma-separated, blank = defaults, 'none' = none)",
                default="",
                show_default=False,
            ).strip()
        except (click.Abort, EOFError):
            return None

        if not raw:
            return default_indices
        if raw.lower() == "none":
            return []

        picks: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                raise click.ClickException(f"Invalid source selection: {part!r}") from None
            if value < 1 or value > len(entries):
                raise click.ClickException(
                    f"Source selection out of range: {value}"
                ) from None
            picks.append(value - 1)
        return sorted(set(picks))

    try:
        from simple_term_menu import TerminalMenu

        menu = TerminalMenu(
            entries,
            title=title,
            menu_cursor="  ▸ ",
            menu_cursor_style=("fg_yellow", "bold"),
            menu_highlight_style=("fg_yellow", "bold"),
            cycle_cursor=True,
            multi_select=True,
            multi_select_empty_ok=True,
            preselected_entries=default_indices,
            show_multi_select_hint=True,
            show_multi_select_hint_text="Space to toggle, Enter to confirm",
        )
        result = menu.show()
        if result is None:
            return None
        if isinstance(result, tuple):
            return list(result)
        return [result]
    except Exception:
        for i, entry in enumerate(entries, 1):
            marker = "[x]" if (i - 1) in default_indices else "[ ]"
            click.echo(f"  {marker} [{i}] {entry}")
        try:
            raw = click.prompt(
                "  Select sources (comma-separated, blank = defaults, 'none' = none)",
                default="",
                show_default=False,
            ).strip()
        except (click.Abort, EOFError):
            return None

        if not raw:
            return default_indices
        if raw.lower() == "none":
            return []

        picks: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                raise click.ClickException(f"Invalid source selection: {part!r}") from None
            if value < 1 or value > len(entries):
                raise click.ClickException(
                    f"Source selection out of range: {value}"
                ) from None
            picks.append(value - 1)
        return sorted(set(picks))


def _choose_provider_interactive(
    choices: list[dict[str, object]] | None = None,
) -> _FlowChoice:
    """Return selected provider, or cancelled."""
    import sys

    from syke.pi_state import get_default_provider

    current_active = get_default_provider()
    choices = choices or _setup_provider_choices()

    if not sys.stdin.isatty():
        console.print("\n  Detected providers:")
        for item in choices:
            tag = "[green]ready[/green]" if item["ready"] else "[yellow]not ready[/yellow]"
            active = " (active)" if item["id"] == current_active and item["ready"] else ""
            console.print(f"    [{tag}]  {item['id']}  — {item['label']}{active}")
        console.print(
            "\n  [dim]No provider selected."
            " Use --provider <id> to choose, or run interactively.[/dim]"
        )
        return _FlowChoice("cancelled")

    entries: list[str] = []
    for item in choices:
        tag = ""
        if item["id"] == current_active and item["ready"]:
            tag = "  (active)"
        elif item["ready"]:
            tag = "  ✓"
        label = str(item["label"])
        if not item["ready"]:
            label = f"{label} — {item['detail']}"
        entries.append(f"{item['id']}  —  {label}{tag}")
    entries.append("Skip for now")

    default_idx = len(entries) - 1
    if current_active:
        for i, item in enumerate(choices):
            if item["id"] == current_active and item["ready"]:
                default_idx = i
                break

    idx = _term_menu_select(entries, title="\n  Select a provider:\n", default_index=default_idx)

    if idx is None or idx == len(entries) - 1:
        return _FlowChoice("cancelled")

    selected = choices[idx]
    return _FlowChoice("selected", cast(str, selected["id"]))


def _invalid_setup_endpoint_input(value: str) -> str | None:
    lowered = value.strip().lower()
    if not lowered:
        return None
    if "/auth/callback" in lowered or "localhost:" in lowered and "code=" in lowered:
        return "This looks like an OAuth callback URL, not a provider endpoint."
    return None


def _provider_endpoint_configured(provider_id: str) -> bool:
    from syke.pi_state import get_provider_base_url

    if get_provider_base_url(provider_id):
        return True
    if provider_id == "azure-openai-responses":
        return bool(
            os.getenv("AZURE_OPENAI_BASE_URL")
            or os.getenv("AZURE_OPENAI_RESOURCE_NAME")
        )
    return False


def _provider_action_choices(provider_id: str) -> list[tuple[str, str]]:
    from syke.llm.pi_client import get_pi_provider_catalog
    from syke.pi_state import get_credential, get_provider_base_url

    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    entry = catalog[provider_id]
    readiness = evaluate_provider_readiness(provider_id)
    credential = get_credential(provider_id)
    actions: list[tuple[str, str]] = []

    if readiness.ready:
        actions.append(("continue", "Continue with current auth/config"))
    if entry.oauth:
        label = "Sign in with Pi"
        if credential and credential.get("type") == "oauth":
            label = "Re-sign in with Pi"
        actions.append(("login", label))
    else:
        actions.append(("api_key", "Enter or replace API key/token"))
    actions.append(("endpoint", "Configure custom endpoint/base URL"))
    if get_provider_base_url(provider_id):
        actions.append(("clear_endpoint", "Remove custom endpoint/base URL"))
    actions.append(("back", "Back to provider list"))
    return actions


def _resolve_provider_auth_interactive(provider_id: str) -> _FlowChoice:
    from syke.llm.pi_client import get_pi_provider_catalog, run_pi_oauth_login
    from syke.pi_state import (
        get_provider_base_url,
        remove_provider_override,
        set_api_key,
        upsert_provider_override,
    )

    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    entry = catalog.get(provider_id)
    if entry is None:
        return _FlowChoice("cancelled")

    while True:
        console.print()
        console.print(f"[bold]Provider[/bold]: [cyan]{provider_id}[/cyan]")
        _render_provider_summary(_describe_provider(provider_id), indent="  ")
        if get_provider_base_url(provider_id):
            console.print(f"  [dim]Custom endpoint:[/dim] {get_provider_base_url(provider_id)}")
        console.print()

        actions = _provider_action_choices(provider_id)
        labels = [label for _, label in actions]
        default_index = 0
        for i, (action_id, _) in enumerate(actions):
            if action_id == "continue":
                default_index = i
                break
        idx = _term_menu_select(
            labels,
            title="\n  Choose auth/config action:\n",
            default_index=default_index,
        )
        if idx is None:
            return _FlowChoice("cancelled")
        action = actions[idx][0]

        if action == "continue":
            return _FlowChoice("continue")

        if action == "login":
            use_local_browser = click.confirm(
                "\n  Use this machine's browser for sign-in?",
                default=True,
            )
            try:
                run_pi_oauth_login(provider_id, manual=not use_local_browser)
            except Exception as exc:
                console.print(f"\n  [red]Pi login failed:[/red] {escape(str(exc))}")
                return _FlowChoice("cancelled")
            continue

        if action == "api_key":
            api_key = click.prompt(
                f"\n  API key/token for {provider_id}",
                hide_input=True,
                default="",
                show_default=False,
            )
            if api_key.strip():
                set_api_key(provider_id, api_key.strip())
            continue

        if action == "endpoint":
            prompt_label = (
                "  Azure resource endpoint/base URL"
                if provider_id == "azure-openai-responses"
                else "  Custom base URL/resource endpoint"
            )
            base_url = click.prompt(
                prompt_label,
                type=str,
                default="",
                show_default=False,
            ).strip()
            if not base_url:
                continue
            endpoint_error = _invalid_setup_endpoint_input(base_url)
            if endpoint_error:
                console.print(f"\n  [red]{endpoint_error}[/red]")
                continue
            upsert_provider_override(provider_id, base_url=base_url)
            continue

        if action == "clear_endpoint":
            remove_provider_override(provider_id)
            continue

        if action == "back":
            return _FlowChoice("back")


def _setup_pi_provider_flow(provider_id: str) -> bool:
    """Run the shared interactive provider/auth/model/probe flow for one provider."""
    return _run_interactive_provider_flow(initial_provider_id=provider_id).status == "selected"


def _setup_api_key_flow(provider_id: str | None = None) -> bool:
    """Prompt for API key and store it. Returns True if configured."""
    if provider_id is None:
        api_providers = [
            item["id"]
            for item in _setup_provider_choices()
            if not cast(bool, item.get("oauth"))
        ]
        entries = [f"{pid}" for pid in api_providers]
        idx = _term_menu_select(entries, title="\n  Which provider?\n")
        if idx is None:
            return False
        provider_id = api_providers[idx]
    return _setup_pi_provider_flow(provider_id)


def _ensure_setup_pi_runtime() -> tuple[str, str]:
    """Install/verify Pi before provider setup and bootstrap work."""
    import subprocess

    console.print("\n[bold]Step 1:[/bold] Pi agent runtime\n")
    try:
        from syke.llm.pi_client import ensure_pi_binary, get_pi_version

        pi_path = ensure_pi_binary()
        ver = get_pi_version(install=False)
    except (OSError, RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        console.print(f"  [red]✗[/red]  Pi runtime: {exc}")
        raise click.ClickException(
            "Setup requires a working Pi runtime before provider setup. "
            "Install Node.js (>= 18) and rerun."
        ) from exc

    console.print(f"  [green]OK[/green]  Pi runtime v{ver}")
    console.print(f"  [dim]Launcher:[/dim] {pi_path}")
    return str(pi_path), str(ver)


def _verify_setup_provider_connection(provider_id: str, model_id: str) -> None:
    from syke.llm.pi_client import probe_pi_provider_connection

    console.print("\n[bold]Step 2b:[/bold] Verify provider connection\n")
    ok, detail = probe_pi_provider_connection(provider_id, model_id)
    if not ok:
        raise click.ClickException(
            "Provider setup did not complete successfully. "
            f"Pi probe failed for {provider_id}/{model_id}: {detail}"
        )
    console.print(f"  [green]OK[/green]  Live Pi request succeeded ({detail})")


def _resolve_activation_model(provider_id: str, *, explicit_model: str | None = None) -> str:
    from syke.llm.pi_client import get_pi_provider_catalog
    from syke.pi_state import get_default_model

    if explicit_model:
        return explicit_model

    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    entry = catalog.get(provider_id)
    current_default_model = get_default_model()
    if entry is not None:
        model_candidates = tuple(entry.available_models or entry.models)
        if current_default_model and current_default_model in set(model_candidates):
            return current_default_model
        if entry.default_model and entry.default_model in set(model_candidates):
            return entry.default_model
        if model_candidates:
            return model_candidates[0]

    if current_default_model:
        return current_default_model

    raise click.ClickException(
        f"No model is configured for {provider_id}. Choose one first with setup or `syke auth set`."
    )


def _choose_provider_model_interactive(provider_id: str) -> _FlowChoice:
    from syke.llm.pi_client import get_pi_provider_catalog
    from syke.pi_state import get_default_model

    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    entry = catalog.get(provider_id)
    if entry is None:
        return _FlowChoice("cancelled")

    model_entries = list(entry.available_models or entry.models)
    if not model_entries:
        console.print(f"\n  [red]No models available for {provider_id}.[/red]")
        return _FlowChoice("back")

    current_default = get_default_model()
    default_model = (
        current_default
        if current_default in model_entries
        else entry.default_model or model_entries[0]
    )
    default_index = model_entries.index(default_model) if default_model in model_entries else 0
    idx = _term_menu_select(
        model_entries,
        title="\n  Select a model:\n",
        default_index=default_index,
    )
    if idx is None:
        return _FlowChoice("back")
    return _FlowChoice("selected", model_entries[idx])


def _verify_provider_activation(provider_id: str, model_id: str) -> None:
    from syke.llm.pi_client import probe_pi_provider_connection

    ok, detail = probe_pi_provider_connection(provider_id, model_id)
    if not ok:
        raise click.ClickException(
            f"Provider activation failed. Pi probe failed for {provider_id}/{model_id}: {detail}"
        )


def _run_interactive_provider_flow(
    *,
    initial_provider_id: str | None = None,
) -> _FlowChoice:
    from syke.pi_state import set_default_model, set_default_provider

    choices = _run_setup_stage("Loading providers...", _setup_provider_choices)
    provider_id = initial_provider_id
    stage = "provider" if provider_id is None else "auth"

    while True:
        if stage == "provider":
            selection = _choose_provider_interactive(choices)
            if selection.status != "selected" or selection.value is None:
                return _FlowChoice("cancelled")
            provider_id = selection.value
            stage = "auth"
            continue

        if provider_id is None:
            return _FlowChoice("cancelled")

        if stage == "auth":
            auth_result = _resolve_provider_auth_interactive(provider_id)
            if auth_result.status == "continue":
                stage = "model"
                continue
            if auth_result.status == "back":
                provider_id = None
                stage = "provider"
                continue
            return _FlowChoice("cancelled")

        if stage == "model":
            model_choice = _choose_provider_model_interactive(provider_id)
            if model_choice.status == "selected" and model_choice.value is not None:
                model_id = model_choice.value
                try:
                    _run_setup_stage(
                        f"Verifying {provider_id}/{model_id}...",
                        lambda provider_id=provider_id, model_id=model_id: _verify_provider_activation(
                            provider_id, model_id
                        ),
                    )
                except click.ClickException as exc:
                    console.print(f"\n  [yellow]{escape(str(exc))}[/yellow]")
                    stage = "model"
                    continue
                set_default_provider(provider_id)
                set_default_model(model_id)
                return _FlowChoice("selected", provider_id)
            stage = "auth"


def _choose_setup_sources_interactive(sources: list[dict[str, object]]) -> list[str]:
    detected = [item for item in sources if item.get("detected")]
    if not detected:
        return []

    entries = []
    for item in detected:
        latest_seen = item.get("latest_seen")
        latest_suffix = (
            f" · latest {latest_seen[:19].replace('T', ' ')}"
            if isinstance(latest_seen, str) and latest_seen
            else ""
        )
        entries.append(f"{item['source']} · {item['files_found']} files{latest_suffix}")

    selected = _term_menu_select_many(
        entries,
        title="\n  Select sources to connect (newest first):\n",
        default_indices=list(range(len(entries))),
    )
    if selected is None:
        raise click.Abort()
    return [cast(str, detected[idx]["source"]) for idx in selected]


@cli.command(short_help="Review and apply local memory setup.")
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Auto-consent confirmations (daemon install), never auto-selects provider",
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
    """Inspect current setup state, then apply the approved local memory plan.

    Human: syke setup
    Agent: syke setup --json
    """
    return _setup_cmd.callback(ctx, yes, use_json, skip_daemon, selected_sources_cli)

    user_id = ctx.obj["user"]
    if use_json:
        click.echo(
            json.dumps(
                _build_setup_inspect_payload(
                    user_id=user_id,
                    cli_provider=ctx.obj.get("provider"),
                ),
                indent=2,
            )
        )
        return

    console.print(f"\n[bold]Syke Setup[/bold] — user: [cyan]{user_id}[/cyan]")
    from syke.llm.env import resolve_provider

    cli_provider = ctx.obj.get("provider")
    inspect_info = _run_setup_stage(
        "Preparing setup plan...",
        lambda: _build_setup_inspect_payload(
            user_id=user_id,
            cli_provider=cli_provider,
        ),
    )
    _render_setup_inspect_summary(inspect_info)
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
            raise click.ClickException(
                f"Requested source(s) not detected during setup: {', '.join(unknown)}"
            )
        selected_sources = requested
    elif not yes and detected_sources:
        selected_sources = _choose_setup_sources_interactive(
            cast(list[dict[str, object]], inspect_info.get("sources") or [])
        )

    _render_section("Step 1 · Sources")
    if selected_sources:
        _render_setup_line("selected", ", ".join(selected_sources))
        skipped_sources = [source for source in detected_sources if source not in selected_sources]
        if skipped_sources:
            _render_setup_line("skipped", ", ".join(skipped_sources))
    elif detected_sources:
        _render_setup_line("selected", "none")
        _render_setup_line("skipped", ", ".join(detected_sources))
    else:
        _render_setup_line("selected", "none detected")

    _run_setup_stage("Checking Pi runtime...", _ensure_setup_pi_runtime)

    # Step 2: Choose LLM provider
    _render_section("Step 2 · Provider")
    has_provider = False

    if cli_provider:
        # Explicit --provider flag — use it directly
        try:
            provider = resolve_provider(cli_provider=cli_provider)
            has_provider = True
            console.print(f"  [green]✓[/green]  Provider: [bold]{provider.id}[/bold]")
        except (ValueError, RuntimeError) as e:
            console.print(f"  [red]✗[/red]  {e}")
    elif not yes and sys.stdin.isatty():
        flow = _run_interactive_provider_flow()
        has_provider = flow.status == "selected"
    elif cast(dict[str, object], inspect_info["provider"]).get("configured"):
        has_provider = True
        console.print(
            "  [green]✓[/green]  Keeping active provider:"
            f" [bold]{cast(dict[str, object], inspect_info['provider'])['id']}[/bold]"
        )
    else:
        flow = _run_interactive_provider_flow()
        has_provider = flow.status == "selected"

    if not has_provider:
        raise click.ClickException("Setup requires a configured provider.")

    provider_info = _provider_payload(ctx.obj.get("provider"))
    if provider_info.get("configured"):
        _render_provider_summary(provider_info, indent="  ")

    provider_id = cast(str | None, provider_info.get("id"))
    model_id = cast(str | None, provider_info.get("model"))
    if not provider_id or not model_id:
        raise click.ClickException("Setup requires a provider and model before ingest can begin.")

    # Step 3: Detect and ingest sources
    _render_section("Step 3 · Connect Sources")
    db = get_db(user_id)

    try:
        existing_total_before = db.count_events(user_id)
        had_memex_before = bool(db.get_memex(user_id))
        ingested_count = 0
        synthesis_started = False
        synthesis_ready_now = False
        distribution_result = None

        def _source_msg(name: str, source_key: str, new_count: int, unit: str = "events") -> None:
            """Print per-source result: new count + existing total."""
            existing = db.count_events(user_id, source=source_key)
            if new_count > 0:
                _render_setup_source_result(
                    name,
                    "ingested",
                    f"+{new_count} new {unit}, {existing} total",
                )
            elif existing > 0:
                _render_setup_source_result(name, "ingested", f"up to date, {existing} {unit}")
            else:
                _render_setup_source_result(name, "ingested", f"{new_count} {unit}")

        from syke.metrics import MetricsTracker
        from syke.observe.bootstrap import ensure_adapters

        _bootstrap_results = _run_setup_stage(
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
            _render_section("Source Results")
        for _bootstrap in _bootstrap_results:
            status_label = {
                "existing": "connected",
                "generated": "connected",
                "skipped": "skipped",
                "failed": "failed",
            }.get(_bootstrap.status, _bootstrap.status)
            _render_setup_source_result(_bootstrap.source, status_label, _bootstrap.detail)

        if (
            selected_sources
            and not _ingestible_sources
            and failed_bootstraps
            and existing_total_before == 0
        ):
            raise click.ClickException(
                "Setup could not bootstrap any selected sources. Fix the warnings above and rerun."
            )

        setup_registry = _observe_registry(user_id)
        for _desc in setup_registry.active_harnesses():
            _src = _desc.source
            if _src not in _ingestible_sources:
                continue
            _adapter = setup_registry.get_adapter(_src, db, user_id)
            if _adapter is None:
                continue
            try:
                tracker = MetricsTracker(user_id)
                with tracker.track(f"ingest_{_src}") as metrics:
                    _result = _adapter.ingest()
                    metrics.events_processed = _result.events_count
                _source_msg(_src, _src, _result.events_count, "events")
                ingested_count += _result.events_count
            except Exception as e:
                console.print(f"  [yellow]WARN[/yellow]  {_src}: {e}")

        # Check total events in DB (including previously ingested)
        total_in_db = db.count_events(user_id)
        if total_in_db == 0 and ingested_count == 0:
            console.print("[yellow]No data sources found to ingest.[/yellow]")

        # Cold start should leave the user with a memex now, not after the daemon's first tick.
        # Also rerun when setup ingested fresh data into an existing store.
        # Step 3b: Immediate synthesis when setup is creating or materially changing state.
        if has_provider and (ingested_count > 0 or (not had_memex_before and total_in_db > 0)):
            _render_section("Step 4 · Initial Synthesis")
            try:
                from syke.llm.backends.pi_synthesis import pi_synthesize

                synthesis_started = True
                synthesis_result = _run_setup_stage(
                    "Running initial synthesis...",
                    lambda: pi_synthesize(
                        db,
                        user_id,
                        force=True,
                        first_run=not had_memex_before,
                    ),
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
                    console.print(
                        "  [yellow]WARN[/yellow]  Initial synthesis did not complete:"
                        f" {synthesis_result.get('error') or synthesis_result.get('reason') or synthesis_result.get('status')}"
                    )
                    console.print("  [dim]Background sync will retry.[/dim]")
            except Exception as e:
                console.print(f"  [yellow]WARN[/yellow]  Initial synthesis failed: {e}")
                console.print("  [dim]Background sync will retry.[/dim]")

        # Step 4: Downstream distribution
        _render_section("Step 5 · Distribution")
        try:
            from syke.distribution import refresh_distribution

            distribution_result = _run_setup_stage(
                "Refreshing downstream capability surfaces...",
                lambda: refresh_distribution(db, user_id),
            )
            for key, status, detail in distribution_result.status_lines():
                _render_setup_line(key, status, detail=detail)
        except Exception as e:
            console.print(f"  [yellow]WARN[/yellow]  Distribution refresh failed: {e}")

        # Step 5: Background daemon
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
                try:
                    _run_setup_stage(
                        "Installing launchd-safe managed build...",
                        lambda: _run_managed_checkout_install(
                            user_id=user_id,
                            installer="auto",
                            restart_daemon=False,
                            prompt=False,
                        ),
                    )
                    daemon_info = _setup_daemon_viability_payload()
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
            _render_section("Step 6 · Background Sync")
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
                    _render_setup_line("daemon", "running", detail=f"PID {pid}")
                    daemon_started = True
                else:
                    _run_setup_stage(
                        "Enabling background sync...",
                        lambda: install_and_start(user_id, interval=900),
                    )
                    readiness = _wait_for_daemon_startup(user_id)
                    ipc = cast(dict[str, object], readiness["ipc"])
                    if readiness.get("running") and ipc.get("ok"):
                        daemon_started = True
                        _render_setup_line("daemon", "enabled", detail="syncs every 15 minutes")
                    elif readiness.get("running"):
                        daemon_started = True
                        _render_setup_line(
                            "daemon",
                            "starting",
                            detail=cast(
                                str,
                                ipc.get("detail")
                                or "daemon process is up; warm ask is not ready yet",
                            ),
                        )
                    else:
                        _render_setup_line(
                            "daemon",
                            "registered",
                            detail="background service registered; health not confirmed yet",
                        )
            except Exception as e:
                _render_setup_line("daemon", "failed", detail=str(e))
                console.print("  [dim]Manual start: syke daemon start[/dim]")

        # Final summary
        console.print("\n[bold green]Setup Complete[/bold green]")
        _render_setup_line("provider", provider_id or "(none)")
        _render_setup_line("model", model_id or "(none)")
        _render_setup_line("sources selected", ", ".join(selected_sources) if selected_sources else "none")
        _render_setup_line("events", f"{total_in_db} total", detail=f"+{ingested_count} new")
        if selected_sources:
            _render_section("Connected Sources")
            for source in selected_sources:
                bootstrap = bootstrap_by_source.get(source)
                if bootstrap is None:
                    _render_setup_source_result(source, "not run")
                    continue
                status_label = {
                    "existing": "connected",
                    "generated": "connected",
                    "skipped": "skipped",
                    "failed": "failed",
                }.get(bootstrap.status, bootstrap.status)
                _render_setup_source_result(source, status_label, bootstrap.detail)
        if synthesis_ready_now:
            _render_setup_line("synthesis", "completed", detail="memex ready now")
        elif synthesis_started:
            _render_setup_line("synthesis", "retrying", detail="background sync will continue")
        elif has_provider:
            _render_setup_line("synthesis", "pending", detail="run syke sync anytime")
        else:
            _render_setup_line("synthesis", "blocked", detail="configure a provider first")
        if daemon_started or daemon_info.get("running"):
            _render_setup_line("background sync", "enabled")
        elif skip_daemon:
            _render_setup_line("background sync", "skipped")
        else:
            _render_setup_line("background sync", "not enabled")

        console.print()
        next_commands = ["syke doctor", 'syke ask "..."', "syke context"]
        if daemon_started or daemon_info.get("running"):
            next_commands.append("syke daemon status")
        console.print(f"[dim]Next: {', '.join(next_commands)}[/dim]")

    finally:
        db.close()


@cli.command()
@click.pass_context
def sync(ctx: click.Context) -> None:
    """Sync new data and run synthesis.

    Pulls new events from all connected sources, then runs an incremental
    synthesis if enough new data is found (minimum 5 events).
    """
    return _sync_cmd.callback(ctx)

    from syke.sync import run_sync

    user_id = ctx.obj["user"]
    db = get_db(user_id)

    try:
        sources = db.get_sources(user_id)
        if not sources:
            console.print("[yellow]No data yet. Run: syke setup[/yellow]")
            return

        console.print(f"\n[bold]Syncing[/bold] — user: [cyan]{user_id}[/cyan]")
        console.print(f"  Sources: {', '.join(sources)}\n")

        total_new, synced = run_sync(db, user_id, out=console)

        console.print(
            f"\n[bold]Synced {total_new} new event(s) from {len(sources)} source(s).[/bold]"
        )
        if total_new == 0:
            console.print("[dim]Already up to date.[/dim]")

    finally:
        db.close()


# ---------------------------------------------------------------------------
# syke auth — provider credential management
# ---------------------------------------------------------------------------


@cli.group(invoke_without_command=True)
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Inspect or change the provider Syke will run with."""
    if ctx.invoked_subcommand is None:
        if sys.stdin.isatty():
            _run_setup_stage("Checking Pi runtime...", _ensure_setup_pi_runtime)
            flow = _run_interactive_provider_flow()
            if flow.status != "selected":
                return
            provider = _run_setup_stage(
                "Loading provider summary...",
                lambda: _provider_payload(ctx.obj.get("provider")),
            )
            console.print(f"\n[bold]Syke Auth[/bold] — user: [cyan]{ctx.obj['user']}[/cyan]")
            _render_provider_summary(provider, indent="  ")
            return
        ctx.invoke(auth_status)


@auth.command("status", short_help="Show resolved provider, auth source, model, and endpoint.")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def auth_status(ctx: click.Context, use_json: bool) -> None:
    """Show the resolved provider plus configured auth and runtime details."""
    from syke.llm.pi_client import get_pi_provider_catalog
    from syke.pi_state import get_default_provider, list_credential_providers, load_pi_models

    active = get_default_provider()
    selected = _run_setup_stage(
        "Loading provider status...",
        lambda: _provider_payload(ctx.obj.get("provider")),
    )

    configured_pids: set[str] = set(list_credential_providers())
    models_payload = load_pi_models()
    provider_overrides = models_payload.get("providers")
    if isinstance(provider_overrides, dict):
        configured_pids.update(pid for pid in provider_overrides if isinstance(pid, str))
    if active:
        configured_pids.add(active)

    catalog = get_pi_provider_catalog()

    providers_payload = _run_setup_stage(
        "Loading configured providers...",
        lambda: [
            _describe_provider(pid, selection_source="Pi settings" if pid == active else None)
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

    console.print(f"\n[bold]Syke Auth[/bold] — user: [cyan]{ctx.obj['user']}[/cyan]")
    if active:
        _render_setup_line("active provider", active, detail="Pi settings")
    else:
        _render_setup_line("active provider", "(none)")

    _render_provider_summary(selected, indent="  ")

    if configured_pids:
        _render_section("Configured Providers")
        for info in providers_payload:
            detail = (
                f"auth {info['auth_source']} • model {info['model']} • endpoint {info['endpoint']}"
            )
            status = "active" if info["id"] == active else "configured"
            if not info.get("configured"):
                status = "unready"
                detail = cast(str, info.get("error") or detail)
            _render_setup_line(cast(str, info["id"]), status, detail=detail)

    unconfigured = [entry.id for entry in catalog if entry.id not in configured_pids]
    if unconfigured:
        _render_section("Available Providers")
        _render_setup_line("available", ", ".join(unconfigured))


@auth.command("set", short_help="Store provider credentials and config.")
@click.argument("provider")
@click.option("--api-key", default=None, help="API key / auth token (required for cloud providers)")
@click.option("--endpoint", default=None, help="API endpoint URL / base URL override")
@click.option("--base-url", default=None, help="Base URL override")
@click.option("--model", default=None, help="Model name (e.g. gpt-5, deepseek-r1)")
@click.option("--api-version", default=None, help="Provider API version (advanced; env/runtime only)")
@click.option(
    "--use", "set_active", is_flag=True, default=False, help="Also make this the active provider"
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
    """Store provider credentials/config. Add --use to make it active."""
    from syke.llm.pi_client import ensure_pi_binary, get_pi_provider_catalog
    from syke.pi_state import (
        set_api_key,
        set_default_model,
        set_default_provider,
        upsert_provider_override,
    )

    ensure_pi_binary()
    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    is_known_provider = provider in catalog

    if api_version:
        raise click.ClickException(
            "--api-version is not persisted in Syke's Pi-owned state. "
            "Use Pi-native environment configuration instead."
        )

    if not is_known_provider and (not model or not (base_url or endpoint)):
        valid = ", ".join(sorted(catalog))
        console.print(
            f"[red]Unknown provider '{provider}'.[/red] Choose one of Pi's built-ins ({valid}) "
            "or provide both --model and --base-url/--endpoint for a custom provider."
        )
        raise SystemExit(1)

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
        selected_model = _resolve_activation_model(provider, explicit_model=model)
        if is_known_provider:
            status = evaluate_provider_readiness(provider)
            if not status.ready:
                console.print(
                    f"[yellow]Stored partial config for {provider}.[/yellow] "
                    f"{escape(status.detail)}"
                )
                raise SystemExit(1)
        _verify_provider_activation(provider, selected_model)
        set_default_model(selected_model)
        set_default_provider(provider)
        console.print(
            f"[green]✓[/green] Config stored and [bold]{provider}[/bold] set as active provider."
        )
    else:
        console.print(f"[green]✓[/green] Config stored for [bold]{provider}[/bold].")


@auth.command("login")
@click.argument("provider")
@click.option("--use", "set_active", is_flag=True, default=False, help="Also make this the active provider")
@click.pass_context
def auth_login(ctx: click.Context, provider: str, set_active: bool) -> None:
    """Run Pi's native OAuth login flow for a provider."""
    from syke.llm.pi_client import ensure_pi_binary, get_pi_provider_catalog, run_pi_oauth_login
    from syke.pi_state import set_default_model, set_default_provider

    ensure_pi_binary()
    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    entry = catalog.get(provider)
    if entry is None:
        valid = ", ".join(sorted(catalog))
        console.print(f"[red]Unknown provider '{provider}'. Valid: {valid}[/red]")
        raise SystemExit(1)
    if not entry.oauth:
        console.print(
            f"[yellow]{provider} does not advertise Pi-native OAuth login.[/yellow] "
            "Use `syke auth set ...` instead."
        )
        raise SystemExit(1)

    try:
        use_local_browser = click.confirm(
            "\n  Use this machine's browser for sign-in?",
            default=True,
        )
        run_pi_oauth_login(provider, manual=not use_local_browser)
    except Exception as exc:
        console.print(f"[red]Pi login failed:[/red] {escape(str(exc))}")
        raise SystemExit(1) from exc

    if set_active:
        selected_model = _resolve_activation_model(provider)
        _verify_provider_activation(provider, selected_model)
        set_default_model(selected_model)
        set_default_provider(provider)
    console.print(f"[green]✓[/green] Pi login completed for [bold]{provider}[/bold].")


@auth.command("use")
@click.argument("provider")
@click.pass_context
def auth_use(ctx: click.Context, provider: str) -> None:
    """Set the active LLM provider."""
    from syke.llm.pi_client import ensure_pi_binary, get_pi_provider_catalog
    from syke.pi_state import set_default_model, set_default_provider

    ensure_pi_binary()
    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    if provider not in catalog:
        valid = ", ".join(sorted(catalog))
        console.print(f"[red]Unknown provider '{provider}'. Valid: {valid}[/red]")
        raise SystemExit(1)

    status = evaluate_provider_readiness(provider)
    if not status.ready:
        console.print(f"[yellow]{provider} is not ready.[/yellow] {escape(status.detail)}")
        raise SystemExit(1)

    selected_model = _resolve_activation_model(provider)
    _verify_provider_activation(provider, selected_model)
    set_default_model(selected_model)
    set_default_provider(provider)
    console.print(f"[green]✓[/green] Active provider set to [bold]{provider}[/bold].")


@auth.command("unset")
@click.argument("provider")
@click.pass_context
def auth_unset(ctx: click.Context, provider: str) -> None:
    """Remove stored credentials for a provider."""
    from syke.pi_state import remove_credential

    removed = remove_credential(provider)
    if removed:
        console.print(f"[green]✓[/green] Credentials removed for [bold]{provider}[/bold].")
    else:
        console.print(f"[dim]No credentials stored for {provider}.[/dim]")


# ---------------------------------------------------------------------------
# syke config — configuration file management
# ---------------------------------------------------------------------------


@cli.group(invoke_without_command=True)
@click.pass_context
def config(ctx: click.Context) -> None:
    """Manage Syke configuration (~/.syke/config.toml)."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(config_show)


@config.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing config file")
@click.pass_context
def config_init(ctx: click.Context, force: bool) -> None:
    """Generate default config.toml with comments."""
    from syke.config_file import CONFIG_PATH, generate_default_config

    if CONFIG_PATH.exists() and not force:
        console.print(f"[yellow]Config already exists:[/yellow] {CONFIG_PATH}")
        console.print("[dim]Use --force to overwrite.[/dim]")
        return

    user_id = ctx.obj["user"]
    content = generate_default_config(user=user_id)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(content)
    console.print(f"[green]✓[/green] Wrote {CONFIG_PATH}")


@config.command("show")
@click.option("--raw", is_flag=True, help="Show raw TOML file contents")
@click.pass_context
def config_show(ctx: click.Context, raw: bool) -> None:
    """Show effective configuration — what's actually running."""
    from syke.config_file import CONFIG_PATH

    if raw:
        if CONFIG_PATH.exists():
            console.print(CONFIG_PATH.read_text())
        else:
            console.print(f"[dim]No config file at {CONFIG_PATH}[/dim]")
        return

    from syke import config as c

    console.print("[bold]Syke Configuration[/bold]")
    console.print(
        f"  [dim]File:[/dim] {CONFIG_PATH}"
        + (" [green](loaded)[/green]" if CONFIG_PATH.exists() else " [dim](defaults)[/dim]")
    )
    console.print()

    # ── Resolve active provider ────────────────────────────────────
    provider_id, provider_source, provider_details = _resolve_provider_display()
    console.print("  [bold]Provider[/bold]")
    if provider_id:
        console.print(f"    active: [cyan]{provider_id}[/cyan] [dim]({provider_source})[/dim]")
        for key, val in provider_details.items():
            console.print(f"    {key}: [cyan]{val}[/cyan]")
    else:
        console.print(
            "    active: [yellow](none)[/yellow] — run syke setup or syke auth set <provider> ... --use"
        )
    console.print()

    _section(
        "Synthesis",
        {
            "thinking level": c.SYNC_THINKING_LEVEL,
            "timeout": f"{c.SYNC_TIMEOUT}s",
            "first run timeout": f"{c.FIRST_RUN_SYNC_TIMEOUT}s",
            "threshold": f"{c.SYNC_EVENT_THRESHOLD} new events",
        },
    )
    _section(
        "Ask",
        {
            "timeout": f"{c.ASK_TIMEOUT}s",
        },
    )
    _section(
        "Daemon",
        {
            "interval": f"{c.DAEMON_INTERVAL}s ({c.DAEMON_INTERVAL // 60} min)",
        },
    )

    # ── Identity (compact) ─────────────────────────────────────────
    from syke.time import resolve_user_tz

    tz = resolve_user_tz()
    tz_display = str(tz) if str(tz) != c.SYKE_TIMEZONE else c.SYKE_TIMEZONE
    if c.SYKE_TIMEZONE == "auto":
        tz_display = f"{tz} (auto)"

    _section(
        "Identity",
        {
            "user": c.DEFAULT_USER,
            "timezone": tz_display,
            "data": str(c.DATA_DIR),
        },
    )


@config.command("path")
def config_path() -> None:
    """Print config file path."""
    from syke.config_file import CONFIG_PATH

    click.echo(CONFIG_PATH)


def _section(title: str, items: dict[str, object]) -> None:
    console.print(f"  [bold]{title}[/bold]")
    for key, val in items.items():
        console.print(f"    {key}: [cyan]{val}[/cyan]")
    console.print()


def _resolve_provider_display() -> tuple[str | None, str, dict[str, str]]:
    """Resolve active provider for display: (id, source, {detail_key: value})."""
    info = _provider_payload(None)
    if not info.get("configured"):
        return None, "", {}

    details = {
        "auth": str(info.get("auth_source") or "missing"),
        "runtime model": str(info.get("model") or "(none)"),
        "endpoint": str(info.get("endpoint") or "(none)"),
        "routing": str(info.get("runtime_provider") or "unknown"),
    }
    return cast(str | None, info.get("id")), str(info.get("source") or "Pi settings"), details


# ---------------------------------------------------------------------------
# syke daemon — background sync
# ---------------------------------------------------------------------------


@cli.group()
@click.pass_context
def daemon(ctx: click.Context) -> None:
    """Background sync daemon (start, stop, status, logs)."""
    pass


@daemon.command("start")
@click.option(
    "--interval",
    type=int,
    default=900,
    help="Sync interval in seconds (default: 900 = 15 min)",
)
@click.pass_context
def daemon_start(ctx: click.Context, interval: int) -> None:
    """Start background sync daemon (macOS LaunchAgent)."""
    return _daemon_start_cmd.callback(ctx, interval)

    from syke.daemon.daemon import install_and_start, is_running

    user_id = ctx.obj["user"]
    # Check if already running
    running, pid = is_running()
    if running:
        console.print(f"[yellow]Daemon already running (PID {pid})[/yellow]")
        return
    console.print(f"[bold]Starting daemon[/bold] — user: [cyan]{user_id}[/cyan]")
    console.print(f"  Sync interval: {interval}s ({interval // 60} minutes)")
    install_and_start(user_id, interval)
    readiness = _wait_for_daemon_startup(user_id)
    ipc = cast(dict[str, object], readiness["ipc"])
    if readiness.get("running") and ipc.get("ok"):
        console.print(
            f"[green]✓[/green] Daemon started. Sync runs every {interval // 60} minutes."
        )
    elif readiness.get("running"):
        console.print("[yellow]Daemon process started, but warm ask is not ready yet.[/yellow]")
        console.print(f"  IPC: {ipc.get('detail')}")
    else:
        console.print("[yellow]Daemon registered, but health is not confirmed yet.[/yellow]")
        console.print("  Check status: syke daemon status")
        console.print("  View logs:    syke daemon logs")
        return
    console.print("  Check status: syke daemon status")
    console.print("  View logs:    syke daemon logs")


@daemon.command("stop")
@click.pass_context
def daemon_stop(ctx: click.Context) -> None:
    """Stop background sync daemon."""
    return _daemon_stop_cmd.callback(ctx)

    import sys

    from syke.daemon.daemon import cron_is_running, is_running, launchd_metadata, stop_and_unload

    running, pid = is_running()
    if sys.platform == "darwin":
        registered = bool(launchd_metadata().get("registered"))
    else:
        registered, _ = cron_is_running()

    if not running and not registered:
        console.print("[dim]Daemon not running[/dim]")
        return

    if running and pid is not None:
        console.print(f"[bold]Stopping daemon[/bold] (PID {pid})")
    else:
        console.print("[bold]Removing daemon registration[/bold]")
    stop_and_unload()
    still_running, current_pid = is_running()
    if sys.platform == "darwin":
        still_registered = bool(launchd_metadata().get("registered"))
    else:
        still_registered, _ = cron_is_running()

    if still_running or still_registered:
        detail = f"running={still_running}"
        if current_pid is not None:
            detail += f", pid={current_pid}"
        detail += f", registered={still_registered}"
        console.print(f"[yellow]Daemon stop is incomplete.[/yellow] {detail}")
        return

    console.print("[green]✓[/green] Daemon stopped.")


@daemon.command("status")
@click.pass_context
def daemon_status_cmd(ctx: click.Context) -> None:
    """Check daemon status."""
    return _daemon_status_cmd.callback(ctx)

    from syke.daemon.daemon import LOG_PATH, is_running, launchd_metadata
    from syke.daemon.metrics import MetricsTracker
    from syke.runtime.locator import (
        SYKE_BIN,
        describe_runtime_target,
        resolve_background_syke_runtime,
        resolve_syke_runtime,
    )

    running, pid = is_running()
    user_id = ctx.obj["user"]
    console.print("[bold]Daemon status[/bold]")
    console.print(
        f"  Running:  {'[green]yes[/green] (PID ' + str(pid) + ')' if running else '[red]no[/red]'}"
    )
    launchd = launchd_metadata()
    if launchd.get("registered") and not running:
        if launchd.get("stale"):
            console.print(
                "  Launchd:  [yellow]stale[/yellow]"
                f" ({'; '.join(cast(list[str], launchd.get('stale_reasons') or []))})"
            )
        else:
            exit_status = launchd.get("last_exit_status")
            if exit_status is None:
                exit_status = "?"
            console.print(f"  Launchd:  registered (last exit: {exit_status})")
    # Last sync from metrics.jsonl
    try:
        summary = MetricsTracker(user_id).get_summary()
        last = summary.get("last_run")
        if last:
            ts = last.get("completed_at", "")[:19].replace("T", " ")
            events = last.get("events_processed", 0)
            ok = "[green]ok[/green]" if last.get("success") else "[red]failed[/red]"
            console.print(f"  Last run: {ts}  +{events} events  {ok}")
        else:
            console.print("  Last run: [dim]no data yet[/dim]")
    except Exception:
        console.print("  Last run: [dim]unavailable[/dim]")
    console.print(f"  Log:      {LOG_PATH}  [dim](syke daemon logs to view)[/dim]")
    try:
        current_runtime = resolve_syke_runtime()
        console.print(f"  CLI:      {describe_runtime_target(current_runtime)}")
    except Exception as exc:
        console.print(f"  CLI:      [yellow]unavailable: {exc}[/yellow]")
    try:
        runtime = resolve_background_syke_runtime()
        console.print(f"  Launcher: {SYKE_BIN}")
        console.print(f"  Target:   {describe_runtime_target(runtime)}")
    except Exception as exc:
        console.print(f"  Launcher: {SYKE_BIN}  [yellow]unavailable: {exc}[/yellow]")
    # Version info (cache-only, never hits network)
    from syke.version_check import cached_update_available

    update_avail, latest_cached = cached_update_available(__version__)
    console.print(f"  Version:  [cyan]{__version__}[/cyan]", end="")
    if update_avail and latest_cached:
        console.print(
            f"  [yellow]Update available: {latest_cached} — run: syke self-update[/yellow]"
        )
    else:
        console.print()


@daemon.command("run", hidden=True)
@click.option(
    "--interval",
    type=int,
    default=900,
    help="Cycle interval in seconds (default: 900 = 15 min)",
)
@click.pass_context
def daemon_run(ctx: click.Context, interval: int) -> None:
    return _daemon_run_cmd.callback(ctx, interval)

    from syke.daemon.daemon import SykeDaemon

    daemon_instance = SykeDaemon(ctx.obj["user"], interval=interval)
    daemon_instance.run()


@daemon.command()
@click.option("-n", "--lines", default=50, help="Number of lines to show (default: 50)")
@click.option("-f", "--follow", is_flag=True, help="Follow log output (like tail -f)")
@click.option("--errors", is_flag=True, help="Show only ERROR lines")
@click.pass_context
def logs(ctx: click.Context, lines: int, follow: bool, errors: bool) -> None:
    """View daemon log output."""
    return _daemon_logs_cmd.callback(ctx, lines, follow, errors)

    import time
    from collections import deque

    from syke.daemon.daemon import LOG_PATH

    if not LOG_PATH.exists():
        console.print(f"[yellow]No daemon log found at {LOG_PATH}[/yellow]")
        console.print("[dim]Is the daemon installed? Run: syke daemon start[/dim]")
        return

    if follow:
        with open(LOG_PATH) as f:
            f.seek(0, 2)  # seek to end
            while True:
                line = f.readline()
                if line:
                    if not errors or " ERROR " in line:
                        console.print(line.rstrip())
                else:
                    time.sleep(0.2)
    else:
        all_lines = LOG_PATH.read_text().splitlines()
        tail = list(deque(all_lines, maxlen=lines))
        if errors:
            tail = [line for line in tail if " ERROR " in line]
        for line in tail:
            console.print(line)


@cli.command("self-update")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def self_update(ctx: click.Context, yes: bool) -> None:
    """Upgrade syke to the latest version from PyPI."""
    return _self_update_cmd.callback(ctx, yes)

    import subprocess

    from syke.daemon.daemon import install_and_start, is_running, stop_and_unload
    from syke.version_check import check_update_available

    user_id = ctx.obj["user"]
    installed = __version__
    update_available, latest = check_update_available(installed)

    console.print(f"  Installed: [cyan]{installed}[/cyan]")
    if latest:
        console.print(f"  Latest:    [cyan]{latest}[/cyan]")
    else:
        console.print("  [yellow]Could not reach PyPI — check your connection.[/yellow]")
        return
    if not update_available:
        console.print("[green]Already up to date.[/green]")
        return

    method = _detect_install_method()

    if method == "uvx":
        console.print(
            "\n[yellow]Installed via uvx — uvx fetches the latest version automatically.[/yellow]"
        )
        console.print("  No action needed: uvx syke ... always uses the latest PyPI release.")
        return
    if method == "source":
        console.print("\n[yellow]Source install detected — update manually:[/yellow]")
        console.print("  git pull && pip install -e .")
        return

    if not yes:
        click.confirm(f"\nUpgrade syke {installed} → {latest}?", abort=True)

    # Stop daemon if running so the new binary is picked up cleanly
    was_running, _ = is_running()
    if was_running:
        console.print("  Stopping daemon...")
        stop_and_unload()
        stop_snapshot = _wait_for_daemon_shutdown(user_id)
        if stop_snapshot.get("running") or stop_snapshot.get("registered"):
            console.print("[red]Daemon did not stop cleanly. Aborting update.[/red]")
            return

    if method == "pipx":
        cmd = ["pipx", "upgrade", "syke"]
    elif method == "uv_tool":
        cmd = ["uv", "tool", "upgrade", "syke"]
    else:
        cmd = ["pip", "install", "--upgrade", "syke"]

    console.print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, timeout=300, check=False)
    if result.returncode != 0:
        console.print("[red]Upgrade failed.[/red]")
        return

    if was_running:
        console.print("  Restarting daemon...")
        install_and_start(user_id)
        readiness = _wait_for_daemon_startup(user_id)
        ipc = cast(dict[str, object], readiness["ipc"])
        if readiness.get("platform") == "Darwin":
            if readiness.get("running") and ipc.get("ok"):
                console.print(f"[green]✓[/green] syke upgraded to {latest}.")
                return
            if readiness.get("running"):
                console.print(
                    f"[yellow]syke upgraded to {latest}, but warm ask is not ready yet.[/yellow]"
                )
                console.print(f"  IPC: {ipc.get('detail')}")
                return
            console.print(
                f"[yellow]syke upgraded to {latest}, but daemon restart is not confirmed yet.[/yellow]"
            )
            console.print("  Check status: syke daemon status")
            return

    console.print(f"[green]✓[/green] syke upgraded to {latest}.")


# ---------------------------------------------------------------------------
# Dashboard (bare `syke` with no subcommand)
# ---------------------------------------------------------------------------


def _show_dashboard(user_id: str) -> None:
    """Show a quick status dashboard when `syke` is invoked without a subcommand."""
    import platform

    console.print(f"[bold]Syke[/bold] v{__version__}  ·  user: {user_id}\n")

    from syke.llm.env import resolve_provider

    try:
        provider = resolve_provider()
        auth_label = f"[green]{provider.id}[/green]"
    except (ValueError, RuntimeError):
        auth_label = "[yellow]not configured[/yellow]"
    console.print(f"  Provider: {auth_label}")

    # Daemon — prefer launchd (macOS one-shot), fall back to PID
    if platform.system() == "Darwin":
        from syke.daemon.daemon import launchd_metadata

        launchd = launchd_metadata()
        if launchd.get("registered"):
            exit_status = cast(int | None, launchd.get("last_exit_status"))
            if launchd.get("stale"):
                daemon_label = "[yellow]stale[/yellow] (launchd registration broken)"
            elif exit_status == 0:
                daemon_label = "[green]running[/green] (launchd)"
            else:
                daemon_label = (
                    f"[yellow]registered[/yellow] (last exit: {exit_status if exit_status is not None else '?'})"
                )
        else:
            daemon_label = "[dim]stopped[/dim]"
    else:
        from syke.daemon.daemon import is_running

        running, pid = is_running()
        if running:
            daemon_label = f"[green]running[/green] (PID {pid})"
        else:
            daemon_label = "[dim]stopped[/dim]"
    console.print(f"  Daemon:  {daemon_label}")

    # DB stats + Memex (both from DB)
    syke_db_path = user_syke_db_path(user_id)
    if syke_db_path.exists():
        db = get_db(user_id)
        try:
            count = db.count_events(user_id)
            status = db.get_status(user_id)
            last_event = status.get("latest_event_at", "never")
            console.print(f"  Events:  {count}")
            console.print(f"  Last:    {last_event or 'never'}")

            # Memex lives in the DB, not a file
            memex = db.get_memex(user_id)
            if memex:
                mem_count = db.count_memories(user_id)
                console.print(f"  Memex:   [green]synthesized[/green] ({mem_count} memories)")
            else:
                console.print("  Memex:   [yellow]not yet synthesized[/yellow] — run: syke sync")
        finally:
            db.close()
    else:
        console.print("  DB:      [dim]not initialized[/dim]")

    console.print("\n  Run [bold]syke --help[/bold] for commands.")


# ---------------------------------------------------------------------------
# Helper for doctor checks
# ---------------------------------------------------------------------------


def _print_check(name: str, ok: bool, detail: str) -> None:
    tag = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
    console.print(f"  {tag}  {name}: {detail}")


# ---------------------------------------------------------------------------
# syke context
# ---------------------------------------------------------------------------


@cli.command(short_help="Print the current MEMEX.md projection.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "markdown"]),
    default="markdown",
    help="Output format",
)
@click.pass_context
def context(ctx: click.Context, fmt: str) -> None:
    """Print the current memex projection from local storage."""
    return _context_cmd.callback(ctx, fmt)

    from syke.memory.memex import get_memex_for_injection

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        content = get_memex_for_injection(db, user_id)
        if not content:
            console.print("[dim]No memex yet. Run: syke setup[/dim]")
            return
        if fmt == "json":
            click.echo(json.dumps({"memex": content, "user": user_id}))
        else:
            click.echo(content)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# syke observe
# ---------------------------------------------------------------------------


@cli.command(short_help="Inspect self-observation and memory trends.")
@click.option("--watch", is_flag=True, help="Live refresh every 30 seconds")
@click.option("--days", "-d", default=7, help="Trend window in days (default: 7)")
@click.pass_context
def observe(ctx: click.Context, watch: bool, days: int) -> None:
    """Inspect self-observation, memory health, and synthesis trends."""
    return _observe_cmd.callback(ctx, watch, days)

    from syke.health import format_observe, full_observe

    user_id = ctx.obj["user"]
    db = get_db(user_id)

    try:
        if watch:
            import time

            try:
                while True:
                    click.clear()
                    data = full_observe(db, user_id)
                    output = format_observe(data)
                    console.print(output)
                    console.print("\n[dim]Refreshing every 30s — Ctrl+C to stop[/dim]")
                    time.sleep(30)
            except KeyboardInterrupt:
                console.print("\n[dim]Stopped.[/dim]")
        else:
            data = full_observe(db, user_id)
            output = format_observe(data)
            console.print(output)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# syke doctor
# ---------------------------------------------------------------------------


def _network_probe_payload(ctx: click.Context) -> dict[str, object]:
    from syke.llm.env import build_pi_runtime_env, resolve_provider

    try:
        provider = resolve_provider(cli_provider=ctx.obj.get("provider"))
    except (ValueError, RuntimeError) as exc:
        return {
            "ok": False,
            "provider": None,
            "detail": f"Cannot resolve provider: {exc}",
            "credential_envs": {},
            "url_envs": {},
        }

    try:
        env = build_pi_runtime_env(provider)
    except RuntimeError as exc:
        return {
            "ok": False,
            "provider": provider.id,
            "detail": str(exc),
            "credential_envs": {},
            "url_envs": {},
        }

    visible_creds = {
        name: value for name, value in env.items() if name.endswith("_API_KEY") and value
    }
    visible_urls = {
        name: value for name, value in env.items() if name.endswith("_BASE_URL") and value
    }
    detail = "Pi-native provider env prepared"
    if "PI_CODING_AGENT_DIR" in env:
        detail += " | syke-owned Pi state configured"
    if visible_creds:
        detail += f" | creds: {', '.join(sorted(visible_creds))}"
    if visible_urls:
        detail += f" | urls: {', '.join(sorted(visible_urls))}"
    return {
        "ok": True,
        "provider": provider.id,
        "detail": detail,
        "credential_envs": visible_creds,
        "url_envs": visible_urls,
    }


def _build_doctor_payload(ctx: click.Context, *, network: bool) -> dict[str, object]:
    from syke.daemon.daemon import is_running, launchd_metadata
    from syke.daemon.ipc import daemon_ipc_status
    from syke.llm.env import build_pi_runtime_env, resolve_provider
    from syke.llm.pi_client import PI_BIN, get_pi_version
    from syke.metrics import runtime_metrics_status
    from syke.observe.trace import self_observation_status
    from syke.runtime.locator import (
        SYKE_BIN,
        describe_runtime_target,
        resolve_background_syke_runtime,
        resolve_syke_runtime,
    )

    user_id = ctx.obj["user"]
    payload: dict[str, object] = {
        "ok": True,
        "user": user_id,
        "checks": {},
        "events": None,
        "memory_health": None,
        "network": None,
    }

    def _add_check(key: str, label: str, ok: bool, detail: str, **extra: object) -> None:
        checks = cast(dict[str, dict[str, object]], payload["checks"])
        checks[key] = {"label": label, "ok": ok, "detail": detail, **extra}
        if not ok:
            payload["ok"] = False

    try:
        provider = resolve_provider(cli_provider=ctx.obj.get("provider"))
        source = _resolve_source(ctx.obj.get("provider"))
        env = build_pi_runtime_env(provider)
        provider_info = _describe_provider(provider.id, selection_source=source)
        visible_tokens = {
            key: _redact_secret(value)
            for key, value in env.items()
            if key.endswith("_API_KEY") and value
        }
        visible_urls = {
            key: value for key, value in env.items() if key.endswith("_BASE_URL") and value
        }
        provider_status = evaluate_provider_readiness(provider.id)
        detail = f"{provider.id} (source: {source})"
        if provider_status.detail:
            detail = f"{detail} — {provider_status.detail}"
        _add_check(
            "provider",
            "Provider",
            provider_status.ready,
            detail,
            provider=provider.id,
            source=source,
            base_url=provider_info.get("endpoint"),
            credential_envs=visible_tokens,
            url_envs=visible_urls,
        )
    except (ValueError, RuntimeError) as exc:
        _add_check("provider", "Provider", False, str(exc))

    if PI_BIN.exists():
        try:
            ver = get_pi_version(install=False)
            _add_check("pi_runtime", "Pi runtime", True, f"v{ver} ({PI_BIN})", version=ver)
        except Exception as exc:
            _add_check(
                "pi_runtime",
                "Pi runtime",
                False,
                f"binary exists but failed: {exc}",
            )
    else:
        _add_check(
            "pi_runtime",
            "Pi runtime",
            False,
            "not installed — run 'syke setup' (requires Node.js)",
        )

    if PI_BIN.exists():
        try:
            get_pi_version(install=False, minimal_env=True)
            _add_check("pi_cold_start", "Pi cold-start", True, "minimal environment OK")
        except Exception as exc:
            _add_check(
                "pi_cold_start",
                "Pi cold-start",
                False,
                f"minimal environment failed: {exc}",
            )

    try:
        current_runtime = resolve_syke_runtime()
        _add_check(
            "cli_runtime",
            "CLI runtime",
            True,
            describe_runtime_target(current_runtime),
        )
    except Exception as exc:
        _add_check("cli_runtime", "CLI runtime", False, str(exc))

    try:
        background_runtime = resolve_background_syke_runtime()
        _add_check(
            "launcher",
            "Launcher",
            True,
            f"{SYKE_BIN} -> {describe_runtime_target(background_runtime)}",
            launcher=str(SYKE_BIN),
        )
    except Exception as exc:
        _add_check("launcher", "Launcher", False, f"{SYKE_BIN}: {exc}", launcher=str(SYKE_BIN))

    syke_db_path = user_syke_db_path(user_id)
    events_db_path = user_events_db_path(user_id)
    has_syke_db = syke_db_path.exists()
    has_events_db = events_db_path.exists()
    has_db = has_syke_db
    _add_check(
        "syke_db",
        "Syke DB",
        has_syke_db,
        str(syke_db_path) if has_syke_db else "not found — run 'syke setup'",
        path=str(syke_db_path),
    )
    _add_check(
        "events_db",
        "Events DB",
        has_events_db,
        str(events_db_path) if has_events_db else "not found — created on first run",
        path=str(events_db_path),
    )

    daemon_running, pid = is_running()
    launchd = launchd_metadata()
    if launchd.get("registered"):
        daemon_ok = daemon_running and not bool(launchd.get("stale"))
        if daemon_running and pid is not None:
            detail = f"launchd registered, PID {pid}"
        elif launchd.get("stale"):
            detail = "launchd stale: " + "; ".join(
                cast(list[str], launchd.get("stale_reasons") or [])
            )
        else:
            exit_status = launchd.get("last_exit_status")
            if exit_status is None:
                exit_status = "?"
            detail = f"launchd registered (last exit: {exit_status})"
    else:
        daemon_ok = daemon_running
        if daemon_running and pid is not None:
            detail = f"PID {pid}"
        else:
            detail = "not running — run 'syke daemon start'"
    _add_check("daemon", "Daemon", daemon_ok, detail, pid=pid)

    ipc = daemon_ipc_status(user_id)
    _add_check(
        "daemon_ipc",
        "Daemon IPC",
        bool(ipc["ok"]),
        cast(str, ipc["detail"]),
        **{k: v for k, v in ipc.items() if k not in {"ok", "detail"}},
    )

    self_obs = self_observation_status()
    _add_check(
        "self_observation",
        "Self-observation",
        bool(self_obs["ok"]),
        cast(str, self_obs["detail"]),
        **{k: v for k, v in self_obs.items() if k not in {"ok", "detail"}},
    )

    metrics_status = runtime_metrics_status(user_id)
    file_logging = metrics_status["file_logging"]
    _add_check(
        "file_logging",
        "File logging",
        bool(file_logging["ok"]),
        cast(str, file_logging["detail"]),
        **{k: v for k, v in file_logging.items() if k not in {"ok", "detail"}},
    )
    metrics_store = metrics_status["metrics_store"]
    _add_check(
        "metrics_store",
        "Metrics store",
        bool(metrics_store["ok"]),
        cast(str, metrics_store["detail"]),
        **{k: v for k, v in metrics_store.items() if k not in {"ok", "detail"}},
    )

    if has_db:
        db = get_db(user_id)
        try:
            event_count = db.count_events(user_id)
            payload["events"] = event_count

            from syke.health import (
                evolution_trends as _evo_trends,
            )
            from syke.health import (
                memex_health as _memex_h,
            )
            from syke.health import (
                memory_health as _mem_h,
            )
            from syke.health import (
                synthesis_health as _syn_h,
            )

            mh = _mem_h(db, user_id)
            _add_check(
                "graph",
                "Graph",
                mh["assessment"] in ("healthy", "dense"),
                f"{mh['active']} active, {mh['links']} links, "
                f"{mh['orphan_pct']}% orphaned ({mh['assessment']})",
                assessment=mh["assessment"],
            )

            sh = _syn_h(db, user_id)
            _add_check(
                "synthesis",
                "Synthesis",
                sh["assessment"] in ("active", "recent"),
                f"{sh['last_run_ago']} ({sh['assessment']})",
                assessment=sh["assessment"],
            )

            mx = _memex_h(db, user_id)
            _add_check(
                "memex",
                "Memex",
                mx["assessment"] in ("fresh", "healthy", "ok"),
                f"{mx['lines']} lines, updated {mx['updated_ago']} ({mx['assessment']})",
                assessment=mx["assessment"],
            )

            ev = _evo_trends(db, user_id)
            evolution_label = f"Evolution ({ev['days']}d)"
            _add_check(
                "evolution",
                evolution_label,
                ev["assessment"] != "dormant",
                f"+{ev['created']} created, -{ev['superseded']} superseded ({ev['assessment']})",
                assessment=ev["assessment"],
                days=ev["days"],
            )

            payload["memory_health"] = {
                "graph": mh,
                "synthesis": sh,
                "memex": mx,
                "evolution": ev,
            }
        finally:
            db.close()

    if network:
        payload["network"] = _network_probe_payload(ctx)
        if not cast(dict[str, object], payload["network"])["ok"]:
            payload["ok"] = False

    return payload


def _render_doctor_payload(payload: dict[str, object], *, network: bool) -> None:
    user_id = cast(str, payload["user"])
    console.print(f"[bold]Syke Doctor[/bold]  ·  user: {user_id}\n")

    checks = cast(dict[str, dict[str, object]], payload["checks"])
    provider_check = checks.get("provider")
    if provider_check:
        _print_check(
            cast(str, provider_check["label"]),
            bool(provider_check["ok"]),
            cast(str, provider_check["detail"]),
        )
        base_url = provider_check.get("base_url")
        if isinstance(base_url, str) and base_url:
            console.print(f"         Base URL: {base_url}")
        credential_envs = provider_check.get("credential_envs", {})
        if isinstance(credential_envs, dict):
            for env_name, token in sorted(credential_envs.items()):
                console.print(f"         {env_name}: {token}")
        url_envs = provider_check.get("url_envs", {})
        if isinstance(url_envs, dict):
            for env_name, value in sorted(url_envs.items()):
                console.print(f"         {env_name}: {value}")

    for key in (
        "pi_runtime",
        "pi_cold_start",
        "cli_runtime",
        "launcher",
        "syke_db",
        "events_db",
        "daemon",
        "daemon_ipc",
        "self_observation",
        "file_logging",
        "metrics_store",
    ):
        check = checks.get(key)
        if check:
            _print_check(
                cast(str, check["label"]),
                bool(check["ok"]),
                cast(str, check["detail"]),
            )

    if payload.get("events") is not None:
        console.print(f"  Events: {payload['events']}")
        console.print("\n  [bold]Memory Health[/bold]")
        for key in ("graph", "synthesis", "memex", "evolution"):
            check = checks.get(key)
            if check:
                _print_check(
                    cast(str, check["label"]),
                    bool(check["ok"]),
                    cast(str, check["detail"]),
                )

    if network:
        console.print("\n  [bold]Network Probe[/bold]")
        network_payload = cast(dict[str, object], payload["network"] or {})
        _print_check(
            "Network", bool(network_payload.get("ok")), cast(str, network_payload.get("detail", ""))
        )
        if network_payload.get("ok"):
            console.print(
                "         Pi-native HTTP probing is not implemented yet; use `syke ask` as the live check."
            )


@cli.command(short_help="Verify auth, runtime, DB, daemon, and memex health.")
@click.option("--network", is_flag=True, help="Test real API connectivity")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def doctor(ctx: click.Context, network: bool, use_json: bool) -> None:
    """Verify auth, runtime, DB, daemon, and memex health."""
    return _doctor_cmd.callback(ctx, network, use_json)

    payload = _build_doctor_payload(ctx, network=network)
    if use_json:
        click.echo(json.dumps(payload, indent=2))
        return
    _render_doctor_payload(payload, network=network)


def _resolve_source(cli_provider: str | None) -> str:
    if cli_provider:
        return "CLI --provider flag"
    if os.getenv("SYKE_PROVIDER"):
        return "SYKE_PROVIDER env"
    from syke.pi_state import get_default_provider

    if get_default_provider():
        return "Pi settings"
    return "unknown"


@cli.command(short_help="Generate or repair an Observe adapter for a harness path.")
@click.argument("path")
@click.pass_context
def connect(ctx: click.Context, path: str) -> None:
    """Generate or repair an Observe adapter for a local harness path."""
    return _connect_cmd.callback(ctx, path)


def _register_extracted_command_overrides() -> None:
    """Bind extracted command families over the legacy in-file implementations."""
    from syke.cli_commands.ask import ask as extracted_ask
    from syke.cli_commands.auth import auth as extracted_auth
    from syke.cli_commands.config import config as extracted_config
    from syke.cli_commands.daemon import daemon as extracted_daemon
    from syke.cli_commands.daemon import self_update as extracted_self_update
    from syke.cli_commands.maintenance import inject as extracted_inject
    from syke.cli_commands.maintenance import sync as extracted_sync
    from syke.cli_commands.record import record as extracted_record
    from syke.cli_commands.setup import setup as extracted_setup
    from syke.cli_commands.status import connect as extracted_connect
    from syke.cli_commands.status import context as extracted_context
    from syke.cli_commands.status import doctor as extracted_doctor
    from syke.cli_commands.status import observe as extracted_observe
    from syke.cli_commands.status import status as extracted_status

    cli.add_command(extracted_setup, name="setup")
    cli.add_command(extracted_ask, name="ask")
    cli.add_command(extracted_record, name="record")
    cli.add_command(extracted_status, name="status")
    cli.add_command(extracted_sync, name="sync")
    cli.add_command(extracted_auth, name="auth")
    cli.add_command(extracted_context, name="context")
    cli.add_command(extracted_observe, name="observe")
    cli.add_command(extracted_doctor, name="doctor")
    cli.add_command(extracted_connect, name="connect")
    cli.add_command(extracted_config, name="config")
    cli.add_command(extracted_daemon, name="daemon")
    cli.add_command(extracted_self_update, name="self-update")
    cli.add_command(extracted_inject, name="inject")
    cli.commands.pop("ingest", None)


_register_extracted_command_overrides()
