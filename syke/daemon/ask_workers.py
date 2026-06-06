"""Daemon-owned temporary ask worker supervisor."""

from __future__ import annotations

import json
import logging
import os
import selectors
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TextIO, cast

from syke.config import ASK_MAX_PARALLEL, ASK_TIMEOUT
from syke.llm.backends import AskEvent

logger = logging.getLogger(__name__)


class DaemonAskWorkerError(RuntimeError):
    """Raised when a daemon-owned ask worker cannot produce a valid result."""


class DaemonAskCapacityExceeded(DaemonAskWorkerError):
    """Raised when all daemon-owned temporary ask workers are already in use."""


@dataclass
class DaemonAskWorkerSupervisor:
    """Runs temporary ask workers as child processes owned by the daemon."""

    max_workers: int = ASK_MAX_PARALLEL
    command: Sequence[str] | None = None
    capacity_wait_s: float = 5.0
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _children: set[subprocess.Popen[str]] = field(default_factory=set, init=False)
    _semaphore: threading.BoundedSemaphore | None = field(init=False)

    def __post_init__(self) -> None:
        self._semaphore = (
            threading.BoundedSemaphore(self.max_workers) if self.max_workers > 0 else None
        )

    def ask(
        self,
        *,
        user_id: str,
        syke_db_path: str,
        question: str,
        on_event: Callable[[AskEvent], None] | None,
        timeout: float | None,
        transport_details: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        wait_started = time.monotonic()
        if not self._acquire_capacity(timeout):
            raise DaemonAskCapacityExceeded(
                f"ask worker capacity exceeded ({self.max_workers} temporary workers)"
            )
        worker_slot_wait_ms = int((time.monotonic() - wait_started) * 1000)
        try:
            started = time.monotonic()
            answer, metadata = self._run_child(
                {
                    "user_id": user_id,
                    "syke_db_path": syke_db_path,
                    "question": question,
                    "timeout": timeout,
                    "transport_details": {
                        **transport_details,
                        "worker_slot_wait_ms": worker_slot_wait_ms,
                    },
                },
                on_event=on_event,
                timeout=timeout,
            )
            enriched = {**transport_details, **metadata}
            enriched.setdefault("transport", "daemon_worker")
            enriched["worker_roundtrip_ms"] = int((time.monotonic() - started) * 1000)
            enriched.setdefault("worker_slot_wait_ms", worker_slot_wait_ms)
            return answer, enriched
        finally:
            self._release_capacity()

    def stop(self) -> None:
        with self._lock:
            children = list(self._children)
        for child in children:
            if child.poll() is not None:
                continue
            try:
                child.terminate()
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
            except OSError:
                logger.debug("Failed to stop ask worker %s", child.pid, exc_info=True)

    def _acquire_capacity(self, timeout: float | None) -> bool:
        semaphore = self._semaphore
        if semaphore is None:
            return True
        wait_s = self.capacity_wait_s
        if isinstance(timeout, (int, float)) and timeout > 0:
            wait_s = min(wait_s, max(float(timeout) - 1.0, 0.0))
        return semaphore.acquire(timeout=wait_s)

    def _release_capacity(self) -> None:
        if self._semaphore is not None:
            self._semaphore.release()

    def _worker_command(self) -> list[str]:
        if self.command is not None:
            return list(self.command)
        return [sys.executable, "-m", "syke.daemon.ask_worker_child"]

    def _worker_env(self) -> dict[str, str]:
        from syke.pi_state import build_pi_agent_env, get_default_provider
        from syke.runtime.child_env import build_child_process_env

        provider = os.getenv("SYKE_PROVIDER") or get_default_provider()
        return build_child_process_env(build_pi_agent_env(), provider=provider)

    def _start_child(self) -> subprocess.Popen[str]:
        child = subprocess.Popen(
            self._worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=self._worker_env(),
        )
        with self._lock:
            self._children.add(child)
        return child

    def _run_child(
        self,
        request: dict[str, object],
        *,
        on_event: Callable[[AskEvent], None] | None,
        timeout: float | None,
    ) -> tuple[str, dict[str, object]]:
        child = self._start_child()
        stderr_lines: list[str] = []
        try:
            assert child.stdin is not None
            assert child.stdout is not None
            assert child.stderr is not None

            child.stdin.write(json.dumps(request, default=str))
            child.stdin.close()

            deadline = time.monotonic() + (
                float(timeout) + 5.0
                if isinstance(timeout, (int, float)) and timeout > 0
                else float(ASK_TIMEOUT) + 5.0
            )
            selector = selectors.DefaultSelector()
            selector.register(child.stdout, selectors.EVENT_READ, "stdout")
            selector.register(child.stderr, selectors.EVENT_READ, "stderr")

            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("ask worker timed out")
                    events = selector.select(timeout=min(remaining, 0.25))
                    if not events:
                        if child.poll() is not None:
                            self._drain_closed_pipes(selector)
                        continue
                    for key, _mask in events:
                        stream = cast(TextIO, key.fileobj)
                        line = stream.readline()
                        if not line:
                            selector.unregister(stream)
                            continue
                        if key.data == "stderr":
                            stderr_lines.append(line.rstrip())
                            continue
                        result = self._handle_stdout_line(line, on_event=on_event)
                        if result is not None:
                            self._wait_after_result(child)
                            return result
            finally:
                selector.close()

            returncode = child.wait(timeout=2)
            stderr = "\n".join(stderr_lines[-20:])
            raise DaemonAskWorkerError(
                f"ask worker exited without result (exit {returncode})"
                + (f": {stderr}" if stderr else "")
            )
        except TimeoutError as exc:
            self._kill_child(child)
            raise DaemonAskWorkerError(str(exc)) from exc
        finally:
            with self._lock:
                self._children.discard(child)

    def _drain_closed_pipes(self, selector: selectors.BaseSelector) -> None:
        for key in list(selector.get_map().values()):
            stream = cast(TextIO, key.fileobj)
            line = stream.readline()
            if not line:
                selector.unregister(stream)

    def _handle_stdout_line(
        self,
        line: str,
        *,
        on_event: Callable[[AskEvent], None] | None,
    ) -> tuple[str, dict[str, object]] | None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DaemonAskWorkerError(f"invalid ask worker JSON: {exc}") from exc
        if not isinstance(message, dict):
            raise DaemonAskWorkerError("ask worker message was not a JSON object")
        message_type = message.get("type")
        if message_type == "event":
            raw_event = message.get("event")
            if callable(on_event) and isinstance(raw_event, dict):
                event_type = raw_event.get("type")
                content = raw_event.get("content")
                metadata = raw_event.get("metadata")
                if event_type in {"thinking", "text", "tool_call"} and isinstance(content, str):
                    on_event(
                        AskEvent(
                            type=event_type,
                            content=content,
                            metadata=metadata if isinstance(metadata, dict) else None,
                        )
                    )
            return None
        if message_type == "result":
            answer = message.get("answer")
            metadata = message.get("metadata")
            if not isinstance(answer, str) or not isinstance(metadata, dict):
                raise DaemonAskWorkerError("ask worker result missing answer or metadata")
            return answer, dict(metadata)
        if message_type == "error":
            error = message.get("error")
            detail = error if isinstance(error, str) and error else "ask worker error"
            raise DaemonAskWorkerError(detail)
        raise DaemonAskWorkerError(f"unexpected ask worker message type: {message_type!r}")

    def _kill_child(self, child: subprocess.Popen[str]) -> None:
        if child.poll() is not None:
            return
        child.kill()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            logger.debug("Ask worker %s did not exit after kill", child.pid)

    def _wait_after_result(self, child: subprocess.Popen[str]) -> None:
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._kill_child(child)
