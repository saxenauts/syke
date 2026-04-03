"""Setup flow support helpers for the Syke CLI."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import cast

import click

from syke.cli_support.context import observe_registry
from syke.cli_support.daemon_state import daemon_payload
from syke.cli_support.providers import provider_payload, render_provider_summary
from syke.cli_support.render import SetupStatus, console, render_setup_line
from syke.config import user_events_db_path, user_syke_db_path


def run_setup_stage(label: str, fn):
    with SetupStatus(label):
        return fn()


def render_setup_source_result(source: str, status: str, detail: str | None = None) -> None:
    render_setup_line(source, status, detail=detail)


def trust_payload(user_id: str) -> dict[str, list[dict[str, str]]]:
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
    registry = observe_registry(user_id)
    for desc in registry.active_harnesses():
        if desc.discover is None:
            continue
        for root in desc.discover.roots:
            sources.append({"source": desc.source, "path": str(Path(root.path).expanduser())})

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


def setup_source_inventory(user_id: str) -> list[dict[str, object]]:
    from datetime import UTC, datetime

    sources: list[dict[str, object]] = []
    registry = observe_registry(user_id)

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


def setup_provider_choices() -> list[dict[str, object]]:
    from syke.llm.env import evaluate_provider_readiness
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


def setup_runtime_payload() -> dict[str, object]:
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


def setup_target_payload(
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


def setup_daemon_viability_payload() -> dict[str, object]:
    import platform

    from syke.runtime.locator import resolve_background_syke_runtime

    payload = daemon_payload()
    system = platform.system()
    detail = payload.get("detail")
    installable = True
    remediation: str | None = None

    if system == "Darwin":
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


def build_setup_inspect_payload(*, user_id: str, cli_provider: str | None) -> dict[str, object]:
    provider = provider_payload(cli_provider)
    providers = setup_provider_choices()
    sources = setup_source_inventory(user_id)
    trust = trust_payload(user_id)
    runtime = setup_runtime_payload()
    daemon = setup_daemon_viability_payload()
    setup_targets = setup_target_payload(
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
            "description": (
                "Run initial synthesis immediately when a provider is ready "
                "and setup creates or changes state."
            ),
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


def render_setup_inspect_summary(info: dict[str, object]) -> None:
    console.print("\n[bold]Setup plan[/bold]\n")
    render_provider_summary(cast(dict[str, object], info["provider"]), indent="  ")
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
            render_setup_line(cast(str, item["source"]), roots, detail=latest_detail, indent="    ")
    else:
        render_setup_line("sources", "none detected", indent="  ")

    proposed_actions = cast(list[dict[str, object]], info.get("proposed_actions") or [])
    if proposed_actions:
        console.print("\n  [bold]Planned actions[/bold]")
        for action in proposed_actions:
            sources = cast(list[str] | None, action.get("sources"))
            detail = ", ".join(sources) if sources else None
            render_setup_line(
                cast(str, action["id"]),
                cast(str, action["description"]),
                detail=detail,
                indent="    ",
            )

    daemon = cast(dict[str, object], info["daemon"])
    console.print("\n  [bold]Background sync[/bold]")
    state = "ready" if daemon.get("installable") else "blocked"
    render_setup_line(
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
            render_setup_line(
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
        render_setup_line(target["kind"], target["path"], indent="    ")


def choose_setup_sources_interactive(sources: list[dict[str, object]]) -> list[str]:
    from syke.cli_support.auth_flow import term_menu_select_many

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

    selected = term_menu_select_many(
        entries,
        title="\n  Select sources to connect (newest first):\n",
        default_indices=list(range(len(entries))),
    )
    if selected is None:
        raise click.Abort()
    return [cast(str, detected[idx]["source"]) for idx in selected]
