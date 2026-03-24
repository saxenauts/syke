from __future__ import annotations

import logging
import os
import socket
import threading
import time
from http.client import HTTPResponse
from pathlib import Path
from typing import Protocol, cast
from urllib import error, request

log = logging.getLogger(__name__)


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

        # Patch 2: handle adaptive thinking type
        # Claude CLI sends thinking={'type': 'adaptive'} but the adapter
        # only handles 'enabled'. Without this, thinking is silently dropped.
        from litellm.llms.anthropic.experimental_pass_through.responses_adapters.transformation import (
            LiteLLMAnthropicToResponsesAPIAdapter as _Adapter,
        )

        _orig_translate = _Adapter.translate_thinking_to_reasoning

        @staticmethod
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

        log.info("Enabled Azure Responses API with thinking traces")
    except Exception:
        log.warning("Failed to enable Azure Responses API routing", exc_info=True)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return cast(tuple[str, int], s.getsockname())[1]


class LiteLLMProxy:
    def __init__(self, config_path: str | Path, port: int | None = None) -> None:
        self.config_path: str = str(Path(config_path))
        self.port: int = port or 0
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
        if self.port == 0:
            self.port = _find_free_port()

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
    global _active_proxy
    if _active_proxy and _active_proxy.is_running:
        return _active_proxy.port
    _active_proxy = LiteLLMProxy(config_path, port=port)
    return _active_proxy.start()


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
