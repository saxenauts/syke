from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

_MAX_OUTPUT_CHARS = 2048


def _truncate_output(value: object) -> str:
    text = value if isinstance(value, str) else str(value)
    return text[:_MAX_OUTPUT_CHARS]


def _make_self_observe_hooks(
    observer: object,
    run_id: str,
) -> tuple[
    Callable[[dict[str, Any], str, dict[str, Any]], Awaitable[None]],
    Callable[[dict[str, Any], str, dict[str, Any]], Awaitable[None]],
]:
    started_at: dict[str, float] = {}

    async def pre_hook(payload: dict[str, Any], tool_use_id: str, _context: dict[str, Any]) -> None:
        del payload
        started_at[tool_use_id] = time.perf_counter()

    async def post_hook(payload: dict[str, Any], tool_use_id: str, _context: dict[str, Any]) -> None:
        start = started_at.pop(tool_use_id, None)
        duration_ms = 0
        if start is not None:
            duration_ms = int((time.perf_counter() - start) * 1000)

        tool_name = str(payload.get("tool_name") or "tool")
        tool_input = payload.get("tool_input")
        tool_response = payload.get("tool_response")

        observer.record(
            "tool_observation",
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_output": _truncate_output(tool_response),
                "duration_ms": duration_ms,
                "success": True,
            },
            run_id=run_id,
        )

    return pre_hook, post_hook


__all__ = ["_make_self_observe_hooks"]
