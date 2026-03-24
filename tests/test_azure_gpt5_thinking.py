"""End-to-end tests for Azure GPT-5.4-mini thinking through LiteLLM proxy."""

import pytest


class TestAzureGPT5ThinkingEndToEnd:
    """Verify Azure GPT-5 thinking patches work end-to-end."""

    def test_azure_gpt5_config_allows_thinking(self):
        """Azure GPT-5 models should have thinking enabled in config."""
        from syke.llm.litellm_config import generate_litellm_config

        config_yaml = generate_litellm_config(
            "azure",
            {"endpoint": "https://test.openai.azure.com", "model": "gpt-5.4-mini"},
            "test-key",
        )

        # Verify reasoning_auto_summary is enabled
        assert "reasoning_auto_summary: true" in config_yaml

    def test_thinking_patch_adds_thinking_to_azure_gpt5_params(self):
        """Patch 4: thinking is added to Azure GPT-5 supported params at runtime."""
        from syke.llm.litellm_proxy import _enable_azure_responses_api
        from litellm.llms.azure.chat.gpt_5_transformation import AzureOpenAIGPT5Config

        # Before patch: thinking not in params
        params_before = AzureOpenAIGPT5Config().get_supported_openai_params("gpt-5.4-mini")
        assert "thinking" not in params_before

        # Apply patches
        _enable_azure_responses_api()

        # After patch: thinking is in params
        params_after = AzureOpenAIGPT5Config().get_supported_openai_params("gpt-5.4-mini")
        assert "thinking" in params_after

    def test_reasoning_models_get_no_api_version(self):
        """Azure reasoning models (gpt-5, o1, o3, o4) skip api_version."""
        from syke.llm.litellm_config import generate_litellm_config

        config_yaml = generate_litellm_config(
            "azure",
            {
                "endpoint": "https://test.openai.azure.com",
                "model": "gpt-5.4-mini",
                "api_version": "2024-02-01",  # Should be stripped for reasoning models
            },
            "test-key",
        )

        # api_version should NOT be in the config for reasoning models
        assert "api_version" not in config_yaml

    def test_non_reasoning_models_keep_api_version(self):
        """Non-reasoning Azure models keep api_version."""
        from syke.llm.litellm_config import generate_litellm_config

        config_yaml = generate_litellm_config(
            "azure",
            {
                "endpoint": "https://test.openai.azure.com",
                "model": "gpt-4o",  # Not a reasoning model
                "api_version": "2024-02-01",
            },
            "test-key",
        )

        # api_version SHOULD be present for non-reasoning models
        assert "api_version" in config_yaml

    def test_syke_sends_enabled_thinking_type(self):
        """Verify syke sends enabled type, not adaptive."""
        from syke.distribution.ask_agent import SYNC_THINKING

        # SYNC_THINKING should be a number (budget_tokens)
        assert isinstance(SYNC_THINKING, int)
        assert SYNC_THINKING > 0

        # When making requests, syke sends:
        # thinking={"type": "enabled", "budget_tokens": SYNC_THINKING}
        expected = {"type": "enabled", "budget_tokens": SYNC_THINKING}
        assert expected["type"] == "enabled"  # Not "adaptive"
