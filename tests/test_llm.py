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
    def test_provider_ready_when_pi_reports_available_models(
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

        assert status.ready
        assert "Pi runtime configured" in status.detail

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
        status = evaluate_provider_readiness("openai-codex")

        assert not status.ready
        assert "syke auth login openai-codex" in status.detail

    def test_default_model_mismatch_marks_provider_unready(
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
        monkeypatch.setattr("syke.llm.env.get_default_model", lambda: "sonnet")
        status = evaluate_provider_readiness("kimi-coding")

        assert not status.ready
        assert "Configured default model 'sonnet'" in status.detail


class TestBuildPiRuntimeEnv:
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
        monkeypatch.setattr("syke.runtime.pi_settings.SYNC_THINKING", 8192)

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


class TestBuildLLMFn:
    def test_restarts_runtime_on_next_call_after_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

        monkeypatch.setattr("syke.llm.simple.prepare_workspace", lambda _user: None)
        monkeypatch.setattr("syke.llm.simple.start_pi_runtime", lambda **_kwargs: next(runtimes))
        monkeypatch.setattr("syke.llm.simple.write_sandbox_config", lambda *args, **kwargs: None)

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
                time.sleep(0.03)
                return SimpleNamespace(ok=True, error=None, output="done")

        monkeypatch.setattr("syke.llm.simple.prepare_workspace", lambda _user: None)
        monkeypatch.setattr("syke.llm.simple.start_pi_runtime", lambda **_kwargs: FakeRuntime())
        monkeypatch.setattr("syke.llm.simple._HEARTBEAT_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr("syke.llm.simple.write_sandbox_config", lambda *args, **kwargs: None)

        with caplog.at_level("INFO", logger="syke.llm.simple"):
            llm_fn = build_llm_fn()
            assert llm_fn("long") == "done"

        assert "LLM prompt still running" in caplog.text
