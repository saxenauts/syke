"""Tests for Syke's Pi-native provider resolution and workspace config."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from syke.llm.env import build_pi_runtime_env, resolve_provider
from syke.llm.providers import PROVIDERS
from syke.runtime.pi_settings import configure_pi_workspace


class TestProviderRegistry:
    def test_expected_providers_registered(self) -> None:
        assert {
            "openrouter",
            "zai",
            "kimi",
            "codex",
            "azure",
            "openai",
            "ollama",
            "vllm",
            "llama-cpp",
        } == set(PROVIDERS)

    def test_pi_provider_mappings_exist_for_primary_cloud_providers(self) -> None:
        assert PROVIDERS["openrouter"].pi_provider == "openrouter"
        assert PROVIDERS["zai"].pi_provider == "zai"
        assert PROVIDERS["kimi"].pi_provider == "kimi-coding"
        assert PROVIDERS["openai"].pi_provider == "openai"
        assert PROVIDERS["azure"].pi_provider == "azure-openai-responses"
        assert PROVIDERS["codex"].pi_provider == "openai-codex"


class TestResolveProvider:
    def test_cli_flag_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "zai")
        spec = resolve_provider(cli_provider="openrouter")
        assert spec.id == "openrouter"

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "openrouter")
        spec = resolve_provider()
        assert spec.id == "openrouter"

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider"):
            _ = resolve_provider(cli_provider="nonexistent")

    def test_no_provider_configured_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("SYKE_PROVIDER", raising=False)
        from syke.llm.auth_store import AuthStore

        empty_store = AuthStore(tmp_path / "auth.json")
        with patch("syke.llm.env._get_auth_store", return_value=empty_store):
            with pytest.raises(RuntimeError, match="No provider configured"):
                _ = resolve_provider()


class TestBuildPiRuntimeEnv:
    def test_openrouter_maps_token_to_pi_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYKE_OPENROUTER_API_KEY", "sk-or-test-key")
        env = build_pi_runtime_env(PROVIDERS["openrouter"])
        assert env["OPENROUTER_API_KEY"] == "sk-or-test-key"

    def test_openai_uses_base_url_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://example.openai.local/v1")
        env = build_pi_runtime_env(PROVIDERS["openai"])
        assert env["OPENAI_API_KEY"] == "sk-openai"
        assert env["OPENAI_BASE_URL"] == "https://example.openai.local/v1"

    def test_azure_maps_api_key_and_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_API_KEY", "azure-test-key")
        monkeypatch.setenv("AZURE_API_BASE", "https://azure.example.com")
        monkeypatch.setenv("AZURE_API_VERSION", "2025-01-01-preview")
        env = build_pi_runtime_env(PROVIDERS["azure"])
        assert env["AZURE_OPENAI_API_KEY"] == "azure-test-key"
        assert env["AZURE_OPENAI_BASE_URL"] == "https://azure.example.com/openai/v1"
        assert env["AZURE_OPENAI_API_VERSION"] == "v1"

    def test_azure_openai_base_url_is_normalized_when_openai_path_is_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AZURE_API_KEY", "azure-test-key")
        monkeypatch.setenv("AZURE_API_BASE", "https://azure.example.com/openai")
        env = build_pi_runtime_env(PROVIDERS["azure"])
        assert env["AZURE_OPENAI_BASE_URL"] == "https://azure.example.com/openai/v1"
        assert env["AZURE_OPENAI_API_VERSION"] == "v1"

    def test_azure_rejects_deployment_scoped_urls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_API_KEY", "azure-test-key")
        monkeypatch.setenv(
            "AZURE_API_BASE",
            "https://azure.example.com/openai/deployments/prod-gpt4o",
        )
        with pytest.raises(RuntimeError, match="deployment URLs are not supported"):
            _ = build_pi_runtime_env(PROVIDERS["azure"])

    def test_vllm_uses_neutral_custom_api_key_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "vllm")
        monkeypatch.setenv("VLLM_API_BASE", "http://127.0.0.1:8000/v1")
        from syke.llm.auth_store import AuthStore

        store = AuthStore(Path("/tmp/unused-auth.json"))
        with patch("syke.llm.env._get_auth_store", return_value=store):
            store.set_token("vllm", "local-secret")
            env = build_pi_runtime_env(PROVIDERS["vllm"])
        assert env["SYKE_PI_API_KEY"] == "local-secret"


class TestPiWorkspaceSettings:
    def test_workspace_settings_for_builtin_provider(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "openrouter")
        monkeypatch.setenv("SYKE_OPENROUTER_API_KEY", "sk-or-test-key")

        env = configure_pi_workspace(tmp_path, session_dir=tmp_path / "sessions")
        settings = json.loads((tmp_path / ".pi" / "settings.json").read_text())

        assert env["OPENROUTER_API_KEY"] == "sk-or-test-key"
        assert settings["defaultProvider"] == "openrouter"
        assert settings["sessionDir"] == str(tmp_path / "sessions")
        assert settings["defaultModel"]
        assert settings["defaultThinkingLevel"]

    def test_workspace_settings_write_openai_override_extension(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://custom.example.com/v1")

        _ = configure_pi_workspace(tmp_path, session_dir=tmp_path / "sessions")
        settings = json.loads((tmp_path / ".pi" / "settings.json").read_text())
        extension_path = tmp_path / ".pi" / "extensions" / "syke-provider.mjs"

        assert settings["defaultProvider"] == "openai"
        assert settings["extensions"] == ["extensions"]
        assert "custom.example.com" in extension_path.read_text()

    def test_workspace_settings_write_custom_openai_compatible_provider(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "vllm")
        monkeypatch.setenv("VLLM_API_BASE", "http://127.0.0.1:8000/v1")
        from syke.llm.auth_store import AuthStore

        store = AuthStore(tmp_path / "auth.json")
        store.set_token("vllm", "local-secret")
        with patch("syke.llm.env._get_auth_store", return_value=store):
            env = configure_pi_workspace(tmp_path, session_dir=tmp_path / "sessions")

        settings = json.loads((tmp_path / ".pi" / "settings.json").read_text())
        extension = (tmp_path / ".pi" / "extensions" / "syke-provider.mjs").read_text()

        assert env["SYKE_PI_API_KEY"] == "local-secret"
        assert settings["defaultProvider"] == "syke-vllm"
        assert "registerProvider(\"syke-vllm\"" in extension
        assert "http://127.0.0.1:8000/v1" in extension

class TestConfigImportBehavior:
    def test_config_import_does_not_mutate_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-preserved")
        _ = importlib.reload(importlib.import_module("syke.config"))
        assert importlib.import_module("os").environ.get("ANTHROPIC_API_KEY") == "sk-ant-test-preserved"
