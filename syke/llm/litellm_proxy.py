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
        for _ in range(50):
            try:
                with cast(HTTPResponse, request.urlopen(health_url, timeout=1)) as response:  # noqa: S310
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

        import uvicorn
        from litellm.proxy.proxy_server import app

        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
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
