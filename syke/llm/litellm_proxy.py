from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from http.client import HTTPResponse
from pathlib import Path
from typing import Any, Protocol, cast
from urllib import error, request

log = logging.getLogger(__name__)

# Deterministic port for cross-process coordination
_PROXY_PORT = 43123

# Filesystem paths for cross-process coordination
_SYKE_DIR = Path.home() / ".syke"
_PROXY_LOCK_FILE = _SYKE_DIR / "litellm_proxy.lock"
_PROXY_METADATA_FILE = _SYKE_DIR / "litellm_proxy.json"
_PROXY_CONFIG_DIR = _SYKE_DIR / "litellm"


class _PatchedContentBlockFn(Protocol):
    _syke_patched: bool

    def __call__(self, self_obj: object, choices: object) -> object: ...


# ---------------------------------------------------------------------------
# Monkey-patch: fix LiteLLM streaming adapter for reasoning_content
# ---------------------------------------------------------------------------
# LiteLLM v1.82.0 has a bug in the Anthropic pass-through streaming adapter:
#
#   _translate_streaming_openai_chunk_to_anthropic_content_block (block TYPE)
#     → Does NOT check `reasoning_content` → returns "text" (wrong)
#
#   _translate_streaming_openai_chunk_to_anthropic (delta TYPE)
#     → DOES check `reasoning_content` → returns "thinking_delta" (correct)
#
# Result: Claude CLI receives thinking_delta for a text block → crash:
#   "Content block is not a thinking block"
#
# This patch adds the missing `reasoning_content` check to the block-type
# function, exactly mirroring the delta function's existing logic.
#
# Tracks:
#   - https://github.com/BerriAI/litellm/pull/23160
#   - https://github.com/BerriAI/litellm/issues/22997
#
# Self-removes when LiteLLM fixes this upstream (version gate below).
# ---------------------------------------------------------------------------

_LITELLM_PATCH_MAX_VERSION = "1.90.0"


def _apply_litellm_reasoning_content_patch() -> None:
    try:
        import litellm

        version = getattr(litellm, "version", None) or getattr(litellm, "__version__", "0.0.0")
        parts = [int(x) for x in str(version).split(".")[:3]]
        max_parts = [int(x) for x in _LITELLM_PATCH_MAX_VERSION.split(".")[:3]]
        if parts >= max_parts:
            log.debug(
                "LiteLLM %s >= %s — skipping reasoning_content patch (likely fixed upstream)",
                version,
                _LITELLM_PATCH_MAX_VERSION,
            )
            return

        from litellm.llms.anthropic.experimental_pass_through.adapters.transformation import (
            LiteLLMAnthropicMessagesAdapter,
        )
        from litellm.types.llms.openai import ChatCompletionThinkingBlock
        from litellm.types.utils import StreamingChoices

        _attr = "_translate_streaming_openai_chunk_to_anthropic_content_block"
        _original = getattr(LiteLLMAnthropicMessagesAdapter, _attr)

        if getattr(_original, "_syke_patched", False):
            return

        def _patched_content_block(self, choices):
            for choice in choices:
                if (
                    isinstance(choice, StreamingChoices)
                    and hasattr(choice.delta, "reasoning_content")
                    and choice.delta.reasoning_content is not None
                ):
                    return "thinking", ChatCompletionThinkingBlock(
                        type="thinking", thinking="", signature=""
                    )
            return _original(self, choices)

        patched_fn = cast(_PatchedContentBlockFn, _patched_content_block)
        patched_fn._syke_patched = True
        setattr(LiteLLMAnthropicMessagesAdapter, _attr, patched_fn)
        log.info("Applied LiteLLM reasoning_content streaming patch (v%s)", version)

    except Exception:
        log.warning(
            "Failed to apply LiteLLM reasoning_content patch",
            exc_info=True,
        )


def _enable_azure_responses_api() -> None:
    """Enable reasoning traces for Azure through the Responses API.

    Three patches:
    1. Route Azure through Responses API (not Chat Completions).
    2. Handle 'adaptive' thinking type (Claude CLI sends this, not 'enabled').
    3. Add 'signature' field to thinking content_block_start events.
    """
    try:
        import litellm.llms.anthropic.experimental_pass_through.messages.handler as _msg_handler

        # Patch 1: route Azure through Responses API
        current = getattr(_msg_handler, "_RESPONSES_API_PROVIDERS", frozenset())
        if "azure" not in current:
            _msg_handler._RESPONSES_API_PROVIDERS = frozenset(current | {"azure", "azure_ai"})

        # Patch 2: normalize "adaptive" thinking type to "enabled"
        # Some callers send thinking={'type': 'adaptive'}. The adapter handles
        # "enabled" natively with budget_tokens -> effort mapping. We normalize
        # "adaptive" -> "enabled" to use that mapping instead of hardcoding.
        # Claude CLI sends thinking={'type': 'adaptive'} but the adapter
        # only handles 'enabled'. Without this, thinking is silently dropped.
        from litellm.llms.anthropic.experimental_pass_through.responses_adapters.transformation import (
            LiteLLMAnthropicToResponsesAPIAdapter as _Adapter,
        )

        _orig_translate = _Adapter.translate_thinking_to_reasoning

        @staticmethod
        def _patched_translate(thinking):
            # Normalize "adaptive" to "enabled" so LiteLLM's existing
            # budget_tokens -> effort mapping is used. The original patch
            # hardcoded "medium" which ignored budget_tokens.
            if isinstance(thinking, dict) and thinking.get("type") == "adaptive":
                thinking = {**thinking, "type": "enabled"}

            reasoning = _orig_translate(thinking)

            # Azure doesn't document "minimal" effort; clamp to "low" for safety
            if reasoning and reasoning.get("effort") == "minimal":
                reasoning["effort"] = "low"

            return reasoning
        def _patched_translate(thinking):
            if isinstance(thinking, dict) and thinking.get("type") == "adaptive":
                return {"effort": "medium", "summary": "detailed"}
            return _orig_translate(thinking)

        if not getattr(_Adapter.translate_thinking_to_reasoning, "_syke_patched", False):
            _patched_translate._syke_patched = True
            _Adapter.translate_thinking_to_reasoning = _patched_translate

        # Patch 3: add signature field to thinking content_block_start
        # The CLI requires 'signature' on thinking blocks. Without it,
        # the block is created but thinking text isn't accumulated.
        # We directly patch the _next_block_index's caller by wrapping
        # _process_event to fix the chunk after it's queued.
        from litellm.llms.anthropic.experimental_pass_through.responses_adapters.streaming_iterator import (
            AnthropicResponsesStreamWrapper as _Wrapper,
        )

        _orig_process = _Wrapper._process_event

        def _patched_process(self, event):
            queue_len_before = len(self._chunk_queue)
            _orig_process(self, event)
            # Check new chunks for thinking blocks missing signature
            for chunk in self._chunk_queue[queue_len_before:]:
                cb = chunk.get("content_block")
                if (
                    chunk.get("type") == "content_block_start"
                    and isinstance(cb, dict)
                    and cb.get("type") == "thinking"
                    and "signature" not in cb
                ):
                    cb["signature"] = ""

        if not getattr(_orig_process, "_syke_patched", False):
            _patched_process._syke_patched = True
            _Wrapper._process_event = _patched_process

        # Patch 4: add 'thinking' to Azure GPT-5 supported params
        # Without this, litellm's param validator rejects 'thinking' before
        # the Responses API adapter can translate it to 'reasoning'.
        try:
            from litellm.llms.azure.chat.gpt_5_transformation import AzureOpenAIGPT5Config

            _orig_get_params = AzureOpenAIGPT5Config.get_supported_openai_params

            def _patched_get_params(self, model: str):
                params = _orig_get_params(self, model)
                if "thinking" not in params:
                    params.append("thinking")
                return params

            if not getattr(_orig_get_params, "_syke_patched", False):
                _patched_get_params._syke_patched = True
                AzureOpenAIGPT5Config.get_supported_openai_params = _patched_get_params
        except Exception:
            pass  # Non-critical — thinking will be silently dropped

        log.info("Enabled Azure Responses API with thinking traces")
    except Exception:
        log.warning("Failed to enable Azure Responses API routing", exc_info=True)


def _get_config_hash(config_path: str) -> str:
    """Compute SHA256 hash of config file contents."""
    try:
        content = Path(config_path).read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]
    except Exception:
        return "unknown"


def _read_proxy_metadata() -> dict[str, Any] | None:
    """Read proxy metadata from filesystem."""
    try:
        if _PROXY_METADATA_FILE.exists():
            content = _PROXY_METADATA_FILE.read_text()
            return json.loads(content)
    except Exception:
        pass
    return None


def _write_proxy_metadata(pid: int, port: int, config_hash: str, config_path: str) -> None:
    """Write proxy metadata to filesystem."""
    try:
        _SYKE_DIR.mkdir(parents=True, exist_ok=True)
        metadata = {
            "pid": pid,
            "port": port,
            "config_hash": config_hash,
            "config_path": config_path,
            "started_at": time.time(),
        }
        _PROXY_METADATA_FILE.write_text(json.dumps(metadata))
    except Exception as e:
        log.warning("Failed to write proxy metadata: %s", e)


def _clear_proxy_metadata() -> None:
    """Clear proxy metadata from filesystem."""
    try:
        if _PROXY_METADATA_FILE.exists():
            _PROXY_METADATA_FILE.unlink()
    except Exception:
        pass


def _is_proxy_responding(port: int) -> bool:
    """Check if proxy is actually responding on the given port."""
    try:
        health_url = f"http://127.0.0.1:{port}/health/liveness"
        req = request.Request(health_url, headers={"Authorization": "Bearer sk-syke-local-proxy"})
        with cast(HTTPResponse, request.urlopen(req, timeout=1)) as response:  # noqa: S310
            return response.getcode() == 200
    except Exception:
        return False


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with given PID exists."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


@contextmanager
def _acquire_proxy_lock():
    """Context manager to acquire filesystem lock for proxy coordination."""
    _SYKE_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(_PROXY_LOCK_FILE), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


class LiteLLMProxy:
    def __init__(self, config_path: str | Path, port: int | None = None) -> None:
        self.config_path: str = str(Path(config_path))
        self.port: int = port or _PROXY_PORT  # Use deterministic port
        self._server: _UvicornServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _wait_for_health(self) -> bool:
        # Use /health/liveness — instant check that server is up.
        # /health calls the actual model which can timeout on cold start.
        health_url = f"{self.base_url}/health/liveness"
        req = request.Request(health_url, headers={"Authorization": "Bearer sk-syke-local-proxy"})
        for _ in range(100):
            try:
                with cast(HTTPResponse, request.urlopen(req, timeout=2)) as response:  # noqa: S310
                    if response.getcode() == 200:
                        return True
            except (error.URLError, OSError):
                pass
            time.sleep(0.2)
        return False

    def start(self) -> int:
        os.environ["CONFIG_FILE_PATH"] = self.config_path
        os.environ["LITELLM_LOG"] = "ERROR"

        import litellm
        import uvicorn
        from litellm.proxy.proxy_server import app

        _apply_litellm_reasoning_content_patch()
        _enable_azure_responses_api()

        litellm.suppress_debug_info = True
        for name in logging.Logger.manager.loggerDict:
            if name.lower().startswith(("litellm", "uvicorn")):
                logging.getLogger(name).setLevel(logging.CRITICAL + 10)

        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="critical")
        self._server = cast(_UvicornServer, uvicorn.Server(config))
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

        if not self._wait_for_health():
            self.stop()
            raise RuntimeError("LiteLLM proxy failed health check")

        log.info("LiteLLM proxy started on port %d", self.port)
        return self.port

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._server = None
        log.info("LiteLLM proxy stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


_active_proxy: LiteLLMProxy | None = None


def start_litellm_proxy(config_path: str | Path, port: int | None = None) -> int:
    """Start or reuse LiteLLM proxy with cross-process coordination.

    Uses filesystem lock (fcntl.flock) to ensure only one proxy runs across
    all Syke processes. Checks existing proxy health before starting new one.
    """
    global _active_proxy

    # Always apply in-process patches — these modify litellm's Python
    # objects for correct thinking/reasoning stream parsing. Must run
    # in every process, even when reusing an external proxy.
    _apply_litellm_reasoning_content_patch()
    _enable_azure_responses_api()

    # Fast path: check in-process singleton first
    if _active_proxy and _active_proxy.is_running:
        return _active_proxy.port

    config_path_str = str(Path(config_path))
    config_hash = _get_config_hash(config_path_str)

    # Cross-process coordination via filesystem lock
    with _acquire_proxy_lock():
        # Check if another process has a running proxy
        metadata = _read_proxy_metadata()
        if metadata:
            existing_pid = metadata.get("pid")
            existing_port = metadata.get("port", _PROXY_PORT)
            existing_hash = metadata.get("config_hash")

            # Check if the proxy is actually responding
            if _is_proxy_responding(existing_port):
                if existing_hash == config_hash:
                    log.debug("Reusing existing LiteLLM proxy on port %d", existing_port)
                    _active_proxy = LiteLLMProxy(config_path_str, port=existing_port)
                    return existing_port
                else:
                    log.warning(
                        "Config mismatch: existing proxy has different config. "
                        "Starting new proxy with current config."
                    )
                    # Fall through to start new proxy
            elif existing_pid and _is_pid_alive(existing_pid):
                log.warning(
                    "Existing proxy (PID %d) not responding but process alive", existing_pid
                )
                # Process alive but proxy not responding - might be starting up
                # Wait a bit and check again
                time.sleep(0.5)
                if _is_proxy_responding(existing_port):
                    _active_proxy = LiteLLMProxy(config_path_str, port=existing_port)
                    return existing_port
            else:
                log.info("Cleaning up stale proxy metadata (PID %d dead)", existing_pid)
                _clear_proxy_metadata()

        # Start new proxy
        use_port = port or _PROXY_PORT
        _active_proxy = LiteLLMProxy(config_path_str, port=use_port)
        actual_port = _active_proxy.start()

        # Write metadata for other processes to discover
        _write_proxy_metadata(
            pid=os.getpid(),
            port=actual_port,
            config_hash=config_hash,
            config_path=config_path_str,
        )
        return actual_port


def stop_litellm_proxy() -> None:
    global _active_proxy
    if _active_proxy:
        _active_proxy.stop()
        _active_proxy = None


def is_litellm_proxy_running() -> bool:
    return _active_proxy is not None and _active_proxy.is_running


class _UvicornServer(Protocol):
    should_exit: bool

    def run(self) -> None: ...
