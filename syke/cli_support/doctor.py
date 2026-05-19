"""Doctor payload and rendering helpers for the Syke CLI."""

from __future__ import annotations

from typing import cast

from syke.cli_support.context import get_db
from syke.cli_support.daemon_state import daemon_payload
from syke.cli_support.providers import describe_provider, resolve_source
from syke.cli_support.render import console, print_check, redact_secret
from syke.daemon.ipc import daemon_ipc_status
from syke.health import evolution_trends as _evo_trends
from syke.health import memex_health as _memex_h
from syke.health import memory_health as _mem_h
from syke.health import synthesis_health as _syn_h
from syke.llm.env import build_pi_runtime_env, evaluate_provider_readiness, resolve_provider
from syke.llm.pi_client import PI_BIN, get_pi_version
from syke.metrics import runtime_metrics_status
from syke.runtime.locator import (
    SYKE_BIN,
    describe_runtime_target,
    resolve_background_syke_runtime,
    resolve_syke_runtime,
)
from syke.trace_store import trace_store_status


def network_probe_payload(ctx) -> dict[str, object]:
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

    readiness = evaluate_provider_readiness(provider.id)
    if not readiness.ready:
        return {
            "ok": False,
            "provider": provider.id,
            "detail": f"Provider network env is not ready: {readiness.detail}",
            "credential_envs": {},
            "url_envs": {},
            "live_probe": False,
        }

    visible_creds = {
        name: value for name, value in env.items() if name.endswith("_API_KEY") and value
    }
    visible_urls = {
        name: value for name, value in env.items() if name.endswith("_BASE_URL") and value
    }
    detail = "Pi-native provider network env prepared (no live API request made)"
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
        "live_probe": False,
    }


def build_doctor_payload(ctx, *, network: bool) -> dict[str, object]:
    from syke.config import user_syke_db_path

    user_id = ctx.obj["user"]
    payload: dict[str, object] = {
        "ok": True,
        "user": user_id,
        "checks": {},
        "memories": None,
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
        source = resolve_source(ctx.obj.get("provider"))
        env = build_pi_runtime_env(provider)
        provider_info = describe_provider(provider.id, selection_source=source)
        visible_tokens = {
            key: redact_secret(value)
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
            _add_check("pi_runtime", "Pi runtime", False, f"binary exists but failed: {exc}")
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
        _add_check("cli_runtime", "CLI runtime", True, describe_runtime_target(current_runtime))
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
    has_syke_db = syke_db_path.exists()
    has_db = has_syke_db
    _add_check(
        "syke_db",
        "Syke DB",
        has_syke_db,
        str(syke_db_path) if has_syke_db else "not found — run 'syke setup'",
        path=str(syke_db_path),
    )

    daemon = daemon_payload()
    service = cast(dict[str, object], daemon.get("service") or {})
    daemon_running = bool(daemon.get("running"))
    daemon_ok = daemon_running and not bool(daemon.get("stale"))
    detail = cast(str, daemon.get("detail") or "not running — run 'syke daemon start'")
    if not daemon_running and not service.get("scheduled_only") and not daemon.get("registered"):
        detail = "not running — run 'syke daemon start'"
    _add_check(
        "daemon",
        "Daemon",
        daemon_ok,
        detail,
        pid=daemon.get("pid"),
        state=daemon.get("state"),
        manager=daemon.get("manager"),
        service=service,
    )

    ipc = daemon_ipc_status(user_id)
    _add_check(
        "daemon_ipc",
        "Daemon IPC",
        bool(ipc["ok"]),
        cast(str, ipc["detail"]),
        **{k: v for k, v in ipc.items() if k not in {"ok", "detail"}},
    )

    trace_status = trace_store_status(user_id)
    _add_check(
        "trace_store",
        "Rollout traces",
        bool(trace_status["ok"]),
        cast(str, trace_status["detail"]),
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
    trace_store = metrics_status["trace_store"]
    _add_check(
        "trace_store_runtime",
        "Trace store runtime",
        bool(trace_store["ok"]),
        cast(str, trace_store["detail"]),
        **{k: v for k, v in trace_store.items() if k not in {"ok", "detail"}},
    )

    # Harness accessibility — detect TCC or permission blocks
    try:
        import os as _os
        from pathlib import Path as _Path

        from syke.observe.catalog import active_sources, discovered_roots

        blocked: list[str] = []
        for spec in active_sources():
            for root in discovered_roots(spec):
                rp = _Path(root) if not isinstance(root, _Path) else root
                if rp.exists() and not _os.access(str(rp), _os.R_OK):
                    blocked.append(f"{spec.source}: {rp}")
        _add_check(
            "harness_access",
            "Harness access",
            len(blocked) == 0,
            "all harness roots readable" if not blocked else f"blocked: {', '.join(blocked)}",
        )
    except Exception:
        pass

    if has_db:
        db = get_db(user_id)
        try:
            memory_count = db.count_memories(user_id, active_only=True)
            payload["memories"] = memory_count
            mh = _mem_h(db, user_id)
            _add_check(
                "graph",
                "Graph",
                mh["assessment"] in ("healthy", "dense"),
                (
                    f"{mh['active']} active, {mh['links']} links, "
                    f"{mh['orphan_pct']}% orphaned ({mh['assessment']})"
                ),
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
        payload["network"] = network_probe_payload(ctx)
        if not cast(dict[str, object], payload["network"])["ok"]:
            payload["ok"] = False

    return payload


def render_doctor_payload(payload: dict[str, object], *, network: bool) -> None:
    user_id = cast(str, payload["user"])
    console.print(f"[bold]Syke Doctor[/bold]  ·  user: {user_id}\n")

    checks = cast(dict[str, dict[str, object]], payload["checks"])
    provider_check = checks.get("provider")
    if provider_check:
        print_check(
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
        "daemon",
        "daemon_ipc",
        "trace_store",
        "trace_store_runtime",
        "file_logging",
        "harness_access",
    ):
        check = checks.get(key)
        if check:
            print_check(cast(str, check["label"]), bool(check["ok"]), cast(str, check["detail"]))

    if payload.get("memories") is not None:
        console.print(f"  Memories: {payload['memories']}")
        console.print("\n  [bold]Memory Health[/bold]")
        for key in ("graph", "synthesis", "memex", "evolution"):
            check = checks.get(key)
            if check:
                print_check(
                    cast(str, check["label"]),
                    bool(check["ok"]),
                    cast(str, check["detail"]),
                )

    if network:
        console.print("\n  [bold]Network Probe[/bold]")
        network_payload = cast(dict[str, object], payload["network"] or {})
        print_check(
            "Network",
            bool(network_payload.get("ok")),
            cast(str, network_payload.get("detail", "")),
        )
        if network_payload.get("ok"):
            console.print("         No live API call was made; use `syke ask` as the live check.")
