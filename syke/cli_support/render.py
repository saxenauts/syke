"""Rendering helpers for the Syke CLI."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

console = Console()


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


def render_setup_line(
    label: str,
    value: str,
    *,
    detail: str | None = None,
    indent: str = "  ",
) -> None:
    suffix = f" [dim]({detail})[/dim]" if detail else ""
    console.print(f"{indent}{label}: {value}{suffix}")


def render_setup_source_result(source: str, status: str, detail: str | None = None) -> None:
    render_setup_line(source, status, detail=detail)


def redact_secret(value: str) -> str:
    if not value:
        return "***"
    return f"*** ({len(value)} chars)"


def render_section(title: str) -> None:
    console.print(f"\n[bold]{title}[/bold]")


def print_check(name: str, ok: bool, detail: str) -> None:
    tag = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
    console.print(f"  {tag}  {name}: {detail}")


def render_kv_section(title: str, items: dict[str, object]) -> None:
    console.print(f"  [bold]{title}[/bold]")
    for key, val in items.items():
        console.print(f"    {key}: [cyan]{val}[/cyan]")
    console.print()
