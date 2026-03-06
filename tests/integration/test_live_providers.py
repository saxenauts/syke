"""Live integration tests for LLM providers — real API calls.

These only run when SYKE_LIVE_TESTS=1 is set. Each test also requires
the provider's key env var to be present.

Run: SYKE_LIVE_TESTS=1 SYKE_OPENROUTER_API_KEY=... uv run pytest tests/integration/ -v
"""

from __future__ import annotations

import os

import pytest

live = pytest.mark.skipif(
    os.getenv("SYKE_LIVE_TESTS") != "1",
    reason="SYKE_LIVE_TESTS not set",
)


def _has_key(env_var: str) -> bool:
    return bool(os.getenv(env_var))


@live
@pytest.mark.skipif(not _has_key("SYKE_OPENROUTER_API_KEY"), reason="No OpenRouter key")
def test_openrouter_connectivity() -> None:
    import httpx

    resp = httpx.post(
        "https://openrouter.ai/api/v1/messages",
        headers={
            "x-api-key": os.environ["SYKE_OPENROUTER_API_KEY"],
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
    data = resp.json()
    assert data.get("content") or data.get("type") == "message"


@live
@pytest.mark.skipif(not _has_key("SYKE_ZAI_API_KEY"), reason="No z.ai key")
def test_zai_connectivity() -> None:
    import httpx

    resp = httpx.post(
        "https://api.z.ai/api/anthropic/v1/messages",
        headers={
            "x-api-key": os.environ["SYKE_ZAI_API_KEY"],
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
    data = resp.json()
    assert data.get("content") or data.get("type") == "message"


@live
@pytest.mark.skipif(not _has_key("SYKE_OPENROUTER_API_KEY"), reason="No OpenRouter key")
def test_openrouter_env_builder_produces_correct_env() -> None:
    from syke.llm.env import build_agent_env
    from syke.llm.providers import PROVIDERS

    env = build_agent_env(PROVIDERS["openrouter"])
    assert env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert env["ANTHROPIC_AUTH_TOKEN"] == os.environ["SYKE_OPENROUTER_API_KEY"]
    assert env["ANTHROPIC_API_KEY"] == ""
