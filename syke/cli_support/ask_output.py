"""Ask-command output helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from syke.llm.backends import AskEvent

ASK_RESULT_OPTIONAL_FIELDS = (
    "transport",
    "ipc_fallback",
    "ipc_error",
    "ipc_attempt_ms",
    "daemon_pid",
    "ipc_roundtrip_ms",
    "ipc_socket_path",
)


@dataclass
class JsonlAskEventCoalescer:
    emit_line: Callable[[dict[str, object]], None]
    pending_type: str | None = None
    pending_parts: list[str] = field(default_factory=list)

    def push(self, event: AskEvent) -> None:
        if event.type in {"thinking", "text"}:
            if self.pending_type == event.type:
                self.pending_parts.append(event.content)
                return
            self.flush()
            self.pending_type = event.type
            self.pending_parts = [event.content]
            return

        self.flush()
        self.emit_line(
            {
                "type": event.type,
                "content": event.content,
                "metadata": event.metadata,
            }
        )

    def flush(self) -> None:
        if self.pending_type is None:
            return
        content = "".join(self.pending_parts)
        if content:
            self.emit_line(
                {
                    "type": self.pending_type,
                    "content": content,
                    "metadata": None,
                }
            )
        self.pending_type = None
        self.pending_parts.clear()


def build_ask_result_payload(
    *,
    question: str,
    answer: str | None,
    provider: str,
    metadata: dict[str, object] | None,
    ok: bool,
    error: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": ok,
        "question": question,
        "answer": answer,
        "provider": provider,
        "duration_ms": metadata.get("duration_ms") if isinstance(metadata, dict) else None,
        "cost_usd": metadata.get("cost_usd") if isinstance(metadata, dict) else None,
        "input_tokens": metadata.get("input_tokens") if isinstance(metadata, dict) else None,
        "output_tokens": metadata.get("output_tokens") if isinstance(metadata, dict) else None,
        "tool_calls": metadata.get("tool_calls") if isinstance(metadata, dict) else None,
        "error": error
        if error is not None
        else metadata.get("error")
        if isinstance(metadata, dict)
        else None,
    }
    if isinstance(metadata, dict):
        for key in ASK_RESULT_OPTIONAL_FIELDS:
            if key in metadata:
                payload[key] = metadata.get(key)
    return payload

