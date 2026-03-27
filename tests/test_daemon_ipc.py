from __future__ import annotations

from pathlib import Path

import pytest

from syke.daemon.ipc import (
    DaemonIpcServer,
    DaemonIpcUnavailable,
    ask_via_daemon,
)
from syke.llm.backends import AskEvent


def test_daemon_ipc_round_trip_streams_events(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)
    seen: list[AskEvent] = []

    def handler(
        syke_db_path: str,
        event_db_path: str,
        question: str,
        on_event,
        timeout: float | None,
    ) -> tuple[str, dict[str, object]]:
        assert syke_db_path == "/tmp/replay-syke.db"
        assert event_db_path == "/tmp/replay-events.db"
        assert question == "What changed?"
        assert timeout == 15.0
        if on_event is not None:
            on_event(AskEvent(type="thinking", content="Looking"))
            on_event(AskEvent(type="text", content="Warm answer"))
        return "Warm answer", {"backend": "pi", "duration_ms": 12}

    server = DaemonIpcServer("test_user", handler)
    assert server.start() is True
    try:
        answer, metadata = ask_via_daemon(
            user_id="test_user",
            syke_db_path="/tmp/replay-syke.db",
            event_db_path="/tmp/replay-events.db",
            question="What changed?",
            on_event=seen.append,
            timeout=15,
        )
    finally:
        server.stop()

    assert answer == "Warm answer"
    assert metadata["transport"] == "daemon_ipc"
    assert isinstance(metadata["ipc_roundtrip_ms"], int)
    assert str(metadata["ipc_socket_path"]).endswith(".sock")
    assert [event.type for event in seen] == ["thinking", "text"]
    assert [event.content for event in seen] == ["Looking", "Warm answer"]


def test_daemon_ipc_errors_surface_as_unavailable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)

    def handler(
        syke_db_path: str,
        event_db_path: str,
        question: str,
        on_event,
        timeout: float | None,
    ) -> tuple[str, dict[str, object]]:
        del syke_db_path, event_db_path, question, on_event, timeout
        raise RuntimeError("boom")

    server = DaemonIpcServer("test_user", handler)
    assert server.start() is True
    try:
        with pytest.raises(DaemonIpcUnavailable, match="boom"):
            ask_via_daemon(
                user_id="test_user",
                syke_db_path="/tmp/replay-syke.db",
                event_db_path="/tmp/replay-events.db",
                question="What changed?",
            )
    finally:
        server.stop()
