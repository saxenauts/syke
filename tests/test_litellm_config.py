"""Tests for LiteLLM config YAML generation."""

from __future__ import annotations

from pathlib import Path

import yaml

from syke.llm.litellm_config import generate_litellm_config, write_litellm_config


def _parse(yaml_str: str) -> dict:
    return yaml.safe_load(yaml_str)


class TestGenerateLitellmConfig:
    def test_azure_full_config(self):
        result = generate_litellm_config(
            "azure",
            {
                "endpoint": "https://test.openai.azure.com",
                "model": "gpt-4o",
                "api_version": "2024-02-01",
            },
            "sk-test",
        )
        cfg = _parse(result)
        params = cfg["model_list"][0]["litellm_params"]
        assert cfg["model_list"][0]["model_name"] == "*"
        assert params["model"] == "azure/gpt-4o"
        assert params["api_base"] == "https://test.openai.azure.com"
        assert params["api_key"] == "sk-test"
        assert params["api_version"] == "2024-02-01"

    def test_ollama_no_auth(self):
        result = generate_litellm_config(
            "ollama",
            {"base_url": "http://localhost:11434", "model": "llama3.2"},
            None,
        )
        cfg = _parse(result)
        params = cfg["model_list"][0]["litellm_params"]
        assert params["model"] == "ollama/llama3.2"
        assert params["api_base"] == "http://localhost:11434"
        assert "api_key" not in params

    def test_openai_config(self):
        result = generate_litellm_config(
            "openai",
            {"model": "gpt-4o"},
            "sk-openai",
        )
        cfg = _parse(result)
        params = cfg["model_list"][0]["litellm_params"]
        assert params["model"] == "openai/gpt-4o"
        assert params["api_key"] == "sk-openai"

    def test_vllm_uses_openai_prefix(self):
        result = generate_litellm_config(
            "vllm",
            {"base_url": "http://localhost:8000", "model": "mistral-7b"},
            None,
        )
        cfg = _parse(result)
        params = cfg["model_list"][0]["litellm_params"]
        assert params["model"] == "openai/mistral-7b"

    def test_wildcard_model_name(self):
        result = generate_litellm_config("openai", {"model": "gpt-4o"}, "sk-test")
        cfg = _parse(result)
        assert cfg["model_list"][0]["model_name"] == "*"

    def test_general_settings_has_local_master_key(self):
        result = generate_litellm_config("openai", {"model": "gpt-4o"}, "sk-test")
        cfg = _parse(result)
        assert cfg["general_settings"]["master_key"] == "sk-syke-local-proxy"

    def test_default_model_fallback(self):
        # No model in provider_config — should default to gpt-4o
        result = generate_litellm_config("openai", {}, "sk-test")
        cfg = _parse(result)
        assert cfg["model_list"][0]["litellm_params"]["model"] == "openai/gpt-4o"

    def test_generate_config_unknown_provider_raises(self):
        """Unknown provider should still generate config with provider_id as prefix."""
        # The code doesn't raise for unknown providers — it uses provider_id as prefix
        result = generate_litellm_config("unknown_provider", {"model": "test-model"}, "sk-test")
        cfg = _parse(result)
        # Should use unknown_provider as the prefix
        assert cfg["model_list"][0]["litellm_params"]["model"] == "unknown_provider/test-model"

    def test_generate_config_missing_model_uses_default(self):
        """Missing model in provider_config should use gpt-4o default."""
        result = generate_litellm_config("openai", {}, "sk-test")
        cfg = _parse(result)
        assert cfg["model_list"][0]["litellm_params"]["model"] == "openai/gpt-4o"

    def test_additional_drop_params_strips_anthropic_specific(self):
        """Config strips Anthropic-specific params that non-Anthropic providers reject."""
        result = generate_litellm_config("azure", {"model": "gpt-4o"}, "sk-test")
        cfg = _parse(result)
        params = cfg["model_list"][0]["litellm_params"]
        drop = params["additional_drop_params"]
        assert "output_config" in drop
        assert "prompt_cache_key" in drop
        assert "thinking" not in drop

    def test_no_merge_reasoning_content_in_choices(self):
        """Config should NOT merge reasoning — LiteLLM /v1/messages handles it natively."""
        result = generate_litellm_config("azure", {"model": "Kimi-K2.5"}, "sk-test")
        cfg = _parse(result)
        params = cfg["model_list"][0]["litellm_params"]
        assert "merge_reasoning_content_in_choices" not in params

    def test_litellm_settings_drop_and_modify_params(self):
        """Global settings enable drop_params and modify_params."""
        result = generate_litellm_config("openai", {"model": "gpt-4o"}, "sk-test")
        cfg = _parse(result)
        assert cfg["litellm_settings"]["drop_params"] is True
        assert cfg["litellm_settings"]["modify_params"] is True


class TestWriteLitellmConfig:
    def test_writes_to_default_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = write_litellm_config("openai", {"model": "gpt-4o"}, "sk-test")
        assert path.exists()
        assert path.name == "litellm_config.yaml"

    def test_writes_to_custom_path(self, tmp_path):
        custom = tmp_path / "custom.yaml"
        path = write_litellm_config("openai", {"model": "gpt-4o"}, "sk-test", path=custom)
        assert path == custom
        assert custom.exists()

    def test_creates_parent_dirs(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "config.yaml"
        write_litellm_config("openai", {"model": "gpt-4o"}, "sk-test", path=deep)
        assert deep.exists()

    def test_returns_path(self, tmp_path):
        custom = tmp_path / "out.yaml"
        result = write_litellm_config("openai", {"model": "gpt-4o"}, "sk-test", path=custom)
        assert isinstance(result, Path)
        assert result == custom
