"""
Persistent Pi agent runtime.

Manages a long-lived Pi process over RPC (JSON over stdio).
The process stays alive across synthesis cycles, managed by the Syke daemon.

Protocol: Pi RPC mode — JSONL over stdin/stdout.
Session: Persistent, stored in session_dir, auto-compacted by Pi.
Sandbox: OS-enforced via .pi/sandbox.json in workspace.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from syke.config import CFG, clean_claude_env

logger = logging.getLogger(__name__)

# ── Model resolution ──────────────────────────────────────────────────

DEFAULT_PI_MODEL = "claude-sonnet-4-20250514"


def resolve_pi_model(model_override: str | None = None) -> str:
    """Resolve the Pi model from override → config → default."""
    if model_override:
        return model_override
    if CFG and hasattr(CFG, "models") and CFG.models:
        sync_model = getattr(CFG.models, "sync", None)
        if sync_model:
            return sync_model
    return DEFAULT_PI_MODEL


def resolve_pi_binary() -> str:
    """Find the Pi binary — ~/.syke/bin/pi, then system PATH."""
    local_bin = Path.home() / ".syke" / "bin" / "pi"
    if local_bin.exists() and os.access(local_bin, os.X_OK):
        return str(local_bin)
    system_bin = shutil.which("pi")
    if system_bin:
        return system_bin
    raise FileNotFoundError(
        "Pi binary not found. Install with: npm install -g @mariozechner/pi-coding-agent "
        "or download a standalone binary to ~/.syke/bin/pi"
    )


# ── RPC Event Stream Reader ──────────────────────────────────────────

class RpcEventStream:
    """
    Threaded reader for Pi's RPC stdout stream.

    Collects JSONL events and provides blocking wait for cycle completion.
    """

    def __init__(self, stdout):
        self._stdout = stdout
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._error: str | None = None
        self._last_reset_at = time.monotonic()

    def start(self):
        self._thread.start()

    def _read_loop(self):
        try:
            for line in self._stdout:
                received_at = time.monotonic()
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(f"Non-JSON line from Pi: {line[:200]}")
                    continue

                with self._lock:
                    if received_at < self._last_reset_at:
                        continue

                    self._events.append(event)

                    event_type = event.get("type", "")
                    if event_type == "agent_end":
                        self._done.set()
                    elif event_type == "error":
                        self._error = event.get("message", "Unknown Pi error")
                        self._done.set()
        except Exception as e:
            self._error = str(e)
            self._done.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for agent_end or error. Returns True if completed."""
        return self._done.wait(timeout=timeout)

    def reset(self):
        """Reset for next prompt cycle."""
        time.sleep(0.1)
        with self._lock:
            self._events.clear()
            self._done.clear()
            self._error = None
            self._last_reset_at = time.monotonic()

    @property
    def events(self) -> list[dict]:
        with self._lock:
            return list(self._events)

    @property
    def error(self) -> str | None:
        return self._error

    def get_output(self) -> str:
        """Extract the final text output from the event stream."""
        texts = []
        for event in self.events:
            if event.get("type") == "text":
                texts.append(event.get("content", ""))
            elif event.get("type") == "message" and event.get("role") == "assistant":
                content = event.get("content", "")
                if isinstance(content, str):
                    texts.append(content)
        return "\n".join(texts).strip()

    def get_tool_calls(self) -> list[dict]:
        """Extract tool calls from the event stream for auditing."""
        return [e for e in self.events if e.get("type") == "tool_call"]


class _StderrDrain:
    """Threaded stderr reader to prevent Pi from blocking on a full pipe."""

    def __init__(self, stderr):
        self._stderr = stderr
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)

    def start(self):
        self._thread.start()

    def _read_loop(self):
        try:
            for line in self._stderr:
                line = line.rstrip()
                if not line:
                    continue
                with self._lock:
                    self._lines.append(line)
                logger.debug("Pi stderr: %s", line)
        except Exception as e:
            logger.debug("Pi stderr drain stopped: %s", e)

    def get_output(self) -> str:
        with self._lock:
            return "\n".join(self._lines)


# ── PiRuntime ─────────────────────────────────────────────────────────

class PiRuntime:
    """
    Persistent Pi agent runtime.

    Lifecycle:
    - start(): spawns Pi in RPC mode, stays alive across cycles
    - prompt(): sends a synthesis prompt, waits for completion
    - stop(): cleanly terminates Pi

    Managed by the Syke daemon as a singleton (see syke.runtime).
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        session_dir: str | Path | None = None,
        model: str | None = None,
    ):
        self.workspace_dir = Path(workspace_dir)
        self.session_dir = Path(session_dir) if session_dir else self.workspace_dir / "sessions"
        self.model = resolve_pi_model(model)
        self._process: subprocess.Popen | None = None
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
        cmd = [
            pi_bin,
            "--mode", "rpc",
            "--model", self.model,
            "--session-dir", str(self.session_dir),
        ]

        logger.info(f"Starting Pi runtime: {' '.join(cmd)}")
        logger.info(f"  workspace: {self.workspace_dir}")
        logger.info(f"  model: {self.model}")

        with clean_claude_env():
            env = os.environ.copy()

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

        self._stream = RpcEventStream(self._process.stdout)
        self._stderr_drain = _StderrDrain(self._process.stderr)
        self._stream.start()
        self._stderr_drain.start()
        self._started_at = time.time()

        # Give Pi a moment to initialize
        time.sleep(1.0)

        if not self.is_alive:
            stderr = self._stderr_drain.get_output() if self._stderr_drain else ""
            raise RuntimeError(f"Pi failed to start: {stderr[:500]}")

        logger.info(f"Pi runtime started (pid={self._process.pid})")

    def stop(self) -> None:
        """Stop the Pi process gracefully."""
        if self._process is None:
            return

        pid = self._process.pid
        logger.info(f"Stopping Pi runtime (pid={pid})")

        try:
            # Send quit command
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
        logger.info(f"Pi runtime stopped (was pid={pid})")

    def prompt(
        self,
        text: str,
        *,
        timeout: float | None = None,
    ) -> PiCycleResult:
        """
        Send a prompt to Pi and wait for completion.

        Returns a PiCycleResult with the output text, tool calls, and status.
        """
        if not self.is_alive:
            raise RuntimeError("Pi runtime is not running")

        self._stream.reset()

        # Send the prompt
        self._send({
            "type": "prompt",
            "content": text,
        })

        # Wait for completion
        start = time.time()
        completed = self._stream.wait(timeout=timeout)
        duration_ms = int((time.time() - start) * 1000)

        if not completed:
            return PiCycleResult(
                status="timeout",
                output="",
                tool_calls=[],
                events=self._stream.events,
                duration_ms=duration_ms,
                error=f"Pi did not complete within {timeout}s",
            )

        if self._stream.error:
            return PiCycleResult(
                status="error",
                output="",
                tool_calls=[],
                events=self._stream.events,
                duration_ms=duration_ms,
                error=self._stream.error,
            )

        return PiCycleResult(
            status="completed",
            output=self._stream.get_output(),
            tool_calls=self._stream.get_tool_calls(),
            events=self._stream.events,
            duration_ms=duration_ms,
        )

    def _send(self, message: dict) -> None:
        """Send a JSON message to Pi's stdin."""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Pi process not available")
        try:
            line = json.dumps(message) + "\n"
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise RuntimeError(f"Failed to send to Pi: {e}") from e

    @property
    def uptime_seconds(self) -> float | None:
        if self._started_at and self.is_alive:
            return time.time() - self._started_at
        return None

    def status(self) -> dict:
        """Runtime status for diagnostics."""
        return {
            "alive": self.is_alive,
            "model": self.model,
            "workspace": str(self.workspace_dir),
            "session_dir": str(self.session_dir),
            "pid": self._process.pid if self._process else None,
            "uptime_s": self.uptime_seconds,
        }


# ── Result container ──────────────────────────────────────────────────

class PiCycleResult:
    """Result of a single Pi prompt/response cycle."""

    def __init__(
        self,
        status: str,
        output: str,
        tool_calls: list[dict],
        events: list[dict],
        duration_ms: int,
        error: str | None = None,
    ):
        self.status = status
        self.output = output
        self.tool_calls = tool_calls
        self.events = events
        self.duration_ms = duration_ms
        self.error = error

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def __repr__(self):
        return (
            f"PiCycleResult(status={self.status!r}, "
            f"output_len={len(self.output)}, "
            f"tool_calls={len(self.tool_calls)}, "
            f"duration_ms={self.duration_ms})"
        )


# Backwards-compatible alias — tests and older consumers reference PiClient
PiClient = PiRuntime