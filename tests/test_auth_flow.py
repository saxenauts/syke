from __future__ import annotations

import subprocess
from unittest.mock import patch

import click
import pytest

from syke.cli_support.auth_flow import (
    ensure_setup_pi_runtime,
    invalid_setup_endpoint_input,
    provider_action_choices,
    term_menu_select_many,
)
from syke.cli_support.exit_codes import SykeRuntimeException
from syke.llm.env import ProviderReadiness
from syke.llm.pi_client import PiProviderCatalogEntry


@pytest.mark.parametrize(
    "value",
    (
        "https://localhost:5050/auth/callback?code=abc123",
        "https://login.example.com/callback-url?code=abc123",
    ),
)
def test_invalid_setup_endpoint_input_rejects_oauth_callback_urls(value: str) -> None:
    assert invalid_setup_endpoint_input(value) == (
        "This looks like an OAuth callback URL, not a provider endpoint."
    )


def test_term_menu_select_many_non_tty_blank_uses_defaults() -> None:
    with (
        patch("syke.cli_support.auth_flow.sys.stdin.isatty", return_value=False),
        patch("click.prompt", return_value=""),
    ):
        selected = term_menu_select_many(
            ["alpha", "beta", "gamma"],
            title="pick",
            default_indices=[2, 0, 2],
        )

    assert selected == [0, 2]


def test_term_menu_select_many_non_tty_parses_deduped_sorted_indices() -> None:
    with (
        patch("syke.cli_support.auth_flow.sys.stdin.isatty", return_value=False),
        patch("click.prompt", return_value="3, 1, 3, 2"),
    ):
        selected = term_menu_select_many(["alpha", "beta", "gamma"], title="pick")

    assert selected == [0, 1, 2]


def test_term_menu_select_many_non_tty_accepts_none_keyword() -> None:
    with (
        patch("syke.cli_support.auth_flow.sys.stdin.isatty", return_value=False),
        patch("click.prompt", return_value="none"),
    ):
        selected = term_menu_select_many(["alpha", "beta"], title="pick")

    assert selected == []


@pytest.mark.parametrize(
    ("raw", "pattern"),
    (
        ("foo", r"Invalid source selection: 'foo'"),
        ("0", "Source selection out of range: 0"),
        ("4", "Source selection out of range: 4"),
    ),
)
def test_term_menu_select_many_non_tty_invalid_input_raises_usage_error(
    raw: str, pattern: str
) -> None:
    with (
        patch("syke.cli_support.auth_flow.sys.stdin.isatty", return_value=False),
        patch("click.prompt", return_value=raw),
        pytest.raises(click.UsageError, match=pattern),
    ):
        term_menu_select_many(["alpha", "beta", "gamma"], title="pick")


@pytest.mark.parametrize("prompt_error", (click.Abort(), EOFError()))
def test_term_menu_select_many_non_tty_prompt_interrupt_returns_none(
    prompt_error: Exception,
) -> None:
    with (
        patch("syke.cli_support.auth_flow.sys.stdin.isatty", return_value=False),
        patch("click.prompt", side_effect=prompt_error),
    ):
        selected = term_menu_select_many(["alpha", "beta"], title="pick")

    assert selected is None


@pytest.mark.parametrize(
    ("oauth", "ready", "credential", "base_url", "expected"),
    (
        (
            True,
            True,
            None,
            None,
            [
                ("continue", "Continue with current auth/config"),
                ("login", "Sign in with Pi"),
                ("endpoint", "Configure custom endpoint/base URL"),
                ("back", "Back to provider list"),
            ],
        ),
        (
            True,
            False,
            {"type": "oauth"},
            "https://proxy.example.com",
            [
                ("login", "Re-sign in with Pi"),
                ("endpoint", "Configure custom endpoint/base URL"),
                ("clear_endpoint", "Remove custom endpoint/base URL"),
                ("back", "Back to provider list"),
            ],
        ),
        (
            False,
            True,
            {"type": "api_key"},
            "https://proxy.example.com",
            [
                ("continue", "Continue with current auth/config"),
                ("api_key", "Enter or replace API key/token"),
                ("endpoint", "Configure custom endpoint/base URL"),
                ("clear_endpoint", "Remove custom endpoint/base URL"),
                ("back", "Back to provider list"),
            ],
        ),
    ),
)
def test_provider_action_choices_permutations(
    monkeypatch: pytest.MonkeyPatch,
    oauth: bool,
    ready: bool,
    credential: dict[str, str] | None,
    base_url: str | None,
    expected: list[tuple[str, str]],
) -> None:
    monkeypatch.setattr(
        "syke.llm.pi_client.get_pi_provider_catalog",
        lambda: (
            PiProviderCatalogEntry(
                "example-provider",
                ("model-a",),
                ("model-a",),
                "model-a",
                oauth,
            ),
        ),
    )
    monkeypatch.setattr(
        "syke.cli_support.auth_flow.evaluate_provider_readiness",
        lambda provider_id: ProviderReadiness(provider_id, ready, "detail"),
    )
    monkeypatch.setattr("syke.pi_state.get_credential", lambda provider_id: credential)
    monkeypatch.setattr("syke.pi_state.get_provider_base_url", lambda provider_id: base_url)

    actions = provider_action_choices("example-provider")

    assert actions == expected


@pytest.mark.parametrize(
    "failure",
    (
        OSError("node missing"),
        RuntimeError("install failed"),
        FileNotFoundError("launcher missing"),
        subprocess.TimeoutExpired(cmd="pi --version", timeout=10),
    ),
)
def test_ensure_setup_pi_runtime_maps_binary_failures_to_syke_runtime_exception(
    failure: Exception,
) -> None:
    with (
        patch("syke.llm.pi_client.ensure_pi_binary", side_effect=failure),
        pytest.raises(SykeRuntimeException, match="Setup requires a working Pi runtime"),
    ):
        ensure_setup_pi_runtime()


def test_ensure_setup_pi_runtime_maps_version_failure_to_syke_runtime_exception() -> None:
    with (
        patch("syke.llm.pi_client.ensure_pi_binary", return_value="/tmp/pi"),
        patch("syke.llm.pi_client.get_pi_version", side_effect=RuntimeError("bad version")),
        pytest.raises(SykeRuntimeException, match="Setup requires a working Pi runtime"),
    ):
        ensure_setup_pi_runtime()
