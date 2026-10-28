from __future__ import annotations

import subprocess
from unittest.mock import patch

import click
import pytest

from syke.cli_support.auth_flow import (
    FlowChoice,
    ensure_setup_pi_runtime,
    invalid_setup_endpoint_input,
    run_interactive_provider_flow,
    term_menu_select_many,
)
from syke.cli_support.exit_codes import SykeRuntimeException
from syke.llm.pi_client import PiProviderCatalogEntry
from syke.pi_state import get_credential, get_default_model, get_default_provider, set_api_key

EXPIRED_KEY_DETAIL = (
    '401: {"type":"authentication_error","code":"api_key_expired",'
    '"message":"This API key has expired."}'
)


def test_oauth_callback_urls_are_not_accepted_as_provider_endpoints() -> None:
    for value in (
        "https://localhost:5050/auth/callback?code=abc123",
        "https://login.example.com/callback-url?code=abc123",
    ):
        assert invalid_setup_endpoint_input(value)


def test_non_tty_source_selection_parses_supported_forms() -> None:
    cases = (
        ("", [2, 0, 2], [0, 2]),
        ("3, 1, 3, 2", None, [0, 1, 2]),
        ("none", None, []),
    )
    with patch("syke.cli_support.auth_flow.sys.stdin.isatty", return_value=False):
        for raw, defaults, expected in cases:
            with patch("click.prompt", return_value=raw):
                assert (
                    term_menu_select_many(
                        ["alpha", "beta", "gamma"],
                        title="pick",
                        default_indices=defaults,
                    )
                    == expected
                )


def test_non_tty_source_selection_rejects_bad_input_and_handles_interrupts() -> None:
    with patch("syke.cli_support.auth_flow.sys.stdin.isatty", return_value=False):
        for raw in ("foo", "0", "4"):
            with (
                patch("click.prompt", return_value=raw),
                pytest.raises(click.UsageError),
            ):
                term_menu_select_many(["alpha", "beta", "gamma"], title="pick")

        for error in (click.Abort(), EOFError()):
            with patch("click.prompt", side_effect=error):
                assert term_menu_select_many(["alpha", "beta"], title="pick") is None


def test_setup_runtime_failures_share_one_public_error() -> None:
    failures = (
        OSError("node missing"),
        RuntimeError("install failed"),
        FileNotFoundError("launcher missing"),
        subprocess.TimeoutExpired(cmd="pi --version", timeout=10),
    )
    for failure in failures:
        with (
            patch("syke.llm.pi_client.ensure_pi_binary", side_effect=failure),
            pytest.raises(SykeRuntimeException, match="Setup requires a working Pi runtime"),
        ):
            ensure_setup_pi_runtime()

    with (
        patch("syke.llm.pi_client.ensure_pi_binary", return_value="/tmp/pi"),
        patch("syke.llm.pi_client.get_pi_version", side_effect=RuntimeError("bad version")),
        pytest.raises(SykeRuntimeException, match="Setup requires a working Pi runtime"),
    ):
        ensure_setup_pi_runtime()


def test_rejected_key_asks_for_a_new_key_and_retries_the_same_model(monkeypatch) -> None:
    entry = PiProviderCatalogEntry(
        "opencode-go",
        ("kimi-k2.6", "deepseek-v4.1-flash"),
        ("kimi-k2.6", "deepseek-v4.1-flash"),
        "kimi-k2.6",
        False,
    )
    monkeypatch.setattr("syke.llm.pi_client.get_pi_provider_catalog", lambda: (entry,))
    monkeypatch.setattr("syke.llm.env.get_pi_provider_catalog", lambda: (entry,))
    set_api_key("opencode-go", "expired-key")

    def probe(provider: str, model: str, **kwargs):
        if get_credential(provider) == {"type": "api_key", "key": "fresh-key"}:
            return True, "ready"
        return False, EXPIRED_KEY_DETAIL

    monkeypatch.setattr("syke.llm.pi_client.probe_pi_provider_connection", probe)
    picks = iter([0, 0, 1])
    monkeypatch.setattr(
        "syke.cli_support.auth_flow.term_menu_select",
        lambda entries, title, default_index=0: next(picks, None),
    )
    monkeypatch.setattr("click.prompt", lambda *args, **kwargs: "fresh-key")

    assert run_interactive_provider_flow() == FlowChoice("selected", "opencode-go")
    assert get_credential("opencode-go") == {"type": "api_key", "key": "fresh-key"}
    assert (get_default_provider(), get_default_model()) == ("opencode-go", "deepseek-v4.1-flash")
