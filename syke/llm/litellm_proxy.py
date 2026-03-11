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

        def _patched_content_block(self, choices):  # type: ignore[override]
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

        _patched_content_block._syke_patched = True  # type: ignore[attr-defined]
        setattr(LiteLLMAnthropicMessagesAdapter, _attr, _patched_content_block)
        log.info("Applied LiteLLM reasoning_content streaming patch (v%s)", version)

    except Exception:
        log.warning(
            "Failed to apply LiteLLM reasoning_content patch",
            exc_info=True,
        )


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
        health_url = f"{self.base_url}/health"
        req = request.Request(health_url, headers={"Authorization": "Bearer sk-syke-local-proxy"})
        for _ in range(50):
            try:
                with cast(HTTPResponse, request.urlopen(req, timeout=1)) as response:  # noqa: S310
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
