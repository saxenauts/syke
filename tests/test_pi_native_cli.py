from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from syke.cli import _FlowChoice, _setup_provider_choices, cli
from syke.llm.env import ProviderReadiness
from syke.llm.pi_client import PiProviderCatalogEntry


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
        lambda provider, model, timeout_seconds=45: (True, "ping"),
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


def test_auth_set_custom_provider_writes_models_json(cli_runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    monkeypatch.setattr(
        "syke.llm.pi_client.probe_pi_provider_connection",
        lambda provider, model, timeout_seconds=45: (True, "ping"),
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

    choices = _setup_provider_choices()

    assert [item["id"] for item in choices] == ["openai", "openai-codex"]
    assert choices[0]["active"] is True
    assert choices[1]["oauth"] is True


def test_oauth_setup_flow_does_not_prompt_for_custom_endpoint(monkeypatch) -> None:
    from syke.cli import _setup_pi_provider_flow

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
        "syke.cli.evaluate_provider_readiness",
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
        lambda provider, model, timeout_seconds=45: (True, "ping"),
    )
    monkeypatch.setattr("syke.cli._term_menu_select", lambda *args, **kwargs: 0)

    with patch("click.prompt") as prompt_mock, patch("click.confirm", return_value=True):
        result = _setup_pi_provider_flow("openai-codex")

    assert result is True
    prompt_mock.assert_not_called()
    assert seen == {"provider": "openai-codex", "manual": False}


def test_oauth_setup_flow_can_use_manual_redirect_mode(monkeypatch) -> None:
    from syke.cli import _setup_pi_provider_flow

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
        "syke.cli.evaluate_provider_readiness",
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
        lambda provider, model, timeout_seconds=45: (True, "ping"),
    )
    monkeypatch.setattr("syke.cli._term_menu_select", lambda *args, **kwargs: 0)

    with patch("click.prompt") as prompt_mock, patch("click.confirm", return_value=False):
        result = _setup_pi_provider_flow("anthropic")

    assert result is True
    prompt_mock.assert_not_called()
    assert seen == {"provider": "anthropic", "manual": True}


def test_setup_provider_flow_back_from_auth_returns_to_provider_list(monkeypatch) -> None:
    from syke.cli import _run_interactive_provider_flow

    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry("openai", ("gpt-5.4",), (), "gpt-5.4", False),
            PiProviderCatalogEntry("openrouter", ("gpt-5.1",), (), "gpt-5.1", False),
        ),
    )
    monkeypatch.setattr("syke.cli._run_setup_stage", lambda _label, fn: fn())
    selections = iter(
        [
            _FlowChoice("back"),
            _FlowChoice("continue"),
            _FlowChoice("selected", "gpt-5.1"),
        ]
    )
    monkeypatch.setattr(
        "syke.cli._choose_provider_interactive",
        lambda choices=None: _FlowChoice("selected", "openrouter"),
    )
    monkeypatch.setattr(
        "syke.cli._resolve_provider_auth_interactive",
        lambda provider_id: next(selections),
    )
    monkeypatch.setattr(
        "syke.cli._choose_provider_model_interactive",
        lambda provider_id: next(selections),
    )
    monkeypatch.setattr("syke.cli._verify_provider_activation", lambda provider, model: None)
    monkeypatch.setattr("syke.pi_state.set_default_provider", lambda provider_id: None)
    monkeypatch.setattr("syke.pi_state.set_default_model", lambda model_id: None)

    result = _run_interactive_provider_flow(initial_provider_id="openai")

    assert result == _FlowChoice("selected", "openrouter")


def test_choose_activation_model_prefers_live_available_models(monkeypatch) -> None:
    from syke.cli import _choose_provider_model_interactive, _resolve_activation_model

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
    monkeypatch.setattr("syke.cli._term_menu_select", lambda entries, **kwargs: 0)

    assert _resolve_activation_model("openrouter") == "model-b"
    assert _choose_provider_model_interactive("openrouter") == _FlowChoice("selected", "model-b")


def test_describe_provider_uses_pi_catalog_and_agent_auth_signal(monkeypatch) -> None:
    from syke.cli import _describe_provider

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
        "syke.cli.evaluate_provider_readiness",
        lambda provider_id: ProviderReadiness(provider_id, True, "Pi runtime configured"),
    )

    info = _describe_provider("azure-openai-responses")

    assert info["configured"] is True
    assert info["auth_source"] == "Pi agent auth/config"
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
        lambda provider, model, timeout_seconds=45: (False, "fetch failed"),
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

    assert result.exit_code == 1
    assert "Provider activation failed" in result.output
    settings_path = tmp_path / "pi-agent" / "settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "defaultProvider" not in settings
        assert "defaultModel" not in settings


def test_auth_use_runs_live_probe_before_switch(cli_runner, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    (tmp_path / "pi-agent").mkdir(parents=True, exist_ok=True)
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

    def _probe(provider: str, model: str, timeout_seconds: int = 45):
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
        lambda provider, model, timeout_seconds=45: (False, "fetch failed"),
    )

    result = cli_runner.invoke(cli, ["auth", "use", "openrouter"])

    assert result.exit_code == 1
    settings = json.loads((tmp_path / "pi-agent" / "settings.json").read_text(encoding="utf-8"))
    assert settings["defaultProvider"] == "anthropic"
    assert settings["defaultModel"] == "claude-sonnet-4-6"


def test_auth_login_use_runs_live_probe_before_switch(cli_runner, monkeypatch, tmp_path: Path) -> None:
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

    def _probe(provider: str, model: str, timeout_seconds: int = 45):
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

    assert result.exit_code == 1
    assert "--api-version is not persisted" in result.output


def test_setup_source_inventory_orders_detected_sources_by_recency(monkeypatch, tmp_path: Path) -> None:
    from syke.cli import _setup_source_inventory

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

    monkeypatch.setattr("syke.cli._observe_registry", lambda user_id: _Registry())

    sources = _setup_source_inventory("test")

    assert [item["source"] for item in sources[:2]] == ["newer-source", "older-source"]


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
    mock_db = type(
        "DB",
        (),
        {
            "count_events": lambda self, user_id, source=None: 0,
            "get_memex": lambda self, user_id: None,
            "close": lambda self: None,
        },
    )()

    with (
        patch("syke.cli._run_setup_stage", lambda _label, fn: fn()),
        patch("click.confirm", return_value=True),
        patch("syke.cli._build_setup_inspect_payload", return_value=inspect_payload),
        patch(
            "syke.cli._provider_payload",
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
        patch("syke.cli._ensure_setup_pi_runtime", return_value=("~/.syke/bin/pi", "0.64.0")),
        patch("syke.cli._verify_setup_provider_connection"),
        patch("syke.cli._choose_setup_sources_interactive", return_value=["codex"]),
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.observe.bootstrap.ensure_adapters", return_value=[] ) as ensure_adapters,
        patch("syke.cli._observe_registry") as observe_registry,
    ):
        observe_registry.return_value.active_harnesses.return_value = []
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "setup", "--skip-daemon"],
            input="y\n",
        )

    assert result.exit_code == 0
    ensure_adapters.assert_called_once_with("test", sources=["codex"])


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
    mock_db = type(
        "DB",
        (),
        {
            "count_events": lambda self, user_id, source=None: 0,
            "get_memex": lambda self, user_id: None,
            "close": lambda self: None,
        },
    )()

    with (
        patch("syke.cli._run_setup_stage", lambda _label, fn: fn()),
        patch("click.confirm", return_value=True),
        patch("syke.cli._build_setup_inspect_payload", return_value=inspect_payload),
        patch(
            "syke.cli._provider_payload",
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
        patch("syke.cli._ensure_setup_pi_runtime", return_value=("~/.syke/bin/pi", "0.64.0")),
        patch("syke.cli._verify_setup_provider_connection"),
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.observe.bootstrap.ensure_adapters", return_value=[] ) as ensure_adapters,
        patch("syke.cli._observe_registry") as observe_registry,
    ):
        observe_registry.return_value.active_harnesses.return_value = []
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "setup", "--skip-daemon", "--source", "claude-code", "--yes"],
        )

    assert result.exit_code == 0
    ensure_adapters.assert_called_once_with("test", sources=["claude-code"])


def test_setup_renders_consistent_summary_lines(cli_runner, monkeypatch) -> None:
    inspect_payload = {
        "provider": {"configured": True, "id": "openrouter"},
        "sources": [
            {"source": "claude-code", "roots": ["~/.claude/projects"], "files_found": 10, "detected": True},
            {"source": "codex", "roots": ["~/.codex"], "files_found": 5, "detected": True},
        ],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "daemon": {"platform": "Darwin", "installable": False, "detail": "blocked"},
    }
    mock_db = type(
        "DB",
        (),
        {
            "count_events": lambda self, user_id, source=None: 0,
            "get_memex": lambda self, user_id: None,
            "close": lambda self: None,
        },
    )()

    bootstrap_results = [
        SimpleNamespace(source="claude-code", status="generated", detail="strict validation passed"),
    ]

    with (
        patch("syke.cli._run_setup_stage", lambda _label, fn: fn()),
        patch("click.confirm", return_value=True),
        patch("syke.cli._build_setup_inspect_payload", return_value=inspect_payload),
        patch(
            "syke.cli._provider_payload",
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
        patch("syke.cli._ensure_setup_pi_runtime", return_value=("~/.syke/bin/pi", "0.64.0")),
        patch("syke.cli._verify_setup_provider_connection"),
        patch("syke.cli._choose_setup_sources_interactive", return_value=["claude-code"]),
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.observe.bootstrap.ensure_adapters", return_value=bootstrap_results),
        patch("syke.cli._observe_registry") as observe_registry,
    ):
        observe_registry.return_value.active_harnesses.return_value = []
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--skip-daemon"], input="y\n")

    assert result.exit_code == 0
    assert "selected: claude-code" in result.output
    assert "skipped: codex" in result.output
    assert "claude-code: connected" in result.output
    assert "strict validation passed" in result.output
    assert "sources selected: claude-code" in result.output
