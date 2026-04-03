"""Local IPC bridge for daemon-served ask requests."""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import threading
import time
from collections.abc import Callable
from hashlib import sha1
from pathlib import Path
from tempfile import gettempdir
from typing import Any

from syke.config import ASK_TIMEOUT
from syke.llm.backends import AskEvent

logger = logging.getLogger(__name__)

IPC_PROTOCOL_VERSION = 1
IPC_DIR = Path(os.path.expanduser("~/.config/syke"))


class DaemonIpcUnavailable(RuntimeError):
    """Raised when the local daemon IPC transport is not available."""


class DaemonIpcProtocolError(RuntimeError):
    """Raised when daemon IPC returns an invalid response."""


class _DaemonIpcClientDisconnected(RuntimeError):
    """Raised when the IPC client disconnects before the server finishes writing."""


def _unlink_socket(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.debug("Failed to remove daemon IPC socket %s", path, exc_info=True)


def socket_path_for_user(user_id: str) -> Path:
    safe_user = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in user_id)
    safe_user = safe_user.strip("_") or "default"
    preferred = IPC_DIR / f"daemon-{safe_user}.sock"
    if len(str(preferred)) <= 96:
        return preferred
    digest = sha1(str(preferred).encode("utf-8")).hexdigest()[:16]
    return Path(gettempdir()) / f"syke-{digest}.sock"


def daemon_ipc_status(user_id: str) -> dict[str, object]:
    socket_path = socket_path_for_user(user_id)
    unix_supported = hasattr(socket, "AF_UNIX")
    socket_present = socket_path.exists()
    if not unix_supported:
        detail = "Unix domain sockets unavailable on this platform"
    elif socket_present:
        detail = f"daemon IPC socket present at {socket_path}"
    else:
        detail = f"daemon IPC socket missing at {socket_path}; ask falls back to direct runtime"
    return {
        "ok": unix_supported and socket_present,
        "supported": unix_supported,
        "socket_present": socket_present,
        "socket_path": str(socket_path),
        "detail": detail,
    }


def daemon_runtime_status(user_id: str, *, timeout: float = 1.5) -> dict[str, object]:
    """Fetch the daemon's current warm runtime binding over local IPC."""
    base = {
        "ok": False,
        "reachable": False,
        "alive": False,
        "provider": None,
        "model": None,
        "runtime_pid": None,
        "daemon_pid": None,
        "uptime_s": None,
        "binding_error": None,
        "detail": None,
    }
    if not hasattr(socket, "AF_UNIX"):
        return {
            **base,
            "detail": "Unix domain sockets unavailable on this platform",
        }

    socket_path = socket_path_for_user(user_id)
    if not socket_path.exists():
        return {
            **base,
            "detail": f"daemon IPC socket missing at {socket_path}",
        }

    request = {
        "protocol": IPC_PROTOCOL_VERSION,
        "type": "runtime_status",
        "user_id": user_id,
    }

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(max(timeout, 0.1))
            sock.connect(str(socket_path))
            sock.sendall(_encode_message(request))
            with sock.makefile("r", encoding="utf-8") as reader:
                raw_line = reader.readline()
        if not raw_line:
            raise DaemonIpcProtocolError("Daemon IPC connection closed without runtime status")
        message = _decode_message(raw_line)
        message_type = message.get("type")
        if message_type == "error":
            error = message.get("error")
            detail = error if isinstance(error, str) and error else "daemon IPC error"
            return {
                **base,
                "reachable": True,
                "detail": detail,
            }
        if message_type != "runtime_status":
            raise DaemonIpcProtocolError(
                f"Unexpected daemon IPC message type: {message_type!r}"
            )
        runtime = message.get("runtime")
        if not isinstance(runtime, dict):
            raise DaemonIpcProtocolError("Daemon IPC runtime status was not a JSON object")

        alive = bool(runtime.get("alive"))
        provider = runtime.get("provider") if isinstance(runtime.get("provider"), str) else None
        model = runtime.get("model") if isinstance(runtime.get("model"), str) else None
        binding_error = (
            runtime.get("binding_error")
            if isinstance(runtime.get("binding_error"), str)
            else None
        )
        detail = (
            f"{provider or '(unknown)'} / {model or '(unknown)'}"
            if alive
            else binding_error or "daemon reachable, but no warm runtime is active"
        )
        return {
            **base,
            "ok": alive,
            "reachable": True,
            "alive": alive,
            "provider": provider,
            "model": model,
            "runtime_pid": runtime.get("pid"),
            "daemon_pid": message.get("daemon_pid"),
            "uptime_s": runtime.get("uptime_s"),
            "binding_error": binding_error,
            "detail": detail,
        }
    except (OSError, TimeoutError, DaemonIpcProtocolError) as exc:
        return {
            **base,
            "detail": str(exc),
        }


def _encode_message(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, default=str) + "\n").encode("utf-8")


def _decode_message(raw_line: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        raise DaemonIpcProtocolError(f"Invalid daemon IPC JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise DaemonIpcProtocolError("Daemon IPC response was not a JSON object")
    return payload


class _ThreadingUnixStreamServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class DaemonIpcServer:
    """Simple JSONL-over-UDS server for warm-runtime ask reuse."""

    def __init__(
        self,
        user_id: str,
        ask_handler: Callable[
            [str, str, str, Callable[[AskEvent], None] | None, float | None],
            tuple[str, dict[str, object]],
        ],
        runtime_status_handler: Callable[[], dict[str, Any]] | None = None,
    ):
        self.user_id = user_id
        self.ask_handler = ask_handler
        self.runtime_status_handler = runtime_status_handler
        self.socket_path = socket_path_for_user(user_id)
        self._server: _ThreadingUnixStreamServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return hasattr(socket, "AF_UNIX")

    def start(self) -> bool:
        if not self.enabled:
            logger.info("Daemon IPC disabled: Unix domain sockets are unavailable")
            return False

        try:
            self.socket_path.parent.mkdir(parents=True, exist_ok=True)
            _unlink_socket(self.socket_path)
        except OSError:
            logger.info(
                "Daemon IPC disabled: could not prepare socket path %s",
                self.socket_path,
                exc_info=True,
            )
            return False
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def _send(self, payload: dict[str, Any]) -> None:
                try:
                    self.wfile.write(_encode_message(payload))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                    raise _DaemonIpcClientDisconnected("daemon IPC client disconnected") from exc

            def handle(self) -> None:
                raw_request = self.rfile.readline()
                if not raw_request:
                    return

                try:
                    request = _decode_message(raw_request.decode("utf-8"))
                    if request.get("protocol") != IPC_PROTOCOL_VERSION:
                        raise DaemonIpcProtocolError(
                            f"Unsupported daemon IPC protocol: {request.get('protocol')!r}"
                        )

                    request_user = request.get("user_id")
                    if request_user != outer.user_id:
                        raise DaemonIpcProtocolError(
                            f"Daemon IPC user mismatch: {request_user!r} != {outer.user_id!r}"
                        )
                    request_type = request.get("type")
                    if request_type == "runtime_status":
                        if outer.runtime_status_handler is None:
                            raise DaemonIpcProtocolError(
                                "Daemon IPC runtime status is not available"
                            )
                        runtime = outer.runtime_status_handler()
                        if not isinstance(runtime, dict):
                            raise DaemonIpcProtocolError(
                                "Daemon IPC runtime status handler returned invalid data"
                            )
                        self._send(
                            {
                                "type": "runtime_status",
                                "runtime": runtime,
                                "daemon_pid": os.getpid(),
                            }
                        )
                        return
                    if request_type != "ask":
                        raise DaemonIpcProtocolError(
                            f"Unsupported daemon IPC request type: {request_type!r}"
                        )

                    syke_db_path = request.get("syke_db_path")
                    event_db_path = request.get("event_db_path")
                    question = request.get("question")
                    timeout = request.get("timeout")
                    stream = bool(request.get("stream"))

                    if not isinstance(syke_db_path, str) or not syke_db_path:
                        raise DaemonIpcProtocolError("Missing syke_db_path in daemon IPC request")
                    if not isinstance(event_db_path, str) or not event_db_path:
                        raise DaemonIpcProtocolError("Missing event_db_path in daemon IPC request")
                    if not isinstance(question, str) or not question:
                        raise DaemonIpcProtocolError("Missing question in daemon IPC request")
                    timeout_value = (
                        float(timeout) if isinstance(timeout, int | float) and timeout > 0 else None
                    )

                    def emit(event: AskEvent) -> None:
                        self._send(
                            {
                                "type": "event",
                                "event": {
                                    "type": event.type,
                                    "content": event.content,
                                    "metadata": event.metadata,
                                },
                            }
                        )

                    answer, metadata = outer.ask_handler(
                        syke_db_path,
                        event_db_path,
                        question,
                        emit if stream else None,
                        timeout_value,
                    )
                    self._send(
                        {
                            "type": "result",
                            "answer": answer,
                            "metadata": metadata,
                            "daemon_pid": os.getpid(),
                        }
                    )
                except _DaemonIpcClientDisconnected:
                    logger.debug("Daemon IPC client disconnected before request completion")
                    return
                except Exception as exc:
                    logger.warning("Daemon IPC request failed", exc_info=True)
                    try:
                        self._send(
                            {
                                "type": "error",
                                "error": str(exc),
                                "daemon_pid": os.getpid(),
                            }
                        )
                    except _DaemonIpcClientDisconnected:
                        logger.debug("Daemon IPC client disconnected while sending error response")
                        return

        try:
            self._server = _ThreadingUnixStreamServer(str(self.socket_path), Handler)
        except OSError:
            logger.info(
                "Daemon IPC disabled: could not bind socket %s",
                self.socket_path,
                exc_info=True,
            )
            self._server = None
            _unlink_socket(self.socket_path)
            return False
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        try:
            self.socket_path.chmod(0o600)
        except OSError:
            logger.debug(
                "Failed to chmod daemon IPC socket %s",
                self.socket_path,
                exc_info=True,
            )
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        _unlink_socket(self.socket_path)


def ask_via_daemon(
    *,
    user_id: str,
    syke_db_path: str,
    event_db_path: str,
    question: str,
    on_event: Callable[[AskEvent], None] | None = None,
    timeout: float | None = None,
) -> tuple[str, dict[str, object]]:
    """Send an ask request to the local daemon over Unix domain sockets."""
    if not hasattr(socket, "AF_UNIX"):
        raise DaemonIpcUnavailable("Unix domain sockets are unavailable on this platform")

    socket_path = socket_path_for_user(user_id)
    if not socket_path.exists():
        raise DaemonIpcUnavailable(f"Daemon IPC socket not found at {socket_path}")

    request = {
        "protocol": IPC_PROTOCOL_VERSION,
        "type": "ask",
        "user_id": user_id,
        "syke_db_path": syke_db_path,
        "event_db_path": event_db_path,
        "question": question,
        "timeout": timeout,
        "stream": on_event is not None,
    }

    started = time.monotonic()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            effective_timeout = (
                float(timeout)
                if isinstance(timeout, (int, float)) and timeout > 0
                else float(ASK_TIMEOUT)
            )
            sock.settimeout(effective_timeout + 5.0)
            sock.connect(str(socket_path))
            sock.sendall(_encode_message(request))

            with sock.makefile("r", encoding="utf-8") as reader:
                for line in reader:
                    message = _decode_message(line)
                    message_type = message.get("type")

                    if message_type == "event":
                        raw_event = message.get("event")
                        if callable(on_event) and isinstance(raw_event, dict):
                            event_type = raw_event.get("type")
                            content = raw_event.get("content")
                            if isinstance(event_type, str) and isinstance(content, str):
                                metadata = raw_event.get("metadata")
                                on_event(
                                    AskEvent(
                                        type=event_type,
                                        content=content,
                                        metadata=metadata if isinstance(metadata, dict) else None,
                                    )
                                )
                        continue

                    if message_type == "result":
                        answer = message.get("answer")
                        metadata = message.get("metadata")
                        if not isinstance(answer, str) or not isinstance(metadata, dict):
                            raise DaemonIpcProtocolError(
                                "Daemon IPC result missing answer or metadata"
                            )
                        response = dict(metadata)
                        response.setdefault("transport", "daemon_ipc")
                        response.setdefault("daemon_pid", message.get("daemon_pid"))
                        response["ipc_roundtrip_ms"] = int((time.monotonic() - started) * 1000)
                        response["ipc_socket_path"] = str(socket_path)
                        return answer, response

                    if message_type == "error":
                        error = message.get("error")
                        detail = error if isinstance(error, str) and error else "daemon IPC error"
                        raise DaemonIpcUnavailable(detail)

                    raise DaemonIpcProtocolError(
                        f"Unexpected daemon IPC message type: {message_type!r}"
                    )
    except (OSError, TimeoutError) as exc:
        raise DaemonIpcUnavailable(str(exc)) from exc

    raise DaemonIpcProtocolError("Daemon IPC connection closed without a result")
