"""Interactive auth and setup helpers for the Syke CLI."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import cast

import click
from rich.markup import escape

from syke.cli_support.exit_codes import SykeAuthException, SykeClickException, SykeRuntimeException
from syke.cli_support.render import console
from syke.llm.env import evaluate_provider_readiness


@dataclass(frozen=True)
class FlowChoice:
    status: str
    value: str | None = None


def term_menu_select(entries: list[str], title: str, default_index: int = 0) -> int | None:
    """Arrow-key selection menu with non-TTY fallback."""
    if not sys.stdin.isatty():
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
        return result if isinstance(result, int) else result[0]
    except Exception:
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


def term_menu_select_many(
    entries: list[str],
    title: str,
    default_indices: list[int] | None = None,
) -> list[int] | None:
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
                raise click.UsageError(f"Invalid source selection: {part!r}") from None
            if value < 1 or value > len(entries):
                raise click.UsageError(f"Source selection out of range: {value}") from None
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
                raise click.UsageError(f"Invalid source selection: {part!r}") from None
            if value < 1 or value > len(entries):
                raise click.UsageError(f"Source selection out of range: {value}") from None
            picks.append(value - 1)
        return sorted(set(picks))


def choose_provider_interactive(
    choices: list[dict[str, object]] | None = None,
) -> FlowChoice:
    from syke.cli_support.setup_support import setup_provider_choices
    from syke.pi_state import get_default_provider

    current_active = get_default_provider()
    choices = choices or setup_provider_choices()

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
        return FlowChoice("cancelled")

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

    idx = term_menu_select(entries, title="\n  Select a provider:\n", default_index=default_idx)
    if idx is None or idx == len(entries) - 1:
        return FlowChoice("cancelled")

    selected = choices[idx]
    return FlowChoice("selected", cast(str, selected["id"]))


def invalid_setup_endpoint_input(value: str) -> str | None:
    lowered = value.strip().lower()
    if not lowered:
        return None
    if "/auth/callback" in lowered or "localhost:" in lowered and "code=" in lowered:
        return "This looks like an OAuth callback URL, not a provider endpoint."
    return None


def provider_action_choices(provider_id: str) -> list[tuple[str, str]]:
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


def resolve_provider_auth_interactive(provider_id: str) -> FlowChoice:
    from syke.cli_support.providers import describe_provider, render_provider_summary
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
        return FlowChoice("cancelled")

    while True:
        prov = describe_provider(provider_id)
        auth = prov.get("auth_source", "missing")
        model = prov.get("model", "(none)")
        if prov.get("configured"):
            console.print(f"\n  [green]✓[/green] {provider_id}  {model}  [dim]{auth}[/dim]")
        else:
            error = prov.get("error") or f"not configured"
            console.print(f"\n  [yellow]✗[/yellow] {provider_id}: {error}")
        custom_ep = get_provider_base_url(provider_id)
        if custom_ep:
            console.print(f"    [dim]endpoint: {custom_ep}[/dim]")
        console.print()

        actions = provider_action_choices(provider_id)
        labels = [label for _, label in actions]
        default_index = 0
        for i, (action_id, _) in enumerate(actions):
            if action_id == "continue":
                default_index = i
                break
        idx = term_menu_select(
            labels,
            title="\n  Choose auth/config action:\n",
            default_index=default_index,
        )
        if idx is None:
            return FlowChoice("cancelled")
        action = actions[idx][0]

        if action == "continue":
            return FlowChoice("continue")

        if action == "login":
            use_local_browser = click.confirm(
                "\n  Use this machine's browser for sign-in?",
                default=True,
            )
            try:
                run_pi_oauth_login(provider_id, manual=not use_local_browser)
            except Exception as exc:
                console.print(f"\n  [red]Pi login failed:[/red] {escape(str(exc))}")
                return FlowChoice("cancelled")
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
            endpoint_error = invalid_setup_endpoint_input(base_url)
            if endpoint_error:
                console.print(f"\n  [red]{endpoint_error}[/red]")
                continue
            upsert_provider_override(provider_id, base_url=base_url)
            continue

        if action == "clear_endpoint":
            remove_provider_override(provider_id)
            continue

        if action == "back":
            return FlowChoice("back")


def setup_pi_provider_flow(provider_id: str) -> bool:
    return run_interactive_provider_flow(initial_provider_id=provider_id).status == "selected"


def setup_api_key_flow(provider_id: str | None = None) -> bool:
    from syke.cli_support.setup_support import setup_provider_choices

    if provider_id is None:
        api_providers = [
            item["id"]
            for item in setup_provider_choices()
            if not cast(bool, item.get("oauth"))
        ]
        entries = [f"{pid}" for pid in api_providers]
        idx = term_menu_select(entries, title="\n  Which provider?\n")
        if idx is None:
            return False
        provider_id = api_providers[idx]
    return setup_pi_provider_flow(provider_id)


def ensure_setup_pi_runtime() -> tuple[str, str]:
    try:
        from syke.llm.pi_client import ensure_pi_binary, get_pi_version

        pi_path = ensure_pi_binary()
        ver = get_pi_version(install=False)
    except (OSError, RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        console.print(f"  [red]✗[/red]  Pi runtime: {exc}")
        raise SykeRuntimeException(
            "Setup requires a working Pi runtime before provider setup. "
            "Install Node.js (>= 18) and rerun."
        ) from exc

    console.print(f"  [green]✓[/green] Pi v{ver}")
    return str(pi_path), str(ver)


def verify_setup_provider_connection(provider_id: str, model_id: str) -> str:
    from syke.llm.pi_client import probe_pi_provider_connection

    ok, detail = probe_pi_provider_connection(
        provider_id,
        model_id,
        prompt="Reply with only these exact words: syke loaded",
    )
    if not ok:
        raise SykeRuntimeException(
            "Provider setup did not complete successfully. "
            f"Pi probe failed for {provider_id}/{model_id}: {detail}"
        )
    return detail


def resolve_activation_model(provider_id: str, *, explicit_model: str | None = None) -> str:
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

    raise SykeAuthException(
        f"No model is configured for {provider_id}. Choose one first with setup or `syke auth set`."
    )


def choose_provider_model_interactive(provider_id: str) -> FlowChoice:
    from syke.llm.pi_client import get_pi_provider_catalog
    from syke.pi_state import get_default_model

    catalog = {entry.id: entry for entry in get_pi_provider_catalog()}
    entry = catalog.get(provider_id)
    if entry is None:
        return FlowChoice("cancelled")

    model_entries = list(entry.available_models or entry.models)
    if not model_entries:
        console.print(f"\n  [red]No models available for {provider_id}.[/red]")
        return FlowChoice("back")

    current_default = get_default_model()
    default_model = (
        current_default
        if current_default in model_entries
        else entry.default_model or model_entries[0]
    )
    default_index = model_entries.index(default_model) if default_model in model_entries else 0
    idx = term_menu_select(
        model_entries,
        title="\n  Select a model:\n",
        default_index=default_index,
    )
    if idx is None:
        return FlowChoice("back")
    return FlowChoice("selected", model_entries[idx])


def verify_provider_activation(provider_id: str, model_id: str) -> None:
    from syke.llm.pi_client import probe_pi_provider_connection

    ok, detail = probe_pi_provider_connection(provider_id, model_id)
    if not ok:
        raise SykeRuntimeException(
            f"Provider activation failed. Pi probe failed for {provider_id}/{model_id}: {detail}"
        )


def run_interactive_provider_flow(
    *,
    initial_provider_id: str | None = None,
) -> FlowChoice:
    from syke.cli_support.setup_support import run_setup_stage, setup_provider_choices
    from syke.pi_state import set_default_model, set_default_provider

    choices = run_setup_stage("Loading providers...", setup_provider_choices)
    provider_id = initial_provider_id
    stage = "provider" if provider_id is None else "auth"

    while True:
        if stage == "provider":
            selection = choose_provider_interactive(choices)
            if selection.status != "selected" or selection.value is None:
                return FlowChoice("cancelled")
            provider_id = selection.value
            stage = "auth"
            continue

        if provider_id is None:
            return FlowChoice("cancelled")

        if stage == "auth":
            auth_result = resolve_provider_auth_interactive(provider_id)
            if auth_result.status == "continue":
                stage = "model"
                continue
            if auth_result.status == "back":
                provider_id = None
                stage = "provider"
                continue
            return FlowChoice("cancelled")

        if stage == "model":
            model_choice = choose_provider_model_interactive(provider_id)
            if model_choice.status == "selected" and model_choice.value is not None:
                model_id = model_choice.value
                try:
                    run_setup_stage(
                        f"Verifying {provider_id}/{model_id}...",
                        lambda provider_id=provider_id, model_id=model_id: (
                            verify_provider_activation(provider_id, model_id)
                        ),
                    )
                except SykeClickException as exc:
                    console.print(f"\n  [yellow]{escape(str(exc))}[/yellow]")
                    stage = "model"
                    continue
                set_default_provider(provider_id)
                set_default_model(model_id)
                return FlowChoice("selected", provider_id)
            stage = "auth"
