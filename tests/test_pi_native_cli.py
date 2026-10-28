from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from syke.entrypoint import cli
from syke.llm.pi_client import PiProviderCatalogEntry
from syke.onboarding import read_onboarding_state, write_onboarding_state
from syke.pi_state import get_default_model, get_default_provider
from syke.runtime.locator import SykeRuntimeDescriptor
from syke.source_selection import get_selected_sources, set_selected_sources


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


def test_auth_activation_commits_only_after_successful_probe(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    (tmp_path / "pi-agent").mkdir(parents=True)
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
    failed = cli_runner.invoke(
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

    assert failed.exit_code == 4
    assert (get_default_provider(), get_default_model()) == (
        "anthropic",
        "claude-sonnet-4-6",
    )

    failed_use = cli_runner.invoke(cli, ["auth", "use", "openrouter"])
    assert failed_use.exit_code == 4
    assert (get_default_provider(), get_default_model()) == (
        "anthropic",
        "claude-sonnet-4-6",
    )

    seen: dict[str, str] = {}

    def _probe(provider: str, model: str, **kwargs):
        seen["provider"] = provider
        seen["model"] = model
        return True, "ping"

    monkeypatch.setattr("syke.llm.pi_client.probe_pi_provider_connection", _probe)

    activated = cli_runner.invoke(cli, ["auth", "use", "openrouter"])

    assert activated.exit_code == 0
    assert seen == {"provider": "openrouter", "model": "openai/gpt-5.1-codex"}
    assert (get_default_provider(), get_default_model()) == (
        "openrouter",
        "openai/gpt-5.1-codex",
    )


def test_auth_login_reloads_catalog_and_activates_after_probe(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    entry = PiProviderCatalogEntry(
        "openai-codex",
        ("gpt-5.4",),
        ("gpt-5.4",),
        "gpt-5.4",
        True,
        "ChatGPT Plus/Pro (Codex Subscription)",
    )
    catalogs = iter(((),))
    monkeypatch.setattr(
        "syke.llm.pi_client.get_pi_provider_catalog",
        lambda: next(catalogs, (entry,)),
    )
    login = Mock()
    monkeypatch.setattr("syke.llm.pi_client.run_pi_oauth_login", login)
    seen: dict[str, str] = {}

    def _probe(provider: str, model: str, **kwargs):
        seen["provider"] = provider
        seen["model"] = model
        return True, "ping"

    monkeypatch.setattr("syke.llm.pi_client.probe_pi_provider_connection", _probe)

    result = cli_runner.invoke(cli, ["auth", "login", "openai-codex", "--use"])

    assert result.exit_code == 0
    login.assert_called_once_with("openai-codex", method="auto")
    assert seen == {"provider": "openai-codex", "model": "gpt-5.4"}
    assert (get_default_provider(), get_default_model()) == ("openai-codex", "gpt-5.4")


def test_auth_login_passes_device_code_override_to_pi(
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
                (),
                "gpt-5.4",
                True,
            ),
        ),
    )
    login = Mock()
    monkeypatch.setattr("syke.llm.pi_client.run_pi_oauth_login", login)

    result = cli_runner.invoke(
        cli,
        ["auth", "login", "openai-codex", "--method", "device-code"],
    )

    assert result.exit_code == 0
    login.assert_called_once_with("openai-codex", method="device-code")


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


def test_auth_use_rejected_key_returns_auth_exit_and_replace_command(
    cli_runner, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setattr("syke.llm.pi_client.ensure_pi_binary", lambda: str(tmp_path / "pi"))
    _patch_catalog(
        monkeypatch,
        (
            PiProviderCatalogEntry(
                "opencode-go",
                ("deepseek-v4.1-flash",),
                ("deepseek-v4.1-flash",),
                "deepseek-v4.1-flash",
                False,
            ),
        ),
    )
    monkeypatch.setattr(
        "syke.llm.pi_client.probe_pi_provider_connection",
        lambda provider, model, **kw: (
            False,
            '401: {"type":"authentication_error","code":"api_key_expired"}',
        ),
    )
    assert cli_runner.invoke(cli, ["auth", "set", "opencode-go", "--api-key", "old"]).exit_code == 0

    result = cli_runner.invoke(cli, ["auth", "use", "opencode-go"])

    assert result.exit_code == 3
    assert "syke auth set opencode-go --api-key" in result.output


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


def test_auth_unset_coordinates_active_credential_and_override_state(
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

    active_result = cli_runner.invoke(cli, ["auth", "unset", "anthropic"])
    override_result = cli_runner.invoke(cli, ["auth", "unset", "custom-provider"])

    assert active_result.exit_code == 0
    assert override_result.exit_code == 0
    settings = json.loads((tmp_path / "pi-agent" / "settings.json").read_text(encoding="utf-8"))
    assert "defaultProvider" not in settings
    assert "defaultModel" not in settings
    assert json.loads((tmp_path / "pi-agent" / "auth.json").read_text(encoding="utf-8")) == {}
    models_path = tmp_path / "pi-agent" / "models.json"
    assert json.loads(models_path.read_text(encoding="utf-8")) == {"providers": {}}


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
    run_calls: list[dict[str, object]] = []

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
        "syke.cli_commands.setup.subprocess.run",
        lambda cmd, **kwargs: run_calls.append({"cmd": cmd, **kwargs}) or SimpleNamespace(),
    )

    result = _launch_background_onboarding(
        user_id="test",
        selected_sources=["claude-code"],
    )

    assert result == log_path
    assert len(run_calls) == 1
    assert run_calls[0]["cmd"] == [
        str(launcher_path),
        "--user",
        "test",
        "sync",
        "--source",
        "claude-code",
        "--start-daemon-after",
    ]
    assert run_calls[0]["cwd"] == str(runtime.working_directory)
    assert run_calls[0]["timeout"] == 60
    assert run_calls[0]["check"] is True


def test_sync_source_flag_persists_and_forwards_selection(cli_runner) -> None:
    fake_db = SimpleNamespace(close=lambda: None)
    write_onboarding_state(
        "test",
        selected_sources=("codex",),
        total_files=5,
        estimated_minutes=3,
        estimate_method="test",
        mode="manual",
    )
    with (
        patch("syke.cli_commands.maintenance.get_db", return_value=fake_db),
        patch(
            "syke.llm.backends.pi_synthesis.pi_synthesize",
            return_value={
                "status": "completed",
                "memex_updated": True,
                "duration_ms": 1234,
                "session_id": "session-1",
                "session_file": "/protected/session-1.jsonl",
                "num_turns": 3,
                "model": "gpt-test",
                "cost_usd": 0.01,
                "error": None,
            },
        ) as synthesize,
    ):
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "sync", "--source", "codex", "--json"],
        )

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["selected_sources"] == ["codex"]
    assert parsed["duration_ms"] == 1234
    assert parsed["session_id"] == "session-1"
    assert parsed["session_file"] == "/protected/session-1.jsonl"
    assert "tool_calls" not in parsed
    assert parsed["next_steps"] == ["syke memex", "syke status --json", "syke web --open"]
    onboarding = read_onboarding_state("test")
    assert onboarding is not None
    assert onboarding["status"] == "first_synthesis_completed"
    assert get_selected_sources("test") == ("codex",)
    synthesize.assert_called_once_with(
        fake_db,
        "test",
        selected_sources=("codex",),
    )


def test_sync_without_source_uses_persisted_selection(cli_runner) -> None:
    set_selected_sources("test", ("claude-code",))
    fake_db = SimpleNamespace(close=lambda: None)
    with (
        patch("syke.cli_commands.maintenance.get_db", return_value=fake_db),
        patch(
            "syke.llm.backends.pi_synthesis.pi_synthesize",
            return_value={"status": "completed", "memex_updated": False, "error": None},
        ) as synthesize,
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "sync", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["selected_sources"] == ["claude-code"]
    synthesize.assert_called_once_with(
        fake_db,
        "test",
        selected_sources=("claude-code",),
    )


def test_sync_json_preserves_noncompleted_result_classification(cli_runner) -> None:
    cases = (
        ({"status": "failed", "error": "runtime failed"}, None),
        (
            {
                "status": "blocked",
                "reason": "setup_blocked",
                "error": "No Pi model is configured",
            },
            "setup_blocked",
        ),
    )
    for synthesis_result, expected_reason in cases:
        fake_db = SimpleNamespace(close=lambda: None)
        with (
            patch("syke.cli_commands.maintenance.get_db", return_value=fake_db),
            patch(
                "syke.llm.backends.pi_synthesis.pi_synthesize",
                return_value={"memex_updated": False, **synthesis_result},
            ),
        ):
            result = cli_runner.invoke(cli, ["--user", "test", "sync", "--json"])

        parsed = json.loads(result.output)
        assert result.exit_code == 1
        assert parsed["ok"] is False
        assert parsed["status"] == synthesis_result["status"]
        assert parsed["reason"] == expected_reason
        assert parsed["error"] == synthesis_result["error"]


def test_sync_start_daemon_after_persists_selected_sources(cli_runner) -> None:
    with (
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("syke.daemon.daemon.install_and_start") as install_and_start,
        patch(
            "syke.cli_support.daemon_state.wait_for_daemon_startup",
            return_value={
                "platform": "Darwin",
                "running": True,
                "registered": True,
                "ipc": {"ok": True, "detail": "ready"},
            },
        ),
    ):
        result = cli_runner.invoke(
            cli,
            [
                "--user",
                "test",
                "sync",
                "--source",
                "codex",
                "--start-daemon-after",
                "--json",
            ],
        )

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["status"] == "daemon_started"
    assert parsed["selected_sources"] == ["codex"]
    assert parsed["daemon_readiness"]["ipc"]["ok"] is True
    assert get_selected_sources("test") == ("codex",)
    install_and_start.assert_called_once_with("test")


def test_sync_start_daemon_after_fails_when_readiness_unconfirmed(cli_runner) -> None:
    with (
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("syke.daemon.daemon.install_and_start"),
        patch(
            "syke.cli_support.daemon_state.wait_for_daemon_startup",
            return_value={
                "platform": "Darwin",
                "running": True,
                "registered": True,
                "ipc": {"ok": False, "detail": "socket missing"},
            },
        ),
    ):
        result = cli_runner.invoke(
            cli, ["--user", "test", "sync", "--start-daemon-after", "--json"]
        )

    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["ok"] is False
    assert parsed["status"] == "daemon_start_unconfirmed"
    assert parsed["daemon_readiness"]["ipc"]["detail"] == "socket missing"
