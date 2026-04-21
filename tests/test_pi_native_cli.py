from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import click

from syke.cli_support.auth_flow import (
    FlowChoice,
    choose_provider_model_interactive,
    resolve_activation_model,
    run_interactive_provider_flow,
    setup_pi_provider_flow,
)
from syke.cli_support.providers import describe_provider
from syke.cli_support.setup_support import setup_provider_choices, setup_source_inventory
from syke.entrypoint import cli
from syke.llm.env import ProviderReadiness
from syke.llm.pi_client import PiProviderCatalogEntry
from syke.runtime.locator import SykeRuntimeDescriptor


def _patch_catalog(monkeypatch, entries: tuple[PiProviderCatalogEntry, ...]) -> None:
    monkeypatch.setattr("syke.llm.pi_client.get_pi_provider_catalog", lambda: entries)
    monkeypatch.setattr("syke.llm.env.get_pi_provider_catalog", lambda: entries)


def test_auth_set_builtin_provider_writes_pi_native_state(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    monkeypatch.setattr(
        "syke.llm.pi_client.probe_pi_provider_connection",
        lambda provider, model, **kw: (True, "ping"),
    )
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "openrouter",
                ("openai/gpt-5.1-codex",),
                ("openai/gpt-5.1-codex",),
                "openai/gpt-5.1-codex",
                False,
            ),
        ),
    )

    result = cli_runner.invoke(
        cli,
        [
            "auth",
            "set",
            "openrouter",
            "--api-key",
            "dummy-key",
            "--model",
            "openai/gpt-5.1-codex",
            "--use",
        ],
    )

    assert result.exit_code == 0
    auth = json.loads((tmp_path / "pi-agent" / "auth.json").read_text(encoding="utf-8"))
    settings = json.loads((tmp_path / "pi-agent" / "settings.json").read_text(encoding="utf-8"))
    assert auth["openrouter"]["type"] == "api_key"
    assert auth["openrouter"]["key"] == "dummy-key"
    assert settings["defaultProvider"] == "openrouter"
    assert settings["defaultModel"] == "openai/gpt-5.1-codex"


def test_auth_set_custom_provider_writes_models_json(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    monkeypatch.setattr(
        "syke.llm.pi_client.probe_pi_provider_connection",
        lambda provider, model, **kw: (True, "ping"),
    )
    _patch_catalog(monkeypatch, ())

    result = cli_runner.invoke(
        cli,
        [
            "auth",
            "set",
            "localproxy",
            "--base-url",
            "http://localhost:8000/v1",
            "--model",
            "local-model",
            "--use",
        ],
    )

    assert result.exit_code == 0
    models = json.loads((tmp_path / "pi-agent" / "models.json").read_text(encoding="utf-8"))
    assert models["providers"]["localproxy"]["api"] == "openai-completions"
    assert models["providers"]["localproxy"]["baseUrl"] == "http://localhost:8000/v1"
    assert models["providers"]["localproxy"]["models"] == [{"id": "local-model"}]


def test_auth_status_json_reads_pi_native_state(cli_runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    (tmp_path / "pi-agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pi-agent" / "auth.json").write_text(
        json.dumps({"openrouter": {"type": "api_key", "key": "dummy-key"}}),
        encoding="utf-8",
    )
    (tmp_path / "pi-agent" / "settings.json").write_text(
        json.dumps({"defaultProvider": "openrouter", "defaultModel": "openai/gpt-5.1-codex"}),
        encoding="utf-8",
    )
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "openrouter",
                ("openai/gpt-5.1-codex",),
                ("openai/gpt-5.1-codex",),
                "openai/gpt-5.1-codex",
                False,
            ),
            PiProviderCatalogEntry("openai", ("gpt-5.4",), (), "gpt-5.4", False),
        ),
    )

    result = cli_runner.invoke(cli, ["auth", "status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["active_provider"] == "openrouter"
    assert payload["selected_provider"]["id"] == "openrouter"
    assert payload["selected_provider"]["model"] == "openai/gpt-5.1-codex"


def test_setup_provider_choices_use_pi_catalog(monkeypatch) -> None:
    monkeypatch.setattr("syke.pi_state.get_default_provider", lambda: "openai")
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry("openai", ("gpt-5.4",), ("gpt-5.4",), "gpt-5.4", False),
            PiProviderCatalogEntry(
                "openai-codex",
                ("gpt-5.4",),
                (),
                "gpt-5.4",
                True,
                "ChatGPT Plus/Pro (Codex Subscription)",
            ),
        ),
    )

    choices = setup_provider_choices()

    assert [item["id"] for item in choices] == ["openai", "openai-codex"]
    assert choices[0]["active"] is True
    assert choices[1]["oauth"] is True


def test_oauth_setup_flow_does_not_prompt_for_custom_endpoint(monkeypatch) -> None:
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "openai-codex",
                ("gpt-5.4",),
                (),
                "gpt-5.4",
                True,
                "ChatGPT Plus/Pro (Codex Subscription)",
            ),
        ),
    )
    state = {"logged_in": False}
    monkeypatch.setattr(
        "syke.cli_support.auth_flow.evaluate_provider_readiness",
        lambda provider: ProviderReadiness(
            provider,
            state["logged_in"],
            "ready" if state["logged_in"] else "login required",
        ),
    )
    seen: dict[str, object] = {}

    def _login(provider: str, *, manual: bool = False) -> None:
        seen["provider"] = provider
        seen["manual"] = manual
        state["logged_in"] = True

    monkeypatch.setattr("syke.llm.pi_client.run_pi_oauth_login", _login)
    monkeypatch.setattr(
        "syke.llm.pi_client.probe_pi_provider_connection",
        lambda provider, model, **kw: (True, "ping"),
    )
    monkeypatch.setattr("syke.cli_support.auth_flow.term_menu_select", lambda *args, **kwargs: 0)

    with patch("click.prompt") as prompt_mock, patch("click.confirm", return_value=True):
        result = setup_pi_provider_flow("openai-codex")

    assert result is True
    prompt_mock.assert_not_called()
    assert seen == {"provider": "openai-codex", "manual": False}


def test_oauth_setup_flow_can_use_manual_redirect_mode(monkeypatch) -> None:
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "anthropic",
                ("claude-sonnet-4-6",),
                (),
                "claude-sonnet-4-6",
                True,
                "Anthropic (Claude Pro/Max)",
            ),
        ),
    )
    state = {"logged_in": False}
    monkeypatch.setattr(
        "syke.cli_support.auth_flow.evaluate_provider_readiness",
        lambda provider: ProviderReadiness(
            provider,
            state["logged_in"],
            "ready" if state["logged_in"] else "login required",
        ),
    )
    seen: dict[str, object] = {}

    def _login(provider: str, *, manual: bool = False) -> None:
        seen["provider"] = provider
        seen["manual"] = manual
        state["logged_in"] = True

    monkeypatch.setattr("syke.llm.pi_client.run_pi_oauth_login", _login)
    monkeypatch.setattr(
        "syke.llm.pi_client.probe_pi_provider_connection",
        lambda provider, model, **kw: (True, "ping"),
    )
    monkeypatch.setattr("syke.cli_support.auth_flow.term_menu_select", lambda *args, **kwargs: 0)

    with patch("click.prompt") as prompt_mock, patch("click.confirm", return_value=False):
        result = setup_pi_provider_flow("anthropic")

    assert result is True
    prompt_mock.assert_not_called()
    assert seen == {"provider": "anthropic", "manual": True}


def test_oauth_setup_flow_writes_to_isolated_pi_state(monkeypatch, tmp_path: Path) -> None:
    from syke.cli_support.auth_flow import setup_pi_provider_flow

    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "anthropic",
                ("claude-sonnet-4-6",),
                (),
                "claude-sonnet-4-6",
                True,
                "Anthropic (Claude Pro/Max)",
            ),
        ),
    )
    state = {"logged_in": False}

    monkeypatch.setattr(
        "syke.cli_support.auth_flow.evaluate_provider_readiness",
        lambda provider: ProviderReadiness(
            provider,
            state["logged_in"],
            "ready" if state["logged_in"] else "login required",
        ),
    )

    def _login(provider: str, *, manual: bool = False) -> None:
        del provider, manual
        state["logged_in"] = True

    monkeypatch.setattr("syke.llm.pi_client.run_pi_oauth_login", _login)
    monkeypatch.setattr(
        "syke.llm.pi_client.probe_pi_provider_connection",
        lambda provider, model, **kw: (True, "ping"),
    )
    monkeypatch.setattr("syke.cli_support.auth_flow.term_menu_select", lambda *args, **kwargs: 0)

    with patch("click.confirm", return_value=False):
        result = setup_pi_provider_flow("anthropic")

    assert result is True
    settings = json.loads((tmp_path / "pi-agent" / "settings.json").read_text(encoding="utf-8"))
    assert settings["defaultProvider"] == "anthropic"
    assert settings["defaultModel"] == "claude-sonnet-4-6"


def test_verify_setup_provider_connection_uses_alive_probe_prompt(monkeypatch, capsys) -> None:
    from syke.cli_support.auth_flow import verify_setup_provider_connection

    seen: dict[str, object] = {}

    def _probe(provider: str, model: str, *, timeout_seconds: int = 45, prompt: str = "x"):
        seen["provider"] = provider
        seen["model"] = model
        seen["timeout_seconds"] = timeout_seconds
        seen["prompt"] = prompt
        return True, "syke loaded"

    monkeypatch.setattr("syke.llm.pi_client.probe_pi_provider_connection", _probe)

    verify_setup_provider_connection("openai-codex", "gpt-5.4")

    capsys.readouterr()
    assert seen["provider"] == "openai-codex"
    assert seen["model"] == "gpt-5.4"
    # Prompt is generative with timestamp — just verify it mentions Syke and readiness
    assert "Syke" in seen["prompt"]
    assert "ready" in seen["prompt"]


def test_setup_provider_flow_back_from_auth_returns_to_provider_list(monkeypatch) -> None:
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry("openai", ("gpt-5.4",), (), "gpt-5.4", False),
            PiProviderCatalogEntry("openrouter", ("gpt-5.1",), (), "gpt-5.1", False),
        ),
    )
    monkeypatch.setattr("syke.cli_support.setup_support.run_setup_stage", lambda _label, fn: fn())
    selections = iter(
        [
            FlowChoice("back"),
            FlowChoice("continue"),
            FlowChoice("selected", "gpt-5.1"),
        ]
    )
    monkeypatch.setattr(
        "syke.cli_support.auth_flow.choose_provider_interactive",
        lambda choices=None: FlowChoice("selected", "openrouter"),
    )
    monkeypatch.setattr(
        "syke.cli_support.auth_flow.resolve_provider_auth_interactive",
        lambda provider_id: next(selections),
    )
    monkeypatch.setattr(
        "syke.cli_support.auth_flow.choose_provider_model_interactive",
        lambda provider_id: next(selections),
    )
    monkeypatch.setattr(
        "syke.cli_support.auth_flow.verify_provider_activation", lambda provider, model: None
    )
    monkeypatch.setattr("syke.pi_state.set_default_provider", lambda provider_id: None)
    monkeypatch.setattr("syke.pi_state.set_default_model", lambda model_id: None)

    result = run_interactive_provider_flow(initial_provider_id="openai")

    assert result == FlowChoice("selected", "openrouter")


def test_choose_activation_model_prefers_live_available_models(monkeypatch) -> None:
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "openrouter",
                ("model-a", "model-b"),
                ("model-b",),
                "model-a",
                False,
            ),
        ),
    )
    monkeypatch.setattr("syke.pi_state.get_default_model", lambda: None)
    monkeypatch.setattr("syke.cli_support.auth_flow.term_menu_select", lambda entries, **kwargs: 0)

    assert resolve_activation_model("openrouter") == "model-b"
    assert choose_provider_model_interactive("openrouter") == FlowChoice("selected", "model-b")


def test_describe_provider_uses_pi_catalog_and_agent_auth_signal(monkeypatch) -> None:
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "azure-openai-responses",
                ("gpt-5.4-mini",),
                ("gpt-5.4-mini",),
                "gpt-5.2",
                False,
                requires_base_url=False,
            ),
        ),
    )
    monkeypatch.setattr("syke.pi_state.get_credential", lambda provider_id: None)
    monkeypatch.setattr(
        "syke.pi_state.get_default_provider",
        lambda: "azure-openai-responses",
    )
    monkeypatch.setattr("syke.pi_state.get_default_model", lambda: "gpt-5.4-mini")
    monkeypatch.setattr("syke.llm.env.get_default_provider", lambda: "azure-openai-responses")
    monkeypatch.setattr("syke.llm.env.get_default_model", lambda: "gpt-5.4-mini")
    monkeypatch.setattr("syke.pi_state.get_provider_base_url", lambda provider_id: None)
    monkeypatch.setattr("syke.pi_state.get_provider_override", lambda provider_id: {})
    monkeypatch.setattr(
        "syke.cli_support.providers.evaluate_provider_readiness",
        lambda provider_id: ProviderReadiness(provider_id, True, "Pi runtime configured"),
    )

    info = describe_provider("azure-openai-responses")

    assert info["configured"] is True
    assert info["auth_source"] == "catalog only (not daemon-safe)"
    assert info["endpoint"] == "provider default"
    assert info["endpoint_source"] == "Pi built-in/default"


def test_auth_set_use_stops_when_live_probe_fails(cli_runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "openrouter",
                ("openai/gpt-5.1-codex",),
                ("openai/gpt-5.1-codex",),
                "openai/gpt-5.1-codex",
                False,
            ),
        ),
    )
    monkeypatch.setattr(
        "syke.llm.pi_client.probe_pi_provider_connection",
        lambda provider, model, **kw: (False, "fetch failed"),
    )

    result = cli_runner.invoke(
        cli,
        [
            "auth",
            "set",
            "openrouter",
            "--api-key",
            "dummy-key",
            "--model",
            "openai/gpt-5.1-codex",
            "--use",
        ],
    )

    assert result.exit_code == 4
    assert "Provider activation failed" in result.output
    settings_path = tmp_path / "pi-agent" / "settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "defaultProvider" not in settings
        assert "defaultModel" not in settings


def test_auth_use_runs_live_probe_before_switch(cli_runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    (tmp_path / "pi-agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pi-agent" / "auth.json").write_text(
        json.dumps({"openrouter": {"type": "api_key", "key": "dummy-key"}}),
        encoding="utf-8",
    )
    (tmp_path / "pi-agent" / "settings.json").write_text(
        json.dumps({"defaultModel": "openai/gpt-5.1-codex"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "openrouter",
                ("openai/gpt-5.1-codex",),
                ("openai/gpt-5.1-codex",),
                "openai/gpt-5.1-codex",
                False,
            ),
        ),
    )
    seen: dict[str, str] = {}

    def _probe(provider: str, model: str, **kwargs):
        seen["provider"] = provider
        seen["model"] = model
        return True, "ping"

    monkeypatch.setattr("syke.llm.pi_client.probe_pi_provider_connection", _probe)

    result = cli_runner.invoke(cli, ["auth", "use", "openrouter"])

    assert result.exit_code == 0
    assert seen == {"provider": "openrouter", "model": "openai/gpt-5.1-codex"}


def test_auth_use_probe_failure_does_not_mutate_existing_active_state(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    (tmp_path / "pi-agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pi-agent" / "auth.json").write_text(
        json.dumps({"openrouter": {"type": "api_key", "key": "dummy-key"}}),
        encoding="utf-8",
    )
    (tmp_path / "pi-agent" / "settings.json").write_text(
        json.dumps({"defaultProvider": "anthropic", "defaultModel": "claude-sonnet-4-6"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "openrouter",
                ("openai/gpt-5.1-codex",),
                ("openai/gpt-5.1-codex",),
                "openai/gpt-5.1-codex",
                False,
            ),
        ),
    )
    monkeypatch.setattr(
        "syke.llm.pi_client.probe_pi_provider_connection",
        lambda provider, model, **kw: (False, "fetch failed"),
    )

    result = cli_runner.invoke(cli, ["auth", "use", "openrouter"])

    assert result.exit_code == 4
    settings = json.loads((tmp_path / "pi-agent" / "settings.json").read_text(encoding="utf-8"))
    assert settings["defaultProvider"] == "anthropic"
    assert settings["defaultModel"] == "claude-sonnet-4-6"


def test_auth_login_use_runs_live_probe_before_switch(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "openai-codex",
                ("gpt-5.4",),
                ("gpt-5.4",),
                "gpt-5.4",
                True,
                "ChatGPT Plus/Pro (Codex Subscription)",
            ),
        ),
    )
    monkeypatch.setattr(
        "syke.llm.pi_client.run_pi_oauth_login", lambda provider, manual=False: None
    )
    seen: dict[str, str] = {}

    def _probe(provider: str, model: str, **kwargs):
        seen["provider"] = provider
        seen["model"] = model
        return True, "ping"

    monkeypatch.setattr("syke.llm.pi_client.probe_pi_provider_connection", _probe)

    with patch("click.confirm", return_value=True):
        result = cli_runner.invoke(cli, ["auth", "login", "openai-codex", "--use"])

    assert result.exit_code == 0
    assert seen == {"provider": "openai-codex", "model": "gpt-5.4"}


def test_auth_set_rejects_unpersisted_api_version(cli_runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "azure-openai-responses",
                ("gpt-5.4-mini",),
                (),
                "gpt-5.4-mini",
                False,
                requires_base_url=True,
            ),
        ),
    )

    result = cli_runner.invoke(
        cli,
        [
            "auth",
            "set",
            "azure-openai-responses",
            "--api-version",
            "2025-01-01-preview",
        ],
    )

    assert result.exit_code == 2
    assert "--api-version is not persisted" in result.output


def test_auth_set_unknown_provider_requires_custom_shape(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    _patch_catalog(monkeypatch, ())

    result = cli_runner.invoke(cli, ["auth", "set", "mystery-provider"])

    assert result.exit_code == 2
    assert "Unknown provider 'mystery-provider'" in result.output


def test_auth_use_not_ready_returns_auth_exit_code(cli_runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "openrouter",
                ("openai/gpt-5.1-codex",),
                (),
                "openai/gpt-5.1-codex",
                False,
            ),
        ),
    )

    result = cli_runner.invoke(cli, ["auth", "use", "openrouter"])

    assert result.exit_code == 3
    assert "No auth configured for 'openrouter'" in result.output


def test_auth_login_non_oauth_provider_is_usage_error(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "openrouter",
                ("openai/gpt-5.1-codex",),
                ("openai/gpt-5.1-codex",),
                "openai/gpt-5.1-codex",
                False,
            ),
        ),
    )

    result = cli_runner.invoke(cli, ["auth", "login", "openrouter"])

    assert result.exit_code == 2
    assert "does not advertise Pi-native OAuth login" in result.output


def test_auth_set_missing_runtime_returns_runtime_exit(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr(
        "syke.llm.pi_client.ensure_pi_binary",
        lambda: (_ for _ in ()).throw(RuntimeError("pi missing")),
    )

    result = cli_runner.invoke(cli, ["auth", "set", "openrouter", "--api-key", "dummy-key"])

    assert result.exit_code == 4
    assert "Pi runtime is unavailable" in result.output


def test_auth_unset_clears_active_provider_when_removing_active_credential(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    (tmp_path / "pi-agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pi-agent" / "auth.json").write_text(
        json.dumps({"anthropic": {"type": "oauth", "access": "token"}}),
        encoding="utf-8",
    )
    (tmp_path / "pi-agent" / "settings.json").write_text(
        json.dumps({"defaultProvider": "anthropic", "defaultModel": "claude-sonnet-4-6"}),
        encoding="utf-8",
    )

    result = cli_runner.invoke(cli, ["auth", "unset", "anthropic"])

    assert result.exit_code == 0
    settings = json.loads((tmp_path / "pi-agent" / "settings.json").read_text(encoding="utf-8"))
    assert "defaultProvider" not in settings
    assert "defaultModel" not in settings


def test_auth_unset_clears_stale_active_provider_without_stored_credential(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    (tmp_path / "pi-agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pi-agent" / "settings.json").write_text(
        json.dumps({"defaultProvider": "anthropic", "defaultModel": "claude-sonnet-4-6"}),
        encoding="utf-8",
    )

    result = cli_runner.invoke(cli, ["auth", "unset", "anthropic"])

    assert result.exit_code == 0
    assert "active provider" in result.output.lower()
    settings = json.loads((tmp_path / "pi-agent" / "settings.json").read_text(encoding="utf-8"))
    assert "defaultProvider" not in settings
    assert "defaultModel" not in settings


def test_auth_unset_removes_provider_override_without_credential(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    (tmp_path / "pi-agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pi-agent" / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "custom-provider": {"baseUrl": "https://example.com", "apiKey": "secret"}
                }
            }
        ),
        encoding="utf-8",
    )

    result = cli_runner.invoke(cli, ["auth", "unset", "custom-provider"])

    assert result.exit_code == 0
    models_path = tmp_path / "pi-agent" / "models.json"
    assert not models_path.exists()


def test_setup_source_inventory_orders_detected_sources_by_recency(
    monkeypatch, tmp_path: Path
) -> None:
    newer = tmp_path / "newer"
    older = tmp_path / "older"
    newer.mkdir()
    older.mkdir()
    (newer / "a.jsonl").write_text("{}", encoding="utf-8")
    (older / "b.jsonl").write_text("{}", encoding="utf-8")
    newer_time = 2_000_000_000
    older_time = 1_000_000_000
    os.utime(newer / "a.jsonl", (newer_time, newer_time))
    os.utime(older / "b.jsonl", (older_time, older_time))

    class _Root:
        def __init__(self, path: str) -> None:
            self.path = path
            self.include = ["*.jsonl"]

    class _Desc:
        def __init__(self, source: str, path: Path) -> None:
            self.source = source
            self.format_cluster = "jsonl"
            self.discover = type("D", (), {"roots": [_Root(str(path))]})()

    class _Registry:
        def active_harnesses(self):
            return [_Desc("older-source", older), _Desc("newer-source", newer)]

    monkeypatch.setattr(
        "syke.cli_support.setup_support.observe_registry", lambda user_id: _Registry()
    )

    sources = setup_source_inventory("test")

    assert [item["source"] for item in sources[:2]] == ["newer-source", "older-source"]


def test_launch_background_onboarding_uses_background_safe_launcher(
    monkeypatch, tmp_path: Path
) -> None:
    from syke.cli_commands.setup import _launch_background_onboarding

    log_path = tmp_path / "logs" / "onboarding.log"
    launcher_path = tmp_path / "bin" / "syke"
    runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("syke-managed",),
        target_path=tmp_path / "managed" / "syke",
        working_directory=tmp_path / "managed-root",
    )
    popen_calls: list[dict[str, object]] = []

    monkeypatch.setattr("syke.daemon.daemon.LOG_PATH", log_path)
    monkeypatch.setattr(
        "syke.runtime.locator.resolve_background_syke_runtime",
        lambda: runtime,
    )
    monkeypatch.setattr(
        "syke.runtime.locator.ensure_syke_launcher",
        lambda resolved_runtime: launcher_path,
    )
    monkeypatch.setattr(
        "syke.cli_commands.setup.subprocess.Popen",
        lambda cmd, **kwargs: popen_calls.append({"cmd": cmd, **kwargs}) or SimpleNamespace(),
    )

    result = _launch_background_onboarding(
        user_id="test",
        selected_sources=["claude-code"],
        start_daemon_after=True,
    )

    assert result == log_path
    assert len(popen_calls) == 1
    assert popen_calls[0]["cmd"] == [
        str(launcher_path),
        "--user",
        "test",
        "sync",
        "--source",
        "claude-code",
        "--start-daemon-after",
    ]
    assert popen_calls[0]["cwd"] == str(runtime.working_directory)


def test_setup_uses_selected_sources_from_interactive_choice(cli_runner, monkeypatch) -> None:
    inspect_payload = {
        "provider": {"configured": True, "id": "openrouter"},
        "sources": [
            {
                "source": "claude-code",
                "roots": ["~/.claude/projects"],
                "files_found": 10,
                "detected": True,
                "latest_seen": "2026-04-01T10:00:00+00:00",
            },
            {
                "source": "codex",
                "roots": ["~/.codex/sqlite"],
                "files_found": 5,
                "detected": True,
                "latest_seen": "2026-04-01T09:00:00+00:00",
            },
        ],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "daemon": {"platform": "Darwin", "installable": False, "detail": "blocked"},
    }
    with (
        patch("syke.cli_commands.setup.run_setup_stage", lambda _label, fn: fn()),
        patch("click.confirm", return_value=True),
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=inspect_payload),
        patch(
            "syke.cli_commands.setup.provider_payload",
            return_value={
                "configured": True,
                "id": "openrouter",
                "model": "openai/gpt-5.1-codex",
                "auth_source": "/tmp/auth.json",
                "model_source": "Pi settings defaultModel",
                "endpoint": "provider default",
                "endpoint_source": "Pi built-in/default",
            },
        ),
        patch(
            "syke.cli_commands.setup.ensure_setup_pi_runtime",
            return_value=("~/.syke/bin/pi", "0.64.0"),
        ),
        patch("syke.cli_commands.setup.verify_setup_provider_connection"),
        patch("syke.cli_commands.setup.choose_setup_sources_interactive", return_value=["codex"]),
        patch(
            "syke.cli_commands.setup._launch_background_onboarding",
            return_value=Path("/tmp/onboarding.log"),
        ) as launch_onboarding,
    ):
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "setup", "--skip-daemon"],
            input="y\n",
        )

    assert result.exit_code == 0
    launch_onboarding.assert_called_once_with(
        user_id="test",
        selected_sources=["codex"],
        start_daemon_after=False,
    )


def test_setup_uses_source_flag_subset(cli_runner, monkeypatch) -> None:
    inspect_payload = {
        "provider": {"configured": True, "id": "openrouter"},
        "sources": [
            {"source": "claude-code", "roots": [], "files_found": 10, "detected": True},
            {"source": "codex", "roots": [], "files_found": 5, "detected": True},
        ],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "daemon": {"platform": "Darwin", "installable": False, "detail": "blocked"},
    }
    with (
        patch("syke.cli_commands.setup.run_setup_stage", lambda _label, fn: fn()),
        patch("click.confirm", return_value=True),
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=inspect_payload),
        patch(
            "syke.cli_commands.setup.provider_payload",
            return_value={
                "configured": True,
                "id": "openrouter",
                "model": "openai/gpt-5.1-codex",
                "auth_source": "/tmp/auth.json",
                "model_source": "Pi settings defaultModel",
                "endpoint": "provider default",
                "endpoint_source": "Pi built-in/default",
            },
        ),
        patch(
            "syke.cli_commands.setup.ensure_setup_pi_runtime",
            return_value=("~/.syke/bin/pi", "0.64.0"),
        ),
        patch("syke.cli_commands.setup.verify_setup_provider_connection"),
        patch(
            "syke.cli_commands.setup._launch_background_onboarding",
            return_value=Path("/tmp/onboarding.log"),
        ) as launch_onboarding,
    ):
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "setup", "--skip-daemon", "--source", "claude-code", "--yes"],
        )

    assert result.exit_code == 0
    launch_onboarding.assert_called_once_with(
        user_id="test",
        selected_sources=["claude-code"],
        start_daemon_after=False,
    )


def test_setup_renders_consistent_summary_lines(cli_runner, monkeypatch) -> None:
    inspect_payload = {
        "provider": {"configured": True, "id": "openrouter"},
        "sources": [
            {
                "source": "claude-code",
                "roots": ["~/.claude/projects"],
                "files_found": 10,
                "detected": True,
            },
            {"source": "codex", "roots": ["~/.codex"], "files_found": 5, "detected": True},
        ],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "daemon": {"platform": "Darwin", "installable": False, "detail": "blocked"},
    }
    with (
        patch("syke.cli_commands.setup.run_setup_stage", lambda _label, fn: fn()),
        patch("click.confirm", return_value=True),
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=inspect_payload),
        patch(
            "syke.cli_commands.setup.provider_payload",
            return_value={
                "configured": True,
                "id": "openrouter",
                "model": "openai/gpt-5.1-codex",
                "auth_source": "/tmp/auth.json",
                "model_source": "Pi settings defaultModel",
                "endpoint": "provider default",
                "endpoint_source": "Pi built-in/default",
            },
        ),
        patch(
            "syke.cli_commands.setup.ensure_setup_pi_runtime",
            return_value=("~/.syke/bin/pi", "0.64.0"),
        ),
        patch("syke.cli_commands.setup.verify_setup_provider_connection"),
        patch(
            "syke.cli_commands.setup.choose_setup_sources_interactive", return_value=["claude-code"]
        ),
        patch(
            "syke.cli_commands.setup._launch_background_onboarding",
            return_value=Path("/tmp/onboarding.log"),
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--skip-daemon"], input="y\n")

    assert result.exit_code == 0
    assert "claude-code" in result.output
    assert "Setup complete" in result.output
    assert "syke ask" in result.output
    assert "syke record" in result.output
    assert "tail -f /tmp/onboarding.log" in result.output


def test_setup_starts_background_sync_after_onboarding_when_enabled(
    cli_runner, monkeypatch
) -> None:
    inspect_payload = {
        "provider": {"configured": True, "id": "openrouter"},
        "sources": [
            {
                "source": "claude-code",
                "roots": ["~/.claude/projects"],
                "files_found": 10,
                "detected": True,
            },
        ],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "daemon": {"platform": "Darwin", "installable": False, "detail": "blocked"},
    }

    with (
        patch("syke.cli_commands.setup.run_setup_stage", lambda _label, fn: fn()),
        patch("click.confirm", return_value=True),
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=inspect_payload),
        patch(
            "syke.cli_commands.setup.provider_payload",
            return_value={
                "configured": True,
                "id": "openrouter",
                "model": "openai/gpt-5.1-codex",
                "auth_source": "/tmp/auth.json",
                "model_source": "Pi settings defaultModel",
                "endpoint": "provider default",
                "endpoint_source": "Pi built-in/default",
            },
        ),
        patch(
            "syke.cli_commands.setup.ensure_setup_pi_runtime",
            return_value=("~/.syke/bin/pi", "0.64.0"),
        ),
        patch("syke.cli_commands.setup.verify_setup_provider_connection"),
        patch(
            "syke.cli_commands.setup.choose_setup_sources_interactive", return_value=["claude-code"]
        ),
        patch(
            "syke.cli_commands.setup._launch_background_onboarding",
            return_value=Path("/tmp/onboarding.log"),
        ) as launch_onboarding,
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup"], input="y\n")

    assert result.exit_code == 0
    launch_onboarding.assert_called_once_with(
        user_id="test",
        selected_sources=["claude-code"],
        start_daemon_after=True,
    )
    assert "background sync starts after onboarding" in result.output


def test_setup_always_verifies_provider_even_after_interactive_selection(
    monkeypatch, capsys
) -> None:
    from syke.cli_commands.setup import setup as setup_command

    inspect_payload = {
        "provider": {"configured": False, "id": None},
        "sources": [
            {
                "source": "claude-code",
                "roots": ["~/.claude/projects"],
                "files_found": 10,
                "detected": True,
            },
        ],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "daemon": {"platform": "Darwin", "installable": False, "detail": "blocked"},
    }

    with (
        patch("click.confirm", return_value=True),
        patch("syke.cli_commands.setup.sys.stdin.isatty", return_value=True),
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=inspect_payload),
        patch(
            "syke.cli_commands.setup.provider_payload",
            return_value={
                "configured": True,
                "id": "kimi-coding",
                "model": "k2p5",
                "auth_source": "/tmp/auth.json",
                "model_source": "Pi settings defaultModel",
                "endpoint": "provider default",
                "endpoint_source": "Pi built-in/default",
            },
        ),
        patch(
            "syke.cli_commands.setup.ensure_setup_pi_runtime",
            return_value=("~/.syke/bin/pi", "0.64.0"),
        ),
        patch(
            "syke.cli_commands.setup.run_interactive_provider_flow",
            return_value=FlowChoice("selected", "kimi-coding"),
        ),
        patch(
            "syke.cli_commands.setup.choose_setup_sources_interactive", return_value=["claude-code"]
        ),
        patch(
            "syke.cli_commands.setup._launch_background_onboarding",
            return_value=Path("/tmp/onboarding.log"),
        ),
        patch(
            "syke.cli_commands.setup.verify_setup_provider_connection",
            return_value="Ready to go!",
        ) as verify_provider,
    ):
        ctx = click.Context(setup_command, obj={"user": "test", "provider": None})
        with ctx:
            setup_command.callback(
                yes=False,
                use_json=False,
                skip_daemon=True,
                agent_mode=False,
                selected_sources_cli=(),
            )

    output = capsys.readouterr().out
    # Interactive flow handles its own verification — setup doesn't double-verify
    verify_provider.assert_not_called()
    assert "Setup complete" in output


def test_setup_kept_provider_path_still_verifies_before_onboarding(cli_runner, monkeypatch) -> None:
    inspect_payload = {
        "provider": {"configured": True, "id": "openrouter"},
        "sources": [
            {
                "source": "claude-code",
                "roots": ["~/.claude/projects"],
                "files_found": 10,
                "detected": True,
            },
        ],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "daemon": {"platform": "Darwin", "installable": False, "detail": "blocked"},
    }

    with (
        patch("click.confirm", return_value=True),
        patch("click.testing._NamedTextIOWrapper.isatty", return_value=False),
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=inspect_payload),
        patch(
            "syke.cli_commands.setup.provider_payload",
            return_value={
                "configured": True,
                "id": "openrouter",
                "model": "openai/gpt-5.1-codex",
                "auth_source": "/tmp/auth.json",
                "model_source": "Pi settings defaultModel",
                "endpoint": "provider default",
                "endpoint_source": "Pi built-in/default",
            },
        ),
        patch(
            "syke.cli_commands.setup.ensure_setup_pi_runtime",
            return_value=("~/.syke/bin/pi", "0.64.0"),
        ),
        patch(
            "syke.cli_commands.setup.choose_setup_sources_interactive", return_value=["claude-code"]
        ),
        patch(
            "syke.cli_commands.setup._launch_background_onboarding",
            return_value=Path("/tmp/onboarding.log"),
        ),
        patch(
            "syke.cli_commands.setup.verify_setup_provider_connection", return_value="syke loaded"
        ) as verify_provider,
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--skip-daemon"], input="y\n")

    assert result.exit_code == 0
    verify_provider.assert_called_once_with("openrouter", "openai/gpt-5.1-codex")
    assert "openrouter/openai/gpt-5.1-codex connected" in result.output


def test_setup_reports_daemon_starting_when_process_is_up_but_ipc_is_not_ready(
    cli_runner, monkeypatch
) -> None:
    inspect_payload = {
        "provider": {"configured": True, "id": "openrouter"},
        "sources": [],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "daemon": {"platform": "Darwin", "installable": True, "running": False, "detail": "ready"},
    }

    with (
        patch("syke.cli_commands.setup.run_setup_stage", lambda _label, fn: fn()),
        patch("click.confirm", return_value=True),
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=inspect_payload),
        patch(
            "syke.cli_commands.setup.provider_payload",
            return_value={
                "configured": True,
                "id": "openrouter",
                "model": "openai/gpt-5.1-codex",
                "auth_source": "/tmp/auth.json",
                "model_source": "Pi settings defaultModel",
                "endpoint": "provider default",
                "endpoint_source": "Pi built-in/default",
            },
        ),
        patch(
            "syke.cli_commands.setup.ensure_setup_pi_runtime",
            return_value=("~/.syke/bin/pi", "0.64.0"),
        ),
        patch(
            "syke.cli_commands.setup.verify_setup_provider_connection", return_value="syke loaded"
        ),
        patch(
            "syke.cli_commands.setup._launch_background_onboarding",
            return_value=Path("/tmp/onboarding.log"),
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--yes"])

    assert result.exit_code == 0
    assert "background sync starts after onboarding" in result.output
