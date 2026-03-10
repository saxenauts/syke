"""Live integration tests for LLM providers — real API calls.

Gate: SYKE_LIVE_TESTS=1. Each test also requires provider-specific env vars.
Run: SYKE_LIVE_TESTS=1 AZURE_API_KEY=... .venv/bin/python -m pytest tests/integration/ -v
"""

from __future__ import annotations

import os
from typing import cast
from unittest.mock import MagicMock, patch

import httpx
import pytest
import yaml

from syke.llm.env import build_agent_env
from syke.llm.litellm_config import generate_litellm_config
from syke.llm.providers import PROVIDERS

# (provider_id, required_env_vars, api_mode, expected_litellm_prefix_or_None)
PROVIDER_MATRIX = [
    ("openrouter", ["SYKE_OPENROUTER_API_KEY"], "anthropic", None),
    ("zai", ["SYKE_ZAI_API_KEY"], "anthropic", None),
    ("kimi", ["SYKE_KIMI_API_KEY"], "anthropic", None),
    ("azure", ["AZURE_API_KEY", "AZURE_API_BASE"], "litellm", "azure/"),
    ("azure-ai", ["AZURE_AI_API_KEY", "AZURE_AI_API_BASE"], "litellm", "azure_ai/"),
    ("openai", ["OPENAI_API_KEY"], "litellm", "openai/"),
    # ollama excluded — requires local server running
]

LIVE_ONLY = pytest.mark.skipif(
    os.getenv("SYKE_LIVE_TESTS") != "1",
    reason="SYKE_LIVE_TESTS not set to 1",
)


def _has_keys(*env_vars: str) -> bool:
    return all(bool(os.getenv(var)) for var in env_vars)


def _provider_param(
    provider_id: str,
    required_env_vars: list[str],
    api_mode: str,
    expected_prefix: str | None,
) -> object:
    return pytest.param(
        provider_id,
        required_env_vars,
        api_mode,
        expected_prefix,
        id=provider_id,
        marks=pytest.mark.skipif(
            not _has_keys(*required_env_vars),
            reason=f"Missing env vars for {provider_id}: {', '.join(required_env_vars)}",
        ),
    )


ENV_BUILDER_PARAMS = [
    _provider_param(provider_id, required_env_vars, api_mode, expected_prefix)
    for provider_id, required_env_vars, api_mode, expected_prefix in PROVIDER_MATRIX
]


@LIVE_ONLY
@pytest.mark.parametrize(
    "provider_id,required_env_vars,api_mode,expected_prefix",
    ENV_BUILDER_PARAMS,
)
def test_provider_env_builder_matrix(
    provider_id: str,
    required_env_vars: list[str],
    api_mode: str,
    expected_prefix: str | None,
) -> None:
    del required_env_vars
    del expected_prefix

    provider = PROVIDERS[provider_id]

    if api_mode == "anthropic":
        env = build_agent_env(provider)
        assert env["ANTHROPIC_BASE_URL"] == provider.base_url
        assert env["ANTHROPIC_AUTH_TOKEN"]
        assert env["ANTHROPIC_API_KEY"] == ""
        return

    mock_cfg = MagicMock()
    mock_cfg.providers = {provider_id: {"model": "test-model", "endpoint": "https://test"}}

    with (
        patch("syke.config.CFG", mock_cfg),
        patch(
            "syke.llm.litellm_config.write_litellm_config", return_value="/tmp/test_litellm.yaml"
        ),
        patch("syke.llm.litellm_proxy.start_litellm_proxy", return_value=12345) as mock_start,
    ):
        env = build_agent_env(provider)

    assert env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:")
    assert env["ANTHROPIC_API_KEY"] == "sk-litellm-proxy-placeholder"
    mock_start.assert_called_once()


LITELLM_YAML_CASES = [
    pytest.param(
        "azure",
        {
            "endpoint": "https://test.openai.azure.com",
            "model": "gpt-4o",
            "api_version": "2024-02-01",
        },
        "azure/",
        ["AZURE_API_KEY", "AZURE_API_BASE"],
        id="azure",
        marks=pytest.mark.skipif(
            not _has_keys("AZURE_API_KEY", "AZURE_API_BASE"),
            reason="Missing env vars for azure: AZURE_API_KEY, AZURE_API_BASE",
        ),
    ),
    pytest.param(
        "azure-ai",
        {"base_url": "https://test.services.ai.azure.com/models", "model": "Kimi-K2.5"},
        "azure_ai/",
        ["AZURE_AI_API_KEY", "AZURE_AI_API_BASE"],
        id="azure-ai",
        marks=pytest.mark.skipif(
            not _has_keys("AZURE_AI_API_KEY", "AZURE_AI_API_BASE"),
            reason="Missing env vars for azure-ai: AZURE_AI_API_KEY, AZURE_AI_API_BASE",
        ),
    ),
    pytest.param(
        "openai",
        {"model": "gpt-4o"},
        "openai/",
        ["OPENAI_API_KEY"],
        id="openai",
        marks=pytest.mark.skipif(
            not _has_keys("OPENAI_API_KEY"),
            reason="Missing env vars for openai: OPENAI_API_KEY",
        ),
    ),
]


@LIVE_ONLY
@pytest.mark.parametrize(
    "provider_id,provider_config,expected_prefix,required_env_vars",
    LITELLM_YAML_CASES,
)
def test_litellm_config_yaml_prefix(
    provider_id: str,
    provider_config: dict[str, str],
    expected_prefix: str,
    required_env_vars: list[str],
) -> None:
    del required_env_vars

    yaml_str = generate_litellm_config(provider_id, provider_config, auth_token="test-token")
    parsed = cast(dict[str, list[dict[str, dict[str, str]]]], yaml.safe_load(yaml_str))
    model_name = parsed["model_list"][0]["litellm_params"]["model"]
    assert model_name.startswith(expected_prefix)


CONNECTIVITY_CASES = [
    pytest.param(
        "openrouter",
        "SYKE_OPENROUTER_API_KEY",
        "https://openrouter.ai/api/v1/messages",
        id="openrouter",
        marks=pytest.mark.skipif(
            not _has_keys("SYKE_OPENROUTER_API_KEY"),
            reason="Missing env vars for openrouter: SYKE_OPENROUTER_API_KEY",
        ),
    ),
    pytest.param(
        "zai",
        "SYKE_ZAI_API_KEY",
        "https://api.z.ai/api/anthropic/v1/messages",
        id="zai",
        marks=pytest.mark.skipif(
            not _has_keys("SYKE_ZAI_API_KEY"),
            reason="Missing env vars for zai: SYKE_ZAI_API_KEY",
        ),
    ),
    pytest.param(
        "kimi",
        "SYKE_KIMI_API_KEY",
        "https://api.kimi.com/coding/v1/messages",
        id="kimi",
        marks=pytest.mark.skipif(
            not _has_keys("SYKE_KIMI_API_KEY"),
            reason="Missing env vars for kimi: SYKE_KIMI_API_KEY",
        ),
    ),
]


@LIVE_ONLY
@pytest.mark.parametrize("provider_id,key_env_var,url", CONNECTIVITY_CASES)
def test_anthropic_native_connectivity(provider_id: str, key_env_var: str, url: str) -> None:
    del provider_id

    resp = httpx.post(
        url,
        headers={
            "x-api-key": os.environ[key_env_var],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        },
        timeout=30,
    )

    assert resp.status_code == 200
    data_obj = cast(dict[str, object], resp.json())
    assert data_obj.get("content") or data_obj.get("type") == "message"
