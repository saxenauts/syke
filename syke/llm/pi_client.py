"""Pi RPC client — sync subprocess.Popen, JSON-lines protocol.

Mirrors the proven pattern from tests/pi_rpc_raw.py: threaded stdout
collector, newline-delimited JSON on stdin, wait for agent_end between turns.

Protocol notes (from live testing):
  - No session header on startup — Pi just waits for commands
  - Prompt field is 'message' (NOT 'content')
  - Text deltas nested: message_update -> assistantMessageEvent.type='text_delta', .delta
  - Must wait for agent_end before sending next prompt
  - Commands: JSON objects with 'type' field, newline-terminated on stdin
  - Events stream as AgentSessionEvent objects on stdout
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from typing import Any

from syke.config_file import SykeConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


def resolve_pi_model(config: SykeConfig) -> str:
    """Map a SykeConfig provider + model into a Pi --model string.

    Rules:
      azure    -> 'azure-openai-responses/{model}'
      anthropic -> 'anthropic/{model}'
      else     -> 'openai/{model}'

    For azure the model may also live under config.providers['azure']['model'].
    """
    provider = config.provider or "openai"
    model = config.models.synthesis

    if provider == "azure":
        azure_cfg = config.providers.get("azure", {})
        model = azure_cfg.get("model", model)
        return f"azure-openai-responses/{model}"

    if provider == "anthropic":
        return f"anthropic/{model}"

    return f"openai/{model}"


# ---------------------------------------------------------------------------
# Threaded stdout collector (from proven test pattern)
# ---------------------------------------------------------------------------


class _StdoutCollector:
    """Thread-safe collector for stdout lines with a movable cursor."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self.lines: list[str] = []
        self.lock = threading.Lock()
        self.cursor: int = 0

        def _reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                stripped = line.rstrip()
                if stripped:
                    with self.lock:
                        self.lines.append(stripped)

        self._thread = threading.Thread(target=_reader, daemon=True)
        self._thread.start()

    # -- high-level drains ---------------------------------------------------

    def drain_to_agent_end(self, timeout: float = 60) -> dict[str, Any]:
        """Collect events from cursor until agent_end.

        Returns {"output": str, "events": list[dict], "usage": dict}.
        """
        collected_text = ""
        events: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        start = time.time()

        while time.time() - start < timeout:
            with self.lock:
                current_len = len(self.lines)

            if self.cursor >= current_len:
                time.sleep(0.05)
                continue

            with self.lock:
                snapshot = list(self.lines[self.cursor:current_len])

            for idx, raw in enumerate(snapshot):
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.debug("non-json stdout: %s", raw)
                    continue

                msg_type = msg.get("type", "")
                events.append(msg)

                if msg_type == "message_update":
                    evt = msg.get("assistantMessageEvent", {})
                    evt_type = evt.get("type", "")
                    if evt_type == "text_delta":
                        collected_text += evt.get("delta", "")

                elif msg_type == "agent_end":
                    usage = msg.get("usage", {})
                    self.cursor += idx + 1
                    return {
                        "output": collected_text,
                        "events": events,
                        "usage": usage,
                    }

                elif msg_type == "response":
                    if not msg.get("success", False):
                        cmd = msg.get("command", "")
                        err = msg.get("error", "")
                        log.warning("Pi response error: command=%s error=%s", cmd, err)

            self.cursor += len(snapshot)

        log.warning("drain_to_agent_end timed out after %.1fs", timeout)
        return {"output": collected_text, "events": events, "usage": usage}

    def wait_for_response(self, command_name: str, timeout: float = 10) -> dict[str, Any] | None:
        """Wait for a response message matching a specific command name."""
        start = time.time()
        while time.time() - start < timeout:
            with self.lock:
                current_len = len(self.lines)

            for i in range(self.cursor, current_len):
                with self.lock:
                    raw = self.lines[i]
                try:
                    msg = json.loads(raw)
                    if msg.get("type") == "response" and msg.get("command") == command_name:
                        self.cursor = i + 1
                        return msg
                except json.JSONDecodeError:
                    continue

            time.sleep(0.1)

        return None


# ---------------------------------------------------------------------------
# PiClient
# ---------------------------------------------------------------------------


class PiClient:
    """Sync Pi RPC client using subprocess.Popen + threaded stdout reader.

    Usage::

        with PiClient(model="azure-openai-responses/gpt-4.1-mini") as pi:
            result = pi.prompt("What is 2+2?")
            print(result["output"])
    """

    def __init__(
        self,
        model: str,
        cwd: str = ".",
        thinking: str = "high",
    ) -> None:
        self.model = model
        self.cwd = cwd
        self.thinking = thinking
        self._proc: subprocess.Popen[str] | None = None
        self._collector: _StdoutCollector | None = None
        self._stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Spawn Pi RPC subprocess if not already alive."""
        if self._proc is not None and self._proc.poll() is None:
            return

        cmd = [
            "pi",
            "--mode", "rpc",
            "--no-session",
            "--no-tools",
            "--model", self.model,
        ]
        log.info("Starting Pi RPC: %s", " ".join(cmd))

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.cwd,
        )
        self._collector = _StdoutCollector(self._proc)

        # Drain stderr in background to prevent pipe blocking
        self._stderr_lines = []

        def _stderr_reader() -> None:
            assert self._proc is not None and self._proc.stderr is not None
            for line in self._proc.stderr:
                self._stderr_lines.append(line.rstrip())

        self._stderr_thread = threading.Thread(target=_stderr_reader, daemon=True)
        self._stderr_thread.start()

        # Give Pi a moment to initialize
        time.sleep(1)

    def stop(self) -> None:
        """Kill the Pi subprocess."""
        if self._proc is None:
            return

        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except OSError:
            pass

        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()

        self._proc = None
        self._collector = None

    @property
    def is_alive(self) -> bool:
        """True if the Pi subprocess is running."""
        return self._proc is not None and self._proc.poll() is None

    # -- context manager -----------------------------------------------------

    def __enter__(self) -> PiClient:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- low-level I/O -------------------------------------------------------

    def send(self, cmd: dict[str, Any]) -> None:
        """Write a JSON command + newline to Pi's stdin and flush."""
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("Pi process not started — call start() first")
        line = json.dumps(cmd) + "\n"
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

    # -- high-level API ------------------------------------------------------

    def prompt(self, message: str, timeout: float = 60) -> dict[str, Any]:
        """Send a prompt and collect the full response.

        Returns ``{"output": str, "events": list[dict], "usage": dict}``.
        """
        if not self.is_alive:
            self.start()
        assert self._collector is not None

        self.send({"type": "prompt", "message": message})
        return self._collector.drain_to_agent_end(timeout=timeout)

    def multi_turn(self, messages: list[str], timeout: float = 60) -> list[dict[str, Any]]:
        """Send multiple prompts sequentially, waiting for agent_end between each.

        Returns a list of result dicts (one per message).
        """
        results: list[dict[str, Any]] = []
        for msg in messages:
            results.append(self.prompt(msg, timeout=timeout))
        return results

    def command(self, cmd: str, timeout: float = 10) -> dict[str, Any]:
        """Send a non-prompt command and wait for its response event.

        Returns the response dict or ``{"success": False, "error": "timeout"}``.
        """
        if not self.is_alive:
            self.start()
        assert self._collector is not None

        self.send({"type": cmd})
        resp = self._collector.wait_for_response(cmd, timeout=timeout)
        if resp is None:
            return {"success": False, "error": "timeout", "command": cmd}
        return resp
