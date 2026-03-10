"""Tests for syke.llm — provider resolution and env building."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from syke.llm.env import build_agent_env, resolve_provider
from syke.llm.providers import PROVIDERS


def call_resolve_provider_config(spec: object) -> dict[str, str]:
    resolver = cast(
        Callable[[object], dict[str, str]],
        importlib.import_module("syke.llm.env")._resolve_provider_config,
    )
    return resolver(spec)


class TestProviderSpec:
    def test_claude_login_has_no_base_url(self) -> None:
        spec = PROVIDERS["claude-login"]
        assert spec.base_url is None
        assert spec.is_claude_login is True

    def test_all_providers_registered_and_unique(self) -> None:
        ids = [s.id for s in PROVIDERS.values()]
        assert len(ids) == len(set(ids))
        assert {
            "claude-login",
            "openrouter",
            "zai",
            "kimi",
            "codex",
            "azure",
            "azure-ai",
            "openai",
            "ollama",
            "vllm",
            "llama-cpp",
        } == set(ids)
        assert PROVIDERS["codex"].needs_proxy is True
        assert PROVIDERS["openrouter"].base_url is not None


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

    def test_auto_detects_claude_login(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("SYKE_PROVIDER", raising=False)
        from syke.llm.auth_store import AuthStore

        empty_store = AuthStore(tmp_path / "auth.json")
        with patch("syke.llm.env._claude_login_available", return_value=True):
            with patch("syke.llm.env._get_auth_store", return_value=empty_store):
                spec = resolve_provider()
        assert spec.id == "claude-login"

    def test_raises_when_no_provider_and_no_claude(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("SYKE_PROVIDER", raising=False)
        from syke.llm.auth_store import AuthStore

        empty_store = AuthStore(tmp_path / "auth.json")
        with patch("syke.llm.env._claude_login_available", return_value=False):
            with patch("syke.llm.env._get_auth_store", return_value=empty_store):
                with pytest.raises(RuntimeError, match="No provider configured"):
                    _ = resolve_provider()


class TestBuildAgentEnv:
    def test_claude_login_neutralizes_api_key(self) -> None:
        spec = PROVIDERS["claude-login"]
        env = build_agent_env(spec)
        assert env == {"ANTHROPIC_API_KEY": ""}

    def test_openrouter_sets_base_url_and_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYKE_OPENROUTER_API_KEY", "sk-or-test-key")
        spec = PROVIDERS["openrouter"]
        env = build_agent_env(spec)
        assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
        assert not env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:")
        assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-or-test-key"
        assert env["ANTHROPIC_API_KEY"] == ""

    def test_azure_uses_litellm_proxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_API_KEY", "azure-test-key")
        spec = PROVIDERS["azure"]
        with patch(
            "syke.llm.litellm_config.write_litellm_config", return_value=Path("/tmp/litellm.yaml")
        ) as write_config:
            with patch(
                "syke.llm.litellm_proxy.start_litellm_proxy", return_value=40123
            ) as start_proxy:
                env = build_agent_env(spec)

        assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:40123"
        assert env["ANTHROPIC_API_KEY"] == "sk-litellm-proxy-placeholder"
        write_config.assert_called_once()
        start_proxy.assert_called_once_with(Path("/tmp/litellm.yaml"))

    def test_codex_uses_codex_proxy_not_litellm(self) -> None:
        spec = PROVIDERS["codex"]
        with patch(
            "syke.llm.env._build_codex_env",
            return_value={"ANTHROPIC_BASE_URL": "http://127.0.0.1:9999"},
        ) as codex_env:
            with patch("syke.llm.env._build_litellm_env") as litellm_env:
                env = build_agent_env(spec)

        assert env == {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9999"}
        codex_env.assert_called_once_with()
        litellm_env.assert_not_called()

    def test_zai_sets_base_url_and_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYKE_ZAI_API_KEY", "zai-test-key")
        spec = PROVIDERS["zai"]
        env = build_agent_env(spec)
        assert env["ANTHROPIC_BASE_URL"] == "https://api.z.ai/api/anthropic"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "zai-test-key"
        assert env["ANTHROPIC_API_KEY"] == ""

    def test_missing_token_env_var_omits_auth_token(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("SYKE_OPENROUTER_API_KEY", raising=False)
        from syke.llm.auth_store import AuthStore

        empty_store = AuthStore(tmp_path / "auth.json")
        spec = PROVIDERS["openrouter"]
        with patch("syke.llm.env._get_auth_store", return_value=empty_store):
            env = build_agent_env(spec)
        assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert env["ANTHROPIC_API_KEY"] == ""

    def test_auto_resolves_when_no_provider_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "openrouter")
        monkeypatch.setenv("SYKE_OPENROUTER_API_KEY", "test-key")
        env = build_agent_env()
        assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"


class TestNewLiteLLMProviders:
    def test_new_litellm_providers_registered(self) -> None:
        """All 5 new LiteLLM providers are in PROVIDERS dict."""
        for pid in ("azure", "openai", "ollama", "vllm", "llama-cpp"):
            assert pid in PROVIDERS
            p = PROVIDERS[pid]
            assert p.api_mode == "litellm"
            assert p.requires_litellm is True
            assert p.needs_proxy is True

    def test_existing_providers_unchanged(self) -> None:
        """Existing provider behavior preserved."""
        assert PROVIDERS["codex"].needs_proxy is True
        assert PROVIDERS["codex"].api_mode == "codex"
        assert PROVIDERS["codex"].requires_litellm is False
        assert PROVIDERS["openrouter"].needs_proxy is False
        assert PROVIDERS["openrouter"].api_mode == "anthropic"
        assert PROVIDERS["claude-login"].is_claude_login is True

    def test_provider_count(self) -> None:
        """Exactly 11 providers registered."""
        assert len(PROVIDERS) == 11


class TestConfigPopRemoved:
    def test_config_import_does_not_pop_anthropic_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-preserved")
        _ = importlib.reload(importlib.import_module("syke.config"))
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-test-preserved"


class TestResolveProviderConfig:
    def test_env_var_overrides_config_toml_azure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AZURE_API_BASE env var overrides config.toml endpoint."""
        monkeypatch.setenv("AZURE_API_BASE", "https://override.openai.azure.com")
        from syke.config_file import SykeConfig

        with patch(
            "syke.config.CFG",
            SykeConfig(providers={"azure": {"endpoint": "https://original.openai.azure.com"}}),
        ):
            spec = PROVIDERS["azure"]
            config = call_resolve_provider_config(spec)
            assert config["endpoint"] == "https://override.openai.azure.com"

    def test_config_toml_used_when_env_var_absent_azure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Config.toml endpoint used when AZURE_API_BASE env var not set."""
        monkeypatch.delenv("AZURE_API_BASE", raising=False)
        from syke.config_file import SykeConfig

        with patch(
            "syke.config.CFG",
            SykeConfig(providers={"azure": {"endpoint": "https://config.openai.azure.com"}}),
        ):
            spec = PROVIDERS["azure"]
            config = call_resolve_provider_config(spec)
            assert config["endpoint"] == "https://config.openai.azure.com"

    def test_env_var_overrides_config_toml_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OPENAI_BASE_URL env var overrides config.toml base_url."""
        monkeypatch.setenv("OPENAI_BASE_URL", "https://override.openai.com")
        from syke.config_file import SykeConfig

        with patch(
            "syke.config.CFG",
            SykeConfig(providers={"openai": {"base_url": "https://original.openai.com"}}),
        ):
            spec = PROVIDERS["openai"]
            config = call_resolve_provider_config(spec)
            assert config["base_url"] == "https://override.openai.com"

    def test_env_var_overrides_config_toml_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OLLAMA_HOST env var overrides config.toml base_url."""
        monkeypatch.setenv("OLLAMA_HOST", "http://override:11434")
        from syke.config_file import SykeConfig

        with patch(
            "syke.config.CFG",
            SykeConfig(providers={"ollama": {"base_url": "http://original:11434"}}),
        ):
            spec = PROVIDERS["ollama"]
            config = call_resolve_provider_config(spec)
            assert config["base_url"] == "http://override:11434"

    def test_env_var_overrides_config_toml_vllm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VLLM_API_BASE env var overrides config.toml base_url."""
        monkeypatch.setenv("VLLM_API_BASE", "http://override:8000")
        from syke.config_file import SykeConfig

        with patch(
            "syke.config.CFG", SykeConfig(providers={"vllm": {"base_url": "http://original:8000"}})
        ):
            spec = PROVIDERS["vllm"]
            config = call_resolve_provider_config(spec)
            assert config["base_url"] == "http://override:8000"

    def test_env_var_overrides_config_toml_llama_cpp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLAMA_CPP_API_BASE env var overrides config.toml base_url."""
        monkeypatch.setenv("LLAMA_CPP_API_BASE", "http://override:8080")
        from syke.config_file import SykeConfig

        with patch(
            "syke.config.CFG",
            SykeConfig(providers={"llama-cpp": {"base_url": "http://original:8080"}}),
        ):
            spec = PROVIDERS["llama-cpp"]
            config = call_resolve_provider_config(spec)
            assert config["base_url"] == "http://override:8080"

    def test_multiple_env_var_overrides_azure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Multiple env vars override multiple config.toml values for azure."""
        monkeypatch.setenv("AZURE_API_BASE", "https://override.openai.azure.com")
        monkeypatch.setenv("AZURE_API_VERSION", "2024-06-01")
        from syke.config_file import SykeConfig

        with patch(
            "syke.config.CFG",
            SykeConfig(
                providers={
                    "azure": {
                        "endpoint": "https://original.openai.azure.com",
                        "api_version": "2024-02-01",
                    }
                }
            ),
        ):
            spec = PROVIDERS["azure"]
            config = call_resolve_provider_config(spec)
            assert config["endpoint"] == "https://override.openai.azure.com"
            assert config["api_version"] == "2024-06-01"

    def test_unknown_provider_returns_empty_dict(self) -> None:
        """Unknown provider returns empty dict (no crash)."""
        from syke.config_file import SykeConfig

        with patch("syke.config.CFG", SykeConfig(providers={})):
            spec = PROVIDERS["openrouter"]
            config = call_resolve_provider_config(spec)
            assert config == {}

    def test_provider_with_no_config_toml_entry_returns_empty_dict(self) -> None:
        """Provider not in config.toml returns empty dict."""
        from syke.config_file import SykeConfig

        with patch("syke.config.CFG", SykeConfig(providers={"other": {"key": "value"}})):
            spec = PROVIDERS["azure"]
            config = call_resolve_provider_config(spec)
            assert config == {}


class TestBackwardCompatibility:
    """Verify original 5 providers still work correctly after multi-provider expansion."""

    def test_original_providers_still_registered(self) -> None:
        """Verify claude-login, openrouter, zai, kimi, codex all in PROVIDERS."""
        original_providers = {"claude-login", "openrouter", "zai", "kimi", "codex"}
        for provider_id in original_providers:
            assert provider_id in PROVIDERS, f"Provider {provider_id} not registered"

    def test_original_provider_api_modes(self) -> None:
        """Verify api_mode values for original providers."""
        # Anthropic-compatible providers
        assert PROVIDERS["claude-login"].api_mode == "anthropic"
        assert PROVIDERS["openrouter"].api_mode == "anthropic"
        assert PROVIDERS["zai"].api_mode == "anthropic"
        assert PROVIDERS["kimi"].api_mode == "anthropic"
        # Codex uses its own mode
        assert PROVIDERS["codex"].api_mode == "codex"

    def test_needs_proxy_backward_compat(self) -> None:
        """Verify needs_proxy behavior for original providers."""
        # Only codex needs proxy among original providers
        assert PROVIDERS["codex"].needs_proxy is True
        assert PROVIDERS["openrouter"].needs_proxy is False
        assert PROVIDERS["claude-login"].needs_proxy is False
        assert PROVIDERS["zai"].needs_proxy is False
        assert PROVIDERS["kimi"].needs_proxy is False
        # Verify new azure provider also has needs_proxy=True
        assert PROVIDERS["azure"].needs_proxy is True

    def test_claude_login_env_dict(self) -> None:
        """Verify claude-login build_agent_env returns only ANTHROPIC_API_KEY=""."""
        spec = PROVIDERS["claude-login"]
        env = build_agent_env(spec)
        assert env == {"ANTHROPIC_API_KEY": ""}
        assert "ANTHROPIC_BASE_URL" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env

    def test_openrouter_env_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify openrouter build_agent_env returns correct dict shape."""
        monkeypatch.setenv("SYKE_OPENROUTER_API_KEY", "sk-or-test-key")
        spec = PROVIDERS["openrouter"]
        env = build_agent_env(spec)
        assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-or-test-key"
        assert env["ANTHROPIC_API_KEY"] == ""

    def test_config_without_providers_section(self) -> None:
        """Verify SykeConfig with no [providers] section has providers={}."""
        from syke.config_file import SykeConfig

        cfg = SykeConfig()
        assert cfg.providers == {}
        assert isinstance(cfg.providers, dict)

    def test_old_auth_json_schema_loads_without_providers_key(self, tmp_path: Path) -> None:
        """Verify old auth.json schema (pre-LiteLLM) loads correctly.

        Old schema had minimal structure with only anthropic-native providers.
        This test ensures backward compatibility when loading such files.
        """
        from syke.llm.auth_store import AuthStore

        # Create old-style auth.json with minimal schema
        old_auth_file = tmp_path / "auth.json"
        old_auth_data = {
            "version": 1,
            "active_provider": "openrouter",
            "providers": {
                "openrouter": {"auth_token": "sk-or-old-test-key"},
                "zai": {"auth_token": "zai-old-test-key"},
            },
        }
        old_auth_file.write_text(__import__("json").dumps(old_auth_data, indent=2) + "\n")

        # Load via AuthStore — should not crash
        store = AuthStore(old_auth_file)

        # Verify it loads correctly
        assert store.get_active_provider() == "openrouter"
        assert store.get_token("openrouter") == "sk-or-old-test-key"
        assert store.get_token("zai") == "zai-old-test-key"
        assert store.get_token("azure") is None  # New provider not in old file

        # Verify list_providers works
        providers = store.list_providers()
        assert "openrouter" in providers
        assert "zai" in providers
        assert providers["openrouter"]["active"] == "yes"
        assert providers["zai"]["active"] == ""
