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

    def test_general_settings_no_master_key(self):
        result = generate_litellm_config("openai", {"model": "gpt-4o"}, "sk-test")
        cfg = _parse(result)
        assert cfg["general_settings"]["master_key"] is None

    def test_default_model_fallback(self):
        # No model in provider_config — should default to gpt-4o
        result = generate_litellm_config("openai", {}, "sk-test")
        cfg = _parse(result)
        assert cfg["model_list"][0]["litellm_params"]["model"] == "openai/gpt-4o"


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
