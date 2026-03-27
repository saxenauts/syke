"""Provider integration checks for Pi-native surfaces."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from syke.llm.env import build_agent_env, build_pi_runtime_env
from syke.llm.providers import PROVIDERS

LIVE_ONLY = pytest.mark.skipif(
    os.getenv("SYKE_LIVE_TESTS") != "1",
    reason="SYKE_LIVE_TESTS not set to 1",
)


def _has_keys(*env_vars: str) -> bool:
    return all(bool(os.getenv(var)) for var in env_vars)


def test_provider_registry_is_pi_native() -> None:
    assert "claude-login" not in PROVIDERS
    assert "azure-ai" not in PROVIDERS
    assert all(spec.api_mode == "pi" for spec in PROVIDERS.values())
    assert all(spec.requires_litellm is False for spec in PROVIDERS.values())
    assert all(spec.needs_proxy is False for spec in PROVIDERS.values())


def test_build_agent_env_aliases_pi_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    provider = PROVIDERS["openai"]
    assert build_agent_env(provider) == build_pi_runtime_env(provider)


def test_azure_env_normalization_to_pi_contract() -> None:
    mock_cfg = MagicMock()
    mock_cfg.providers = {
        "azure": {
            "endpoint": "https://example.openai.azure.com/openai",
            "api_version": "2024-02-01",
        }
    }
    with patch("syke.config.CFG", mock_cfg):
        env = build_pi_runtime_env(PROVIDERS["azure"])
    assert env["AZURE_OPENAI_BASE_URL"] == "https://example.openai.azure.com/openai/v1"
    assert env["AZURE_OPENAI_API_VERSION"] == "v1"


LIVE_PROVIDER_TOKEN_CASES = [
    pytest.param(
        "openrouter",
        "SYKE_OPENROUTER_API_KEY",
        "OPENROUTER_API_KEY",
        id="openrouter",
        marks=pytest.mark.skipif(
            not _has_keys("SYKE_OPENROUTER_API_KEY"),
            reason="Missing SYKE_OPENROUTER_API_KEY",
        ),
    ),
    pytest.param(
        "zai",
        "SYKE_ZAI_API_KEY",
        "ZAI_API_KEY",
        id="zai",
        marks=pytest.mark.skipif(
            not _has_keys("SYKE_ZAI_API_KEY"),
            reason="Missing SYKE_ZAI_API_KEY",
        ),
    ),
    pytest.param(
        "kimi",
        "SYKE_KIMI_API_KEY",
        "KIMI_API_KEY",
        id="kimi",
        marks=pytest.mark.skipif(
            not _has_keys("SYKE_KIMI_API_KEY"),
            reason="Missing SYKE_KIMI_API_KEY",
        ),
    ),
    pytest.param(
        "openai",
        "OPENAI_API_KEY",
        "OPENAI_API_KEY",
        id="openai",
        marks=pytest.mark.skipif(
            not _has_keys("OPENAI_API_KEY"),
            reason="Missing OPENAI_API_KEY",
        ),
    ),
]


@LIVE_ONLY
@pytest.mark.parametrize("provider_id,token_env_var,pi_env_var", LIVE_PROVIDER_TOKEN_CASES)
def test_live_provider_tokens_export_to_pi_env(
    provider_id: str,
    token_env_var: str,
    pi_env_var: str,
) -> None:
    provider = PROVIDERS[provider_id]
    env = build_pi_runtime_env(provider)
    assert env[pi_env_var] == os.environ[token_env_var]
