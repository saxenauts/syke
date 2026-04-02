"""Rendering helpers for the Syke CLI."""

from __future__ import annotations

import threading
import time

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


def _format_elapsed(seconds: int) -> str:
    minutes, sec = divmod(max(seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


class SetupStatus:
    def __init__(self, label: str) -> None:
        self.label = label
        self.detail: str | None = None
        self._started_at = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = None
        self._status_cm = None

    def _render(self) -> str:
        elapsed = _format_elapsed(int(time.monotonic() - self._started_at))
        detail = ""
        if self.detail:
            detail = f" [dim]· {escape(self.detail)}[/dim]"
        return f"[bold]{self.label}[/bold] [dim]{elapsed}[/dim]{detail}"

    def __enter__(self) -> SetupStatus:
        self._started_at = time.monotonic()
        self._status_cm = console.status(self._render(), spinner="dots")
        self._status = self._status_cm.__enter__()

        def _heartbeat() -> None:
            while not self._stop.wait(0.25):
                with self._lock:
                    if self._status is not None:
                        self._status.update(self._render())

        self._thread = threading.Thread(target=_heartbeat, daemon=True)
        self._thread.start()
        return self

    def update(self, detail: str) -> None:
        with self._lock:
            self.detail = detail
            if self._status is not None:
                self._status.update(self._render())

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._status_cm is not None:
            self._status_cm.__exit__(exc_type, exc, tb)
        self._status = None
        self._status_cm = None


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
