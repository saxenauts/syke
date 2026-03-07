"""Tests for syke.llm — provider resolution and env building."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from syke.llm.providers import PROVIDERS, ProviderSpec
from syke.llm.env import build_agent_env, resolve_provider


class TestProviderSpec:
    def test_claude_login_has_no_base_url(self) -> None:
        spec = PROVIDERS["claude-login"]
        assert spec.base_url is None
        assert spec.is_claude_login is True

    def test_all_providers_registered_and_unique(self) -> None:
        ids = [s.id for s in PROVIDERS.values()]
        assert len(ids) == len(set(ids))
        assert {"claude-login", "openrouter", "zai", "kimi", "codex"} == set(ids)
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
            resolve_provider(cli_provider="nonexistent")

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
                    resolve_provider()


class TestBuildAgentEnv:
    def test_claude_login_neutralizes_api_key(self) -> None:
        spec = PROVIDERS["claude-login"]
        env = build_agent_env(spec)
        assert env == {"ANTHROPIC_API_KEY": ""}

    def test_openrouter_sets_base_url_and_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYKE_OPENROUTER_API_KEY", "sk-or-test-key")
        spec = PROVIDERS["openrouter"]
        env = build_agent_env(spec)
        assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-or-test-key"
        assert env["ANTHROPIC_API_KEY"] == ""

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

    def test_auto_resolves_when_no_provider_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "openrouter")
        monkeypatch.setenv("SYKE_OPENROUTER_API_KEY", "test-key")
        env = build_agent_env()
        assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"


class TestConfigPopRemoved:
    def test_config_import_does_not_pop_anthropic_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-preserved")
        importlib.reload(importlib.import_module("syke.config"))
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-test-preserved"
