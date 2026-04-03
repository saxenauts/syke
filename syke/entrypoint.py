"""Public CLI entrypoint for Syke."""

from __future__ import annotations

import os

import click

from syke import __version__
from syke.cli_commands.ask import ask
from syke.cli_commands.auth import auth
from syke.cli_commands.config import config
from syke.cli_commands.daemon import daemon, self_update
from syke.cli_commands.maintenance import cost, install_current, sync
from syke.cli_commands.record import record
from syke.cli_commands.setup import setup
from syke.cli_commands.status import connect, context, doctor, observe, status
from syke.cli_support.dashboard import show_dashboard
from syke.config import DEFAULT_USER

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
        show_dashboard(user)


cli.add_command(setup)
cli.add_command(ask)
cli.add_command(record)
cli.add_command(status)
cli.add_command(sync)
cli.add_command(auth)
cli.add_command(context)
cli.add_command(observe)
cli.add_command(doctor)
cli.add_command(connect)
cli.add_command(config)
cli.add_command(daemon)
cli.add_command(self_update)
cli.add_command(cost)
cli.add_command(install_current)
