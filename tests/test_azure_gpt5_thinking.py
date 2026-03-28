from __future__ import annotations

from syke.config import SYNC_THINKING


def test_sync_thinking_budget_is_still_exposed_from_config() -> None:
    assert isinstance(SYNC_THINKING, int)
    assert SYNC_THINKING > 0
    expected = {"type": "enabled", "budget_tokens": SYNC_THINKING}
    assert expected["type"] == "enabled"
