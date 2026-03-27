from __future__ import annotations

import pytest

from syke.config import SYNC_THINKING


def test_litellm_translation_config_surface_is_removed() -> None:
    from syke.llm.litellm_config import generate_litellm_config

    with pytest.raises(RuntimeError, match="LiteLLM translation was removed"):
        _ = generate_litellm_config(
            "azure",
            {"endpoint": "https://test.openai.azure.com", "model": "gpt-5.4-mini"},
            "test-key",
        )


def test_sync_thinking_budget_is_still_exposed_from_config() -> None:
    assert isinstance(SYNC_THINKING, int)
    assert SYNC_THINKING > 0
    expected = {"type": "enabled", "budget_tokens": SYNC_THINKING}
    assert expected["type"] == "enabled"
