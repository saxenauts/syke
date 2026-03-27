"""Persistent Pi agent runtime.

Syke treats Pi as the canonical agent runtime. This client manages a long-lived
Pi RPC subprocess, prepares the workspace-local Pi settings, and turns Pi's RPC
event stream into structured runtime results.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from syke.config import CFG, clean_claude_env
from syke.runtime.pi_settings import configure_pi_workspace

logger = logging.getLogger(__name__)

DEFAULT_PI_MODEL = "claude-sonnet-4-20250514"


def resolve_pi_model(model_override: str | None = None) -> str:
    """Resolve the Pi model from override -> config -> default."""
    if model_override:
        return model_override
    try:
        from syke.llm.env import resolve_provider, _resolve_provider_config

        provider = resolve_provider()
        provider_model = _resolve_provider_config(provider).get("model")
        if provider_model:
            return provider_model
    except Exception:
        pass
    if CFG and getattr(CFG, "models", None):
        synthesis_model = getattr(CFG.models, "synthesis", None)
        if synthesis_model:
            return synthesis_model
    return DEFAULT_PI_MODEL


PI_PACKAGE = "@mariozechner/pi-coding-agent"
PI_LOCAL_PREFIX = Path.home() / ".syke" / "pi"
PI_BIN = Path.home() / ".syke" / "bin" / "pi"


def ensure_pi_binary() -> str:
    """Install Pi locally under ~/.syke/ if missing. Returns binary path."""
    if PI_BIN.exists() and os.access(PI_BIN, os.X_OK):
        return str(PI_BIN)

    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError(
            "Syke's Pi runtime requires Node.js (>= 18). Install from https://nodejs.org"
        )

    logger.info("Installing Pi runtime to %s", PI_LOCAL_PREFIX)
    PI_LOCAL_PREFIX.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [npm, "install", "--prefix", str(PI_LOCAL_PREFIX), PI_PACKAGE],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to install Pi runtime: {result.stderr.strip()[:500]}")

    installed_bin = PI_LOCAL_PREFIX / "node_modules" / ".bin" / "pi"
    if not installed_bin.exists():
        raise RuntimeError(f"Pi binary not found after install at {installed_bin}")

    PI_BIN.parent.mkdir(parents=True, exist_ok=True)
    if PI_BIN.exists() or PI_BIN.is_symlink():
        PI_BIN.unlink()
    PI_BIN.symlink_to(installed_bin)

    logger.info("Pi runtime installed: %s -> %s", PI_BIN, installed_bin)
    return str(PI_BIN)


def resolve_pi_binary() -> str:
    """Find or install the Pi binary at ~/.syke/bin/pi."""
    return ensure_pi_binary()


def _extract_assistant_message(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("type")
    if event_type in {"message", "message_start", "message_end", "turn_end"}:
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

    if event_type != "message_update":
        return None

    message = event.get("message")
    if isinstance(message, dict) and message.get("role") == "assistant":
        return message

    inner = _extract_message_update_event(event)
    if not isinstance(inner, dict):
        return None
    for key in ("message", "partial"):
        candidate = inner.get(key)
        if isinstance(candidate, dict) and candidate.get("role") == "assistant":
            return candidate
    return None


def _extract_message_update_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("type") != "message_update":
        return None
    for key in ("assistantMessageEvent", "event"):
        inner = event.get(key)
        if isinstance(inner, dict):
            return inner
    return None


def _extract_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    chunks: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            chunks.append(block["text"])
        elif block_type in {"thinking", "reasoning"} and isinstance(block.get("text"), str):
            chunks.append(block["text"])
    return "".join(chunks)


def _extract_usage_int(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int):
            return value
    return None


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
                    if event_type == "agent_end":
                        self._done.set()
                    elif event_type == "error":
                        self._error = event.get("message", "Unknown Pi error")
                        self._done.set()
                    elif event_type == "response" and event.get("success") is False:
                        self._error = event.get("error", "Pi command failed")
                        self._done.set()

                if callback is not None:
                    try:
                        callback(event)
                    except Exception:
                        logger.debug("Pi event callback failed", exc_info=True)
        except Exception as exc:
            self._error = str(exc)
            self._done.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout=timeout)

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
            if event.get("type") == "text":
                content = event.get("content")
                if isinstance(content, str):
                    text_deltas.append(content)
                continue

            inner = _extract_message_update_event(event)
            if not isinstance(inner, dict):
                continue

            if inner.get("type") == "text_delta":
                delta = inner.get("delta")
                if isinstance(delta, str):
                    text_deltas.append(delta)

            message = _extract_assistant_message(event)
            if not isinstance(message, dict):
                continue
            message_text = _extract_message_text(message)
            if message_text:
                final_text = message_text

        if text_deltas:
            return "".join(text_deltas).strip()
        return (final_text or "").strip()

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

    def get_tool_calls(self) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for event in self.events:
            event_type = event.get("type")
            if event_type in {"tool_call", "tool_execution_start"}:
                calls.append(event)
                continue
            inner = _extract_message_update_event(event)
            if not isinstance(inner, dict):
                continue
            if inner.get("type") in {"toolcall_start", "toolcall_end"}:
                calls.append(event)
        return calls

    def get_usage(self) -> dict[str, int | float | None]:
        latest_message: dict[str, Any] | None = None
        for event in self.events:
            message = _extract_assistant_message(event)
            if isinstance(message, dict):
                latest_message = message

        if latest_message is None:
            return {
                "input_tokens": None,
                "output_tokens": None,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "cost_usd": None,
            }

        usage = latest_message.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        cost = latest_message.get("cost")
        if not isinstance(cost, dict):
            cost = usage.get("cost") if isinstance(usage.get("cost"), dict) else {}

        return {
            "input_tokens": _extract_usage_int(usage, "input_tokens", "input"),
            "output_tokens": _extract_usage_int(usage, "output_tokens", "output"),
            "cache_read_tokens": _extract_usage_int(usage, "cache_read_tokens", "cacheRead"),
            "cache_write_tokens": _extract_usage_int(usage, "cache_write_tokens", "cacheWrite"),
            "cost_usd": cost.get("total") if isinstance(cost.get("total"), (int, float)) else None,
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
            return {"provider": None, "model": None, "response_id": None}

        provider = latest_message.get("provider")
        model = latest_message.get("model")
        response_id = latest_message.get("responseId")
        return {
            "provider": provider if isinstance(provider, str) else None,
            "model": model if isinstance(model, str) else None,
            "response_id": response_id if isinstance(response_id, str) else None,
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


class PiRuntime:
    """Persistent Pi agent runtime."""

    def __init__(
        self,
        workspace_dir: str | Path,
        session_dir: str | Path | None = None,
        model: str | None = None,
    ):
        self.workspace_dir = Path(workspace_dir)
        self.session_dir = Path(session_dir) if session_dir else self.workspace_dir / "sessions"
        self.model = resolve_pi_model(model)
        self._process: subprocess.Popen[str] | None = None
        self._stream: RpcEventStream | None = None
        self._stderr_drain: _StderrDrain | None = None
        self._started_at: float | None = None

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        """Start the Pi process in RPC mode."""
        if self.is_alive:
            logger.info("Pi runtime already alive")
            return

        pi_bin = resolve_pi_binary()
        runtime_env = configure_pi_workspace(
            self.workspace_dir,
            session_dir=self.session_dir,
            model_override=self.model,
        )

        cmd = [
            pi_bin,
            "--mode",
            "rpc",
            "--model",
            self.model,
            "--session-dir",
            str(self.session_dir),
        ]

        logger.info("Starting Pi runtime: %s", " ".join(cmd))
        logger.info("  workspace: %s", self.workspace_dir)
        logger.info("  model: %s", self.model)

        with clean_claude_env():
            env = os.environ.copy()
        env.update(runtime_env)

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.workspace_dir),
            env=env,
            bufsize=1,
            text=True,
        )

        if self._process.stdout is None or self._process.stderr is None:
            raise RuntimeError("Pi failed to expose stdio pipes")

        self._stream = RpcEventStream(self._process.stdout)
        self._stderr_drain = _StderrDrain(self._process.stderr)
        self._stream.start()
        self._stderr_drain.start()
        self._started_at = time.time()

        time.sleep(1.0)
        if not self.is_alive:
            stderr = self._stderr_drain.get_output() if self._stderr_drain else ""
            raise RuntimeError(f"Pi failed to start: {stderr[:500]}")

        logger.info("Pi runtime started (pid=%s)", self._process.pid)

    def stop(self) -> None:
        """Stop the Pi process gracefully."""
        if self._process is None:
            return

        pid = self._process.pid
        logger.info("Stopping Pi runtime (pid=%s)", pid)
        try:
            self._send({"type": "command", "command": "/quit"})
            self._process.wait(timeout=5)
        except (subprocess.TimeoutExpired, BrokenPipeError, OSError):
            logger.warning("Pi did not quit gracefully, terminating")
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()

        self._process = None
        self._stream = None
        self._stderr_drain = None
        logger.info("Pi runtime stopped (was pid=%s)", pid)

    def prompt(
        self,
        text: str,
        *,
        timeout: float | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> "PiCycleResult":
        """Send a prompt to Pi and wait for completion."""
        if not self.is_alive or self._stream is None:
            raise RuntimeError("Pi runtime is not running")

        self._stream.set_callback(on_event)
        self._stream.reset()

        self._send({"type": "prompt", "message": text})
        start = time.time()
        completed = self._stream.wait(timeout=timeout)
        duration_ms = int((time.time() - start) * 1000)

        events = self._stream.events
        usage = self._stream.get_usage()
        message_metadata = self._stream.get_message_metadata()
        assistant_error = self._stream.get_assistant_error()
        result = PiCycleResult(
            status="completed" if completed and not self._stream.error and not assistant_error else "error",
            output=self._stream.get_output(),
            thinking=self._stream.get_thinking_chunks(),
            tool_calls=self._stream.get_tool_calls(),
            events=events,
            duration_ms=duration_ms,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            cache_write_tokens=usage["cache_write_tokens"],
            cost_usd=usage["cost_usd"],
            provider=message_metadata["provider"],
            response_model=message_metadata["model"],
            response_id=message_metadata["response_id"],
            error=self._stream.error or assistant_error,
        )
        self._stream.set_callback(None)

        if not completed and result.error is None:
            result.status = "timeout"
            result.error = f"Pi did not complete within {timeout}s"
        return result

    def _send(self, message: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Pi process not available")
        try:
            line = json.dumps(message) + "\n"
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(f"Failed to send to Pi: {exc}") from exc

    @property
    def uptime_seconds(self) -> float | None:
        if self._started_at and self.is_alive:
            return time.time() - self._started_at
        return None

    def status(self) -> dict[str, Any]:
        return {
            "alive": self.is_alive,
            "model": self.model,
            "workspace": str(self.workspace_dir),
            "session_dir": str(self.session_dir),
            "pid": self._process.pid if self._process else None,
            "uptime_s": self.uptime_seconds,
        }


class PiCycleResult:
    """Result of a single Pi prompt/response cycle."""

    def __init__(
        self,
        status: str,
        output: str,
        thinking: list[str],
        tool_calls: list[dict[str, Any]],
        events: list[dict[str, Any]],
        duration_ms: int,
        input_tokens: int | None,
        output_tokens: int | None,
        cache_read_tokens: int | None,
        cache_write_tokens: int | None,
        cost_usd: float | None,
        provider: str | None,
        response_model: str | None,
        response_id: str | None,
        error: str | None = None,
    ):
        self.status = status
        self.output = output
        self.thinking = thinking
        self.tool_calls = tool_calls
        self.events = events
        self.duration_ms = duration_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cache_write_tokens = cache_write_tokens
        self.cost_usd = cost_usd
        self.provider = provider
        self.response_model = response_model
        self.response_id = response_id
        self.error = error

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def __repr__(self) -> str:
        return (
            f"PiCycleResult(status={self.status!r}, output_len={len(self.output)}, "
            f"tool_calls={len(self.tool_calls)}, duration_ms={self.duration_ms})"
        )


PiClient = PiRuntime
