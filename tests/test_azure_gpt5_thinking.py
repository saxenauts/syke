from __future__ import annotations

from syke.config import SYNC_THINKING_LEVEL


def test_sync_thinking_level_is_exposed_from_config() -> None:
    assert SYNC_THINKING_LEVEL in {"off", "minimal", "low", "medium", "high", "xhigh"}
