"""Persistent Pi agent runtime.

Syke treats Pi as the canonical agent runtime. This client manages a long-lived
Pi RPC subprocess, prepares its process environment, and turns Pi's RPC event
stream into structured runtime results.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from syke.config import SYNC_THINKING_LEVEL
from syke.llm import pi_catalog as _pi_catalog
from syke.llm import pi_install as _pi_install
from syke.llm import pi_rpc as _pi_rpc
from syke.runtime.child_env import temp_paths_from_env
from syke.runtime.pi_settings import configure_pi_workspace

logger = logging.getLogger(__name__)

# A provider can fail one model call (a bad gateway backend, a rejected request)
# while the next identical call succeeds. Pi retries only errors it knows are
# transient, so a single failed call would otherwise end the whole operation.
PROVIDER_ERROR_RETRIES = 2
PROVIDER_ERROR_RETRY_PROMPT = (
    "The last model request failed at the provider before you could respond. "
    "Continue from where you left off."
)
MIN_RETRY_SECONDS = 5.0

# Public API of the Pi runtime package. Callers import installation, catalog,
# and RPC names from pi_client; the implementations live in the sibling
# modules.
PI_BIN = _pi_install.PI_BIN
PI_NODE_BIN = _pi_install.PI_NODE_BIN
PI_PACKAGE = _pi_install.PI_PACKAGE
PI_PACKAGE_VERSION = _pi_install.PI_PACKAGE_VERSION
ensure_node_binary = _pi_install.ensure_node_binary
ensure_pi_binary = _pi_install.ensure_pi_binary
get_pi_version = _pi_install.get_pi_version
resolve_pi_binary = _pi_install.resolve_pi_binary

PiLaunchBinding = _pi_catalog.PiLaunchBinding
PiProviderCatalogEntry = _pi_catalog.PiProviderCatalogEntry
get_pi_provider_catalog = _pi_catalog.get_pi_provider_catalog
probe_pi_provider_connection = _pi_catalog.probe_pi_provider_connection
resolve_pi_launch_binding = _pi_catalog.resolve_pi_launch_binding
run_pi_oauth_login = _pi_catalog.run_pi_oauth_login

PiCycleResult = _pi_rpc.PiCycleResult
RpcEventStream = _pi_rpc.RpcEventStream


def resolve_pi_model(model_override: str | None = None) -> str:
    """Resolve the Pi model through this module's binding seam.

    Kept here rather than aliased to pi_catalog so tests that patch
    ``pi_client.resolve_pi_launch_binding`` also steer model resolution.
    """
    return resolve_pi_launch_binding(model_override).model


_RPC_STOP_STDIN_GRACE_SECONDS = 0.2
_RPC_STOP_TERM_GRACE_SECONDS = 1.0
_PI_BASH_SPILL_NAME = re.compile(r"pi-bash-[0-9a-f]{16}\.log")


def pi_bash_spill_path_from_event(event: dict[str, Any], temp_dir: Path) -> Path | None:
    """Return a bash spill path only when this Pi event owns it."""
    if event.get("toolName") != "bash":
        return None
    event_type = event.get("type")
    if event_type == "tool_execution_update":
        result_key = "partialResult"
    elif event_type == "tool_execution_end":
        result_key = "result"
    else:
        return None
    result = event.get(result_key)
    if not isinstance(result, dict):
        return None
    details = result.get("details")
    raw_path = details.get("fullOutputPath") if isinstance(details, dict) else None
    if not isinstance(raw_path, str) or not raw_path:
        return None

    candidate = Path(raw_path).expanduser().resolve()
    owned_temp_dir = temp_dir.expanduser().resolve()
    if candidate.parent != owned_temp_dir:
        return None
    if _PI_BASH_SPILL_NAME.fullmatch(candidate.name) is None:
        return None
    return candidate


def remove_pi_bash_spills(paths: set[Path]) -> None:
    """Remove spill files reported by one completed Syke operation."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove Pi bash spill %s: %s", path, exc)


def _build_rpc_launch_command(
    *,
    provider: str | None,
    model: str,
    thinking_level: str,
    session_dir: Path,
    self_learn_skill_path: Path,
    tool_sandbox_profile: Path | None = None,
) -> tuple[list[str], dict[str, str]]:
    syke_self_path = Path(__file__).parent.parent / "runtime" / "syke_self.md"
    cmd = [resolve_pi_binary(), "--mode", "rpc"]
    if provider:
        cmd.extend(["--provider", provider])
    cmd.extend(
        [
            "--model",
            model,
            "--thinking",
            thinking_level,
            "--session-dir",
            str(session_dir),
            "--system-prompt",
            str(syke_self_path),
            "--no-context-files",
            "--no-skills",
            "--skill",
            str(self_learn_skill_path),
        ]
    )
    extra_env: dict[str, str] = {}
    if tool_sandbox_profile is not None:
        extension = _pi_install._install_pi_tool_extension()
        cmd.extend(
            [
                "--no-builtin-tools",
                "--no-extensions",
                "--extension",
                str(extension),
            ]
        )
        extra_env["SYKE_TOOL_SANDBOX_PROFILE"] = str(tool_sandbox_profile)
    return cmd, extra_env


class PiRuntime:
    """Persistent Pi agent runtime."""

    def __init__(
        self,
        workspace_dir: str | Path,
        session_dir: str | Path,
        model: str | None = None,
    ):
        self.workspace_dir = Path(workspace_dir).expanduser().resolve()
        self.session_dir = Path(session_dir).expanduser().resolve()
        if self.session_dir == self.workspace_dir or self.session_dir.is_relative_to(
            self.workspace_dir
        ):
            raise ValueError("Pi session history must be outside the controller workspace")
        self._model_override = model
        self._binding_error: str | None = None
        try:
            binding = resolve_pi_launch_binding(model)
        except RuntimeError as exc:
            self._binding_error = str(exc)
            provider = _pi_catalog._get_active_provider_spec()
            self.provider = _pi_catalog._pi_provider_name(provider)
            self.model = _pi_catalog._raw_pi_model_request(model)[0]
        else:
            self.provider = binding.provider
            self.model = binding.model
        self._process: subprocess.Popen[str] | None = None
        self._stream: RpcEventStream | None = None
        self._stderr_drain: _pi_rpc._StderrDrain | None = None
        self._sandbox_profile_path: Path | None = None
        self._started_at: float | None = None
        self._last_start_duration_ms: int | None = None
        self._start_count = 0
        self._request_id = 0
        self._request_lock = threading.Lock()
        self._prompt_lock = threading.Lock()

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        """Start the Pi process in RPC mode."""
        if self.is_alive:
            logger.info("Pi runtime already alive")
            return
        started = time.monotonic()

        binding = resolve_pi_launch_binding(self._model_override)
        self._binding_error = None
        self.provider = binding.provider
        self.model = binding.model
        _pi_catalog._prepare_host_oauth_for_runtime(self.provider)
        runtime_env = configure_pi_workspace(
            self.workspace_dir,
            session_dir=self.session_dir,
        )
        self_learn_skill_path = (
            Path(runtime_env["PI_CODING_AGENT_DIR"]) / "skills" / "self-learn" / "SKILL.md"
        )
        control_root = self.session_dir.parent
        runtime_root = control_root / "runtime"

        from syke.runtime.sandbox import sandbox_enabled, write_sandbox_profile

        sandbox_profile = None
        if sandbox_enabled():
            preview_env = _pi_catalog._build_pi_process_env(runtime_env, provider=self.provider)
            sandbox_profile = write_sandbox_profile(
                self.workspace_dir,
                control_root=control_root,
                runtime_root=runtime_root,
                extra_temp_dirs=temp_paths_from_env(preview_env),
            )
            if sandbox_profile:
                self._sandbox_profile_path = sandbox_profile

        try:
            cmd, extra_env = _build_rpc_launch_command(
                provider=self.provider,
                model=self.model,
                thinking_level=SYNC_THINKING_LEVEL,
                session_dir=self.session_dir,
                self_learn_skill_path=self_learn_skill_path,
                tool_sandbox_profile=sandbox_profile,
            )
        except Exception:
            self._cleanup_sandbox_profile()
            raise

        logger.info(
            "Starting runtime: %s/%s",
            self.provider or "auto",
            self.model,
        )

        env = _pi_catalog._build_pi_process_env(
            {**runtime_env, **extra_env}, provider=self.provider
        )

        if sandbox_profile:
            logger.info("Pi host is unsandboxed; model tools use the OS sandbox")

        logger.debug("Pi runtime command: %s", " ".join(cmd))

        try:
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
            self._stderr_drain = _pi_rpc._StderrDrain(self._process.stderr)
            self._stream.start()
            self._stderr_drain.start()
            self._started_at = time.time()

            time.sleep(1.0)
            if not self.is_alive:
                stderr = self._stderr_drain.get_output() if self._stderr_drain else ""
                raise RuntimeError(f"Pi failed to start: {stderr[:500]}")
        except Exception:
            self._cleanup_sandbox_profile()
            raise

        self._last_start_duration_ms = int((time.monotonic() - started) * 1000)
        self._start_count += 1
        logger.debug("Pi runtime started (pid=%s)", self._process.pid)

    def stop(self) -> None:
        """Stop the Pi process gracefully."""
        with self._prompt_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if self._process is None:
            return

        process = self._process
        pid = process.pid
        logger.info("Stopping Pi runtime (pid=%s)", pid)

        stdin = getattr(process, "stdin", None)
        if stdin is not None:
            try:
                stdin.close()
            except OSError:
                pass

        if process.poll() is None:
            try:
                process.wait(timeout=_RPC_STOP_STDIN_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass

        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=_RPC_STOP_TERM_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                logger.warning("Pi did not quit gracefully, killing")
                process.kill()
                process.wait()
            except OSError:
                pass

        self._process = None
        self._stream = None
        self._stderr_drain = None
        self._cleanup_sandbox_profile()
        logger.debug("Pi runtime stopped (was pid=%s)", pid)

    def _cleanup_sandbox_profile(self) -> None:
        sandbox_profile = self._sandbox_profile_path
        self._sandbox_profile_path = None
        if sandbox_profile is None:
            return
        try:
            sandbox_profile.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove Pi sandbox profile %s: %s", sandbox_profile, exc)

    def new_session(
        self,
        *,
        parent_session: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Start a fresh Pi session while reusing the warm runtime process."""
        command: dict[str, Any] = {"type": "new_session"}
        if parent_session:
            command["parentSession"] = parent_session
        response = self._send_request(command, timeout=timeout)
        return response if isinstance(response, dict) else {}

    def get_session_stats(self, *, timeout: float = 10.0) -> dict[str, Any]:
        """Fetch Pi's current per-session stats."""
        response = self._send_request({"type": "get_session_stats"}, timeout=timeout)
        return response if isinstance(response, dict) else {}

    def get_state(self, *, timeout: float = 10.0) -> dict[str, Any]:
        """Fetch Pi's current native session identity and runtime state."""
        response = self._send_request({"type": "get_state"}, timeout=timeout)
        return response if isinstance(response, dict) else {}

    def set_session_name(self, name: str, *, timeout: float = 10.0) -> None:
        """Write a correlation name into Pi's native session."""
        self._send_request({"type": "set_session_name", "name": name}, timeout=timeout)

    def prompt(
        self,
        text: str,
        *,
        timeout: float | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        new_session: bool = False,
        session_name: str | None = None,
    ) -> PiCycleResult:
        """Send a prompt to Pi and wait for completion."""
        with self._prompt_lock:
            if not self.is_alive or self._stream is None:
                raise RuntimeError("Pi runtime is not running")

            session_state: dict[str, Any] = {}
            if new_session:
                self._stream.set_callback(None)
                self._stream.reset()
                self.new_session(timeout=min(timeout or 30.0, 30.0))
                if session_name:
                    self.set_session_name(session_name, timeout=min(timeout or 10.0, 10.0))
                session_state = self.get_state(timeout=min(timeout or 10.0, 10.0))

            self._stream.set_callback(on_event)
            self._stream.reset()

            self._send({"type": "prompt", "message": text})
            start = time.time()
            completed = self._stream.wait(timeout=timeout)
            retries = 0
            while completed and not self._stream.error and retries < PROVIDER_ERROR_RETRIES:
                failed_call = self._stream.get_assistant_error()
                remaining = None if timeout is None else timeout - (time.time() - start)
                if not failed_call or (remaining is not None and remaining < MIN_RETRY_SECONDS):
                    break
                retries += 1
                logger.warning(
                    "Pi model call failed at the provider (%s); retry %d/%d",
                    failed_call[:200],
                    retries,
                    PROVIDER_ERROR_RETRIES,
                )
                self._stream.rearm()
                self._send({"type": "prompt", "message": PROVIDER_ERROR_RETRY_PROMPT})
                completed = self._stream.wait(timeout=remaining)
            duration_ms = int((time.time() - start) * 1000)

            events = self._stream.events
            usage = self._stream.get_usage()
            message_metadata = self._stream.get_message_metadata()
            assistant_error = self._stream.get_assistant_error()
            provider = message_metadata.get("provider")
            response_model = message_metadata.get("model")
            response_id = message_metadata.get("response_id")
            stop_reason = message_metadata.get("stop_reason")
            if not completed:
                timeout_error = (
                    self._stream.error
                    or assistant_error
                    or f"Pi did not complete within {timeout}s"
                )
                result = PiCycleResult(
                    status="timeout",
                    output=self._stream.get_output(),
                    thinking=self._stream.get_thinking_chunks(),
                    tool_calls=_pi_rpc._dedupe_tool_invocations(
                        self._stream.get_tool_invocations()
                    ),
                    events=events,
                    num_turns=0,
                    duration_ms=duration_ms,
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    cache_read_tokens=usage["cache_read_tokens"],
                    cache_write_tokens=usage["cache_write_tokens"],
                    cost_usd=usage["cost_usd"],
                    provider=provider,
                    response_model=response_model,
                    response_id=response_id,
                    stop_reason=stop_reason,
                    session_id=session_state.get("sessionId"),
                    session_file=session_state.get("sessionFile"),
                    session_name=session_state.get("sessionName"),
                    error=timeout_error,
                )
                self._stream.set_callback(None)
                self._stop_locked()
                return result

            session_stats = self.get_session_stats(timeout=min(timeout or 10.0, 10.0))
            tool_calls = _pi_rpc._dedupe_tool_invocations(self._stream.get_tool_invocations())
            assistant_messages = session_stats.get("assistantMessages")
            num_turns = assistant_messages if isinstance(assistant_messages, int) else 0
            result = PiCycleResult(
                status="completed"
                if completed and not self._stream.error and not assistant_error
                else "error",
                output=self._stream.get_output(),
                thinking=self._stream.get_thinking_chunks(),
                tool_calls=tool_calls,
                events=events,
                num_turns=num_turns,
                duration_ms=duration_ms,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cache_read_tokens=usage["cache_read_tokens"],
                cache_write_tokens=usage["cache_write_tokens"],
                cost_usd=usage["cost_usd"],
                provider=provider,
                response_model=response_model,
                response_id=response_id,
                stop_reason=stop_reason,
                session_id=session_state.get("sessionId"),
                session_file=session_state.get("sessionFile"),
                session_name=session_state.get("sessionName"),
                error=self._stream.error or assistant_error,
            )
            self._stream.set_callback(None)
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

    def _send_request(self, command: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
        if not self.is_alive or self._stream is None:
            raise RuntimeError("Pi runtime is not running")

        with self._request_lock:
            self._request_id += 1
            request_id = f"req_{self._request_id}"

        self._send({**command, "id": request_id})

        # Liveness waits use wall time: monotonic clocks freeze during
        # system sleep, which stretches deadlines by the sleep duration.
        deadline = time.time() + max(timeout, 0.1)
        scanned = 0
        while time.time() < deadline:
            events = self._stream.events
            for event in events[scanned:]:
                if event.get("type") != "response" or event.get("id") != request_id:
                    continue
                if event.get("success") is False:
                    error = event.get("error") or f"Pi request failed: {command.get('type')}"
                    raise RuntimeError(str(error))
                data = event.get("data")
                return data if isinstance(data, dict) else {}
            scanned = len(events)
            if not self.is_alive:
                raise RuntimeError("Pi runtime exited while waiting for RPC response")
            time.sleep(0.01)

        raise TimeoutError(f"Timed out waiting for Pi RPC response to {command.get('type')}")

    @property
    def uptime_seconds(self) -> float | None:
        if self._started_at and self.is_alive:
            return time.time() - self._started_at
        return None

    def status(self) -> dict[str, Any]:
        session_count = 0
        if self.session_dir.exists():
            session_count = len(list(self.session_dir.glob("*.jsonl")))
        return {
            "alive": self.is_alive,
            "provider": self.provider,
            "model": self.model,
            "binding_error": self._binding_error,
            "workspace": str(self.workspace_dir),
            "session_dir": str(self.session_dir),
            "pid": self._process.pid if self._process else None,
            "uptime_s": self.uptime_seconds,
            "last_start_ms": self._last_start_duration_ms,
            "start_count": self._start_count,
            "session_count": session_count,
        }
