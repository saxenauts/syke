"""Tests for Syke's Pi-native provider resolution and workspace config."""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from syke.llm.env import build_pi_runtime_env, evaluate_provider_readiness, resolve_provider
from syke.llm.pi_client import PiProviderCatalogEntry
from syke.runtime.pi_settings import configure_pi_workspace


def _catalog(*entries: PiProviderCatalogEntry) -> tuple[PiProviderCatalogEntry, ...]:
    return entries


class TestResolveProvider:
    def test_cli_flag_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "zai")
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry("openrouter", ("gpt-5",), ("gpt-5",), "gpt-5", False),
                PiProviderCatalogEntry("zai", ("glm-5",), ("glm-5",), "glm-5", False),
            ),
        )
        spec = resolve_provider(cli_provider="openrouter")
        assert spec.id == "openrouter"

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "openrouter")
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry("openrouter", ("gpt-5",), ("gpt-5",), "gpt-5", False)
            ),
        )
        spec = resolve_provider()
        assert spec.id == "openrouter"

    def test_unknown_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("syke.llm.env.get_pi_provider_catalog", lambda: ())
        with pytest.raises(ValueError, match="Unknown provider"):
            _ = resolve_provider(cli_provider="nonexistent")

    def test_no_provider_configured_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SYKE_PROVIDER", raising=False)
        monkeypatch.setattr("syke.llm.env.get_default_provider", lambda: None)
        with pytest.raises(RuntimeError, match="No provider configured"):
            _ = resolve_provider()


class TestProviderReadiness:
    def test_provider_with_available_models_is_not_ready_without_persisted_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry(
                    "openrouter",
                    ("openai/gpt-5.1-codex",),
                    ("openai/gpt-5.1-codex",),
                    "openai/gpt-5.1-codex",
                    False,
                )
            ),
        )
        monkeypatch.setattr("syke.llm.env.get_default_model", lambda: None)
        status = evaluate_provider_readiness("openrouter")

        assert not status.ready
        assert "No auth configured for 'openrouter'" in status.detail

    def test_azure_requires_endpoint_before_being_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry(
                    "azure-openai-responses",
                    ("gpt-5.4-mini",),
                    ("gpt-5.4-mini",),
                    "gpt-5.2",
                    False,
                    requires_base_url=True,
                )
            ),
        )

        status = evaluate_provider_readiness("azure-openai-responses")

        assert not status.ready
        assert "Configure a base URL/resource endpoint" in status.detail

    def test_provider_with_catalog_models_still_requires_persisted_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry(
                    "future-provider",
                    ("gpt-5.4-mini",),
                    ("gpt-5.4-mini",),
                    "gpt-5.2",
                    False,
                    requires_base_url=False,
                )
            ),
        )

        status = evaluate_provider_readiness("future-provider")

        assert not status.ready
        assert "No auth configured for 'future-provider'" in status.detail

    def test_provider_with_catalog_required_base_url_is_unready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry(
                    "future-provider",
                    ("model-x",),
                    ("model-x",),
                    "model-x",
                    False,
                    requires_base_url=True,
                )
            ),
        )

        status = evaluate_provider_readiness("future-provider")

        assert not status.ready
        assert "Configure a base URL/resource endpoint" in status.detail

    def test_oauth_provider_requires_login_when_not_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry(
                    "openai-codex",
                    ("gpt-5.4",),
                    (),
                    "gpt-5.4",
                    True,
                    "ChatGPT Plus/Pro (Codex Subscription)",
                )
            ),
        )
        monkeypatch.setattr("syke.llm.env.get_credential", lambda provider_id: None)
        status = evaluate_provider_readiness("openai-codex")

        assert not status.ready
        assert "syke auth login openai-codex" in status.detail

    def test_default_model_mismatch_only_marks_active_provider_unready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry(
                    "kimi-coding",
                    ("k2p5", "kimi-k2-thinking"),
                    ("k2p5", "kimi-k2-thinking"),
                    "kimi-k2-thinking",
                    False,
                )
            ),
        )
        monkeypatch.setattr("syke.llm.env.get_default_provider", lambda: "kimi-coding")
        monkeypatch.setattr("syke.llm.env.get_default_model", lambda: "sonnet")
        status = evaluate_provider_readiness("kimi-coding")

        assert not status.ready
        assert "Configured default model 'sonnet'" in status.detail

    def test_oauth_provider_with_persisted_oauth_credential_is_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry(
                    "openai-codex",
                    ("gpt-5.4",),
                    ("gpt-5.4",),
                    "gpt-5.4",
                    True,
                    "ChatGPT Plus/Pro (Codex Subscription)",
                )
            ),
        )
        monkeypatch.setattr("syke.llm.env.get_default_provider", lambda: "anthropic")
        monkeypatch.setattr("syke.llm.env.get_default_model", lambda: "claude-sonnet-4-6")
        monkeypatch.setattr(
            "syke.llm.env.get_credential",
            lambda provider_id: {"type": "oauth"} if provider_id == "openai-codex" else None,
        )

        status = evaluate_provider_readiness("openai-codex")

        assert status.ready

    def test_oauth_provider_with_available_models_but_no_persisted_oauth_is_not_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry(
                    "anthropic",
                    ("claude-sonnet-4-6",),
                    ("claude-sonnet-4-6",),
                    "claude-sonnet-4-6",
                    True,
                    "Anthropic (Claude Pro/Max)",
                )
            ),
        )

        status = evaluate_provider_readiness("anthropic")

        assert not status.ready
        assert "syke auth login anthropic" in status.detail


class TestBuildPiRuntimeEnv:
    def test_runtime_env_defaults_to_syke_owned_state(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("SYKE_PI_AGENT_DIR", raising=False)
        monkeypatch.setattr("syke.pi_state.config.SYKE_HOME", tmp_path / ".syke")
        monkeypatch.setattr("syke.llm.env.get_default_provider", lambda: "openai")
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry("openai", ("gpt-5.4",), ("gpt-5.4",), "gpt-5.4", False)
            ),
        )

        env = build_pi_runtime_env()

        assert env["PI_CODING_AGENT_DIR"] == str((tmp_path / ".syke" / "pi-agent").resolve())

    def test_runtime_env_points_pi_at_syke_owned_state(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
        monkeypatch.setattr("syke.llm.env.get_default_provider", lambda: "openai")
        monkeypatch.setattr(
            "syke.llm.env.get_pi_provider_catalog",
            lambda: _catalog(
                PiProviderCatalogEntry("openai", ("gpt-5.4",), ("gpt-5.4",), "gpt-5.4", False)
            ),
        )
        env = build_pi_runtime_env()
        assert env["PI_CODING_AGENT_DIR"] == str((tmp_path / "pi-agent").resolve())


class TestPiWorkspaceSettings:
    def test_workspace_settings_only_write_runtime_local_settings(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
        monkeypatch.setattr("syke.runtime.pi_settings.SYNC_THINKING_LEVEL", "medium")

        env = configure_pi_workspace(tmp_path, session_dir=tmp_path / "sessions")
        settings = json.loads((tmp_path / ".pi" / "settings.json").read_text())

        assert env["PI_CODING_AGENT_DIR"] == str((tmp_path / "pi-agent").resolve())
        assert settings["sessionDir"] == str(tmp_path / "sessions")
        assert settings["defaultThinkingLevel"] == "medium"
        assert settings["quietStartup"] is True
        assert "defaultProvider" not in settings
        assert "defaultModel" not in settings


class TestConfigImportBehavior:
    def test_config_import_does_not_mutate_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-preserved")
        _ = importlib.reload(importlib.import_module("syke.config"))
        assert (
            importlib.import_module("os").environ.get("ANTHROPIC_API_KEY")
            == "sk-ant-test-preserved"
        )

    def test_config_import_does_not_load_project_dotenv(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SYKE_PROJECT_DOTENV_SENTINEL", raising=False)
        (tmp_path / ".env").write_text(
            "SYKE_PROJECT_DOTENV_SENTINEL=from-project-dotenv\n",
            encoding="utf-8",
        )

        _ = importlib.reload(importlib.import_module("syke.config"))

        assert importlib.import_module("os").environ.get("SYKE_PROJECT_DOTENV_SENTINEL") is None


class TestBuildLLMFn:
    def test_restarts_runtime_on_next_call_after_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from syke.llm.simple import build_llm_fn

        class FakeRuntime:
            def __init__(self, model: str, responses: list[SimpleNamespace]) -> None:
                self.model = model
                self.is_alive = True
                self._responses = list(responses)

            def prompt(
                self,
                _prompt: str,
                *,
                timeout: float | None = None,
                new_session: bool = False,
            ) -> SimpleNamespace:
                _ = (timeout, new_session)
                response = self._responses.pop(0)
                if not response.ok:
                    self.is_alive = False
                return response

        runtime_a = FakeRuntime(
            "gpt-5.4",
            [SimpleNamespace(ok=False, error="Pi did not complete within 120.0s", output="")],
        )
        runtime_b = FakeRuntime(
            "gpt-5.4",
            [SimpleNamespace(ok=True, error=None, output="done")],
        )
        runtimes = iter([runtime_a, runtime_b])

        monkeypatch.setattr("syke.llm.simple.start_pi_runtime", lambda **_kwargs: next(runtimes))

        llm_fn = build_llm_fn()

        with pytest.raises(RuntimeError, match="Pi did not complete within 120.0s"):
            llm_fn("first")

        assert llm_fn("second") == "done"

    def test_logs_heartbeat_for_long_running_prompt(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from syke.llm.simple import build_llm_fn

        class FakeRuntime:
            model = "gpt-5.4"
            is_alive = True

            def prompt(
                self,
                _prompt: str,
                *,
                timeout: float | None = None,
                new_session: bool = False,
            ) -> SimpleNamespace:
                _ = (timeout, new_session)
                time.sleep(0.5)
                return SimpleNamespace(ok=True, error=None, output="done")

        monkeypatch.setattr("syke.llm.simple.start_pi_runtime", lambda **_kwargs: FakeRuntime())
        monkeypatch.setattr("syke.llm.simple._HEARTBEAT_INTERVAL_SECONDS", 0.01)

        with caplog.at_level("INFO", logger="syke.llm.simple"):
            llm_fn = build_llm_fn()
            assert llm_fn("long") == "done"

        assert "LLM prompt still running" in caplog.text
