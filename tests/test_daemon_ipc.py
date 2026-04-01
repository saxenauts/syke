from __future__ import annotations

import json
import logging
import socket
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from syke.daemon.ipc import (
    IPC_PROTOCOL_VERSION,
    DaemonIpcServer,
    DaemonIpcUnavailable,
    _encode_message,
    ask_via_daemon,
    socket_path_for_user,
)
from syke.llm.backends import AskEvent


def _unix_socket_bind_is_available(path: Path) -> bool:
    if not hasattr(socket, "AF_UNIX"):
        return False

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.bind(str(path))
    except OSError:
        return False
    finally:
        probe.close()
        path.unlink(missing_ok=True)

    return True


def _require_unix_socket_bind(tmp_path: Path) -> None:
    if not _unix_socket_bind_is_available(tmp_path / "probe.sock"):
        pytest.skip("Unix socket bind not permitted in this environment")


def _start_server_or_skip(server: DaemonIpcServer) -> None:
    if not server.start():
        pytest.skip("Unix domain socket bind unavailable in this environment")


def test_daemon_ipc_round_trip_streams_events(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)
    _require_unix_socket_bind(tmp_path)
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
    _start_server_or_skip(server)
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
    _require_unix_socket_bind(tmp_path)

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
    _start_server_or_skip(server)
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


def test_daemon_ipc_client_disconnect_is_not_reported_as_request_failure(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)
    _require_unix_socket_bind(tmp_path)
    caplog.set_level(logging.WARNING, logger="syke.daemon.ipc")

    def handler(
        syke_db_path: str,
        event_db_path: str,
        question: str,
        on_event,
        timeout: float | None,
    ) -> tuple[str, dict[str, object]]:
        del syke_db_path, event_db_path, question, timeout
        if on_event is not None:
            on_event(AskEvent(type="thinking", content="Looking"))
        time.sleep(0.05)
        return "Warm answer", {"backend": "pi", "duration_ms": 12}

    server = DaemonIpcServer("test_user", handler)
    _start_server_or_skip(server)
    try:
        request = {
            "protocol": IPC_PROTOCOL_VERSION,
            "type": "ask",
            "user_id": "test_user",
            "syke_db_path": "/tmp/replay-syke.db",
            "event_db_path": "/tmp/replay-events.db",
            "question": "What changed?",
            "timeout": None,
            "stream": True,
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(socket_path_for_user("test_user")))
            sock.sendall(_encode_message(request))
            with sock.makefile("r", encoding="utf-8") as reader:
                first_message = json.loads(reader.readline())
            assert first_message["type"] == "event"
        time.sleep(0.1)

        answer, metadata = ask_via_daemon(
            user_id="test_user",
            syke_db_path="/tmp/replay-syke.db",
            event_db_path="/tmp/replay-events.db",
            question="What changed?",
        )
    finally:
        server.stop()

    assert answer == "Warm answer"
    assert metadata["transport"] == "daemon_ipc"
    assert "Daemon IPC request failed" not in caplog.text


def test_daemon_ipc_start_returns_false_when_socket_bind_is_denied(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("syke.daemon.ipc.IPC_DIR", tmp_path)

    def handler(
        syke_db_path: str,
        event_db_path: str,
        question: str,
        on_event,
        timeout: float | None,
    ) -> tuple[str, dict[str, object]]:
        del syke_db_path, event_db_path, question, on_event, timeout
        return "Warm answer", {"backend": "pi", "duration_ms": 12}

    server = DaemonIpcServer("test_user", handler)

    with patch(
        "syke.daemon.ipc._ThreadingUnixStreamServer",
        side_effect=PermissionError(1, "Operation not permitted"),
    ):
        assert server.start() is False

    assert not server.socket_path.exists()
