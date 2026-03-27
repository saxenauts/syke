"""Tests for removed LiteLLM config surface."""

from __future__ import annotations

from pathlib import Path

import pytest

from syke.llm.litellm_config import generate_litellm_config, write_litellm_config


def test_generate_litellm_config_removed() -> None:
    with pytest.raises(RuntimeError, match="LiteLLM translation was removed"):
        _ = generate_litellm_config("openai", {"model": "gpt-5"}, "sk-test")


def test_write_litellm_config_removed(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="LiteLLM translation was removed"):
        _ = write_litellm_config("openai", {"model": "gpt-5"}, "sk-test", path=tmp_path / "cfg.yaml")
