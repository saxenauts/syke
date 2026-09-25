"""Pi RPC event projection and protocol result types."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, TypedDict

logger = logging.getLogger("syke.llm.pi_client")


class PiUsage(TypedDict):
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    cost_usd: float | None


def _extract_assistant_message(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("type")
    if event_type in {"message_start", "message_end", "turn_end"}:
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            return message
        return None

    if event_type == "agent_end":
        messages = event.get("messages")
        if isinstance(messages, list):
            for candidate in reversed(messages):
                if isinstance(candidate, dict) and candidate.get("role") == "assistant":
                    return candidate
        return None

    return None


def _extract_message_update_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("type") != "message_update":
        return None
    inner = event.get("assistantMessageEvent")
    return inner if isinstance(inner, dict) else None


def _extract_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if not isinstance(content, list):
        return ""

    chunks: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            chunks.append(block["text"])
    return "".join(chunks)


def _extract_usage_int(usage: dict[str, Any], key: str) -> int | None:
    value = usage.get(key)
    return value if isinstance(value, int) else None


def _extract_tool_invocation(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("type") != "tool_execution_start":
        return None
    return {
        "name": str(event.get("toolName") or "tool"),
        "input": event.get("args"),
        "id": event.get("toolCallId"),
    }


def _extract_tool_invocations_from_message(message: dict[str, Any]) -> list[dict[str, Any]]:
    if message.get("role") != "assistant":
        return []

    content = message.get("content")
    if not isinstance(content, list):
        return []

    invocations: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "toolCall":
            continue
        name = block.get("name") or "tool"
        invocations.append(
            {
                "name": str(name),
                "input": block.get("arguments"),
                "id": block.get("id"),
            }
        )
    return invocations


def _dedupe_tool_invocations(invocations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for invocation in invocations:
        key = str(invocation.get("id") or json.dumps(invocation, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(invocation)
    return deduped


class RpcEventStream:
    """Threaded reader for Pi's JSONL RPC stream."""

    def __init__(self, stdout):
        self._stdout = stdout
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._error: str | None = None
        self._last_reset_at = time.monotonic()
        self._callback: Callable[[dict[str, Any]], None] | None = None

    def start(self) -> None:
        self._thread.start()

    def set_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        with self._lock:
            self._callback = callback

    def _read_loop(self) -> None:
        try:
            for line in self._stdout:
                received_at = time.monotonic()
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Non-JSON line from Pi: %s", line[:200])
                    continue

                callback: Callable[[dict[str, Any]], None] | None = None
                with self._lock:
                    if received_at < self._last_reset_at:
                        continue
                    self._events.append(event)
                    callback = self._callback

                    event_type = event.get("type", "")
                    if event_type == "agent_settled":
                        self._done.set()
                    elif event_type == "error":
                        self._error = event.get("message", "Unknown Pi error")
                    elif event_type == "response" and event.get("success") is False:
                        self._error = event.get("error", "Pi command failed")

                if callback is not None:
                    try:
                        callback(event)
                    except Exception:
                        logger.debug("Pi event callback failed", exc_info=True)
                        with self._lock:
                            if self._callback is callback:
                                self._callback = None
        except Exception as exc:
            self._error = str(exc)
            self._done.set()

    def wait(self, timeout: float | None = None) -> bool:
        if timeout is None:
            return self._done.wait(timeout=None)
        # Wall-clock bounded wait. threading.Event.wait runs on the
        # monotonic clock, which freezes during system sleep — a 10-minute
        # timeout would silently span hours. Slice the wait so expiry is
        # re-checked against wall time as it elapses.
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if self._done.wait(timeout=min(max(remaining, 0.0), 5.0)):
                return True
            if remaining <= 0:
                return False

    def rearm(self) -> None:
        """Wait for the next settle while keeping this prompt's events."""
        with self._lock:
            self._done.clear()

    def reset(self) -> None:
        time.sleep(0.1)
        with self._lock:
            self._events.clear()
            self._done.clear()
            self._error = None
            self._last_reset_at = time.monotonic()

    @property
    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    @property
    def error(self) -> str | None:
        return self._error

    def get_output(self) -> str:
        text_deltas: list[str] = []
        final_text: str | None = None

        for event in self.events:
            inner = _extract_message_update_event(event)
            if isinstance(inner, dict) and inner.get("type") == "text_delta":
                delta = inner.get("delta")
                if isinstance(delta, str):
                    text_deltas.append(delta)

            message = _extract_assistant_message(event)
            if not isinstance(message, dict):
                continue
            message_text = _extract_message_text(message)
            if message_text:
                final_text = message_text

        if final_text:
            return final_text.strip()
        if text_deltas:
            return "".join(text_deltas).strip()
        return ""

    def get_thinking_chunks(self) -> list[str]:
        chunks: list[str] = []
        for event in self.events:
            inner = _extract_message_update_event(event)
            if not isinstance(inner, dict):
                continue
            if inner.get("type") == "thinking_delta":
                delta = inner.get("delta")
                if isinstance(delta, str) and delta:
                    chunks.append(delta)
        return chunks

    def get_tool_invocations(self) -> list[dict[str, Any]]:
        invocations: list[dict[str, Any]] = []
        for event in self.events:
            message = event.get("message")
            if isinstance(message, dict):
                invocations.extend(_extract_tool_invocations_from_message(message))
            invocation = _extract_tool_invocation(event)
            if invocation is not None:
                invocations.append(invocation)
        return _dedupe_tool_invocations(invocations)

    def get_usage(self) -> PiUsage:
        input_tokens: int | None = None
        output_tokens: int | None = None
        cache_read_tokens: int | None = None
        cache_write_tokens: int | None = None
        cost_usd: float | None = None

        for event in self.events:
            if event.get("type") != "message_end":
                continue
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue

            value = _extract_usage_int(usage, "input")
            if value is not None:
                input_tokens = (input_tokens or 0) + value
            value = _extract_usage_int(usage, "output")
            if value is not None:
                output_tokens = (output_tokens or 0) + value
            value = _extract_usage_int(usage, "cacheRead")
            if value is not None:
                cache_read_tokens = (cache_read_tokens or 0) + value
            value = _extract_usage_int(usage, "cacheWrite")
            if value is not None:
                cache_write_tokens = (cache_write_tokens or 0) + value

            cost = usage.get("cost")
            if isinstance(cost, dict):
                value = cost.get("total")
                if isinstance(value, (int, float)):
                    cost_usd = (cost_usd or 0.0) + float(value)

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cost_usd": cost_usd,
        }

    def get_assistant_error(self) -> str | None:
        latest_message: dict[str, Any] | None = None
        for event in self.events:
            message = _extract_assistant_message(event)
            if isinstance(message, dict):
                latest_message = message
        if latest_message is None:
            return None
        if latest_message.get("stopReason") == "error":
            error_message = latest_message.get("errorMessage")
            if isinstance(error_message, str) and error_message:
                return error_message
            return "Pi assistant message ended with stopReason=error"
        return None

    def get_message_metadata(self) -> dict[str, str | None]:
        latest_message: dict[str, Any] | None = None
        for event in self.events:
            message = _extract_assistant_message(event)
            if isinstance(message, dict):
                latest_message = message

        if latest_message is None:
            return {"provider": None, "model": None, "response_id": None, "stop_reason": None}

        provider = latest_message.get("provider")
        model = latest_message.get("model")
        response_id = latest_message.get("responseId")
        stop_reason = latest_message.get("stopReason")
        return {
            "provider": provider if isinstance(provider, str) else None,
            "model": model if isinstance(model, str) else None,
            "response_id": response_id if isinstance(response_id, str) else None,
            "stop_reason": stop_reason if isinstance(stop_reason, str) else None,
        }


class _StderrDrain:
    """Threaded stderr reader to prevent Pi from blocking on a full pipe."""

    def __init__(self, stderr):
        self._stderr = stderr
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _read_loop(self) -> None:
        try:
            for line in self._stderr:
                line = line.rstrip()
                if not line:
                    continue
                with self._lock:
                    self._lines.append(line)
                logger.debug("Pi stderr: %s", line)
        except Exception as exc:
            logger.debug("Pi stderr drain stopped: %s", exc)

    def get_output(self) -> str:
        with self._lock:
            return "\n".join(self._lines)


class PiCycleResult:
    """Result of a single Pi prompt/response cycle."""

    def __init__(
        self,
        status: str,
        output: str,
        thinking: list[str],
        tool_calls: list[dict[str, Any]],
        events: list[dict[str, Any]],
        num_turns: int,
        duration_ms: int,
        input_tokens: int | None,
        output_tokens: int | None,
        cache_read_tokens: int | None,
        cache_write_tokens: int | None,
        cost_usd: float | None,
        provider: str | None,
        response_model: str | None,
        response_id: str | None,
        stop_reason: str | None,
        session_id: str | None = None,
        session_file: str | None = None,
        session_name: str | None = None,
        error: str | None = None,
    ):
        self.status = status
        self.output = output
        self.thinking = thinking
        self.tool_calls = tool_calls
        self.events = events
        self.num_turns = num_turns
        self.duration_ms = duration_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cache_write_tokens = cache_write_tokens
        self.cost_usd = cost_usd
        self.provider = provider
        self.response_model = response_model
        self.response_id = response_id
        self.stop_reason = stop_reason
        self.session_id = session_id
        self.session_file = session_file
        self.session_name = session_name
        self.error = error

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def __repr__(self) -> str:
        return (
            f"PiCycleResult(status={self.status!r}, output_len={len(self.output)}, "
            f"tool_calls={len(self.tool_calls)}, duration_ms={self.duration_ms})"
        )
