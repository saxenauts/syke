"""Tests for removed LiteLLM proxy surface."""

from __future__ import annotations

import pytest

from syke.llm.litellm_proxy import (
    is_litellm_proxy_running,
    start_litellm_proxy,
    stop_litellm_proxy,
)


def test_start_litellm_proxy_removed() -> None:
    with pytest.raises(RuntimeError, match="LiteLLM proxy runtime was removed"):
        _ = start_litellm_proxy("/tmp/litellm.yaml")


def test_stop_is_noop_and_not_running() -> None:
    stop_litellm_proxy()
    assert is_litellm_proxy_running() is False
