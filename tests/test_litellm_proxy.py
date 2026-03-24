from __future__ import annotations

import os
import time
from collections.abc import Iterator
from urllib import request
from urllib.error import URLError

import pytest
import uvicorn
from litellm.proxy import proxy_server

import syke.llm.litellm_proxy as litellm_proxy


class _FakeConfig:
    host: str | None = None
    port: int | None = None

    def __init__(self, app: object, host: str, port: int, log_level: str) -> None:
        self.app: object = app
        self.host = host
        self.port = port
        self.log_level: str = log_level
        _FakeConfig.host = host
        _FakeConfig.port = port


class _FakeServer:
    def __init__(self, config: _FakeConfig) -> None:
        self.config: _FakeConfig = config
        self.should_exit: bool = False

    def run(self) -> None:
        while not self.should_exit:
            time.sleep(0.01)


class _Response:
    status: int = 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def getcode(self) -> int:
        return 200


def _install_fake_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uvicorn, "Config", _FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", _FakeServer)
    monkeypatch.setattr(proxy_server, "app", object())


@pytest.fixture
def reset_proxy() -> Iterator[None]:
    litellm_proxy.stop_litellm_proxy()
    yield
    litellm_proxy.stop_litellm_proxy()


def test_start_stop_singleton_and_health(
    monkeypatch: pytest.MonkeyPatch, reset_proxy: None
) -> None:
    _ = reset_proxy
    _install_fake_runtime(monkeypatch)

    # Uses deterministic port _PROXY_PORT = 43123

    calls: list[request.Request] = []

    def fake_urlopen(req: request.Request, timeout: float) -> _Response:
        _ = timeout
        calls.append(req)
        return _Response()

    monkeypatch.setattr("syke.llm.litellm_proxy.request.urlopen", fake_urlopen)

    assert litellm_proxy.is_litellm_proxy_running() is False

    port = litellm_proxy.start_litellm_proxy("/tmp/litellm.yaml")
    assert isinstance(port, int)
    assert port == 43123
    assert litellm_proxy.is_litellm_proxy_running() is True
    assert calls[0].full_url == "http://127.0.0.1:43123/health/liveness"
    assert os.environ["CONFIG_FILE_PATH"] == "/tmp/litellm.yaml"

    same_port = litellm_proxy.start_litellm_proxy("/tmp/other.yaml")
    assert same_port == 43123
    assert _FakeConfig.host == "127.0.0.1"
    assert _FakeConfig.port == 43123

    litellm_proxy.stop_litellm_proxy()
    assert litellm_proxy.is_litellm_proxy_running() is False


def test_start_raises_if_health_check_never_passes(
    monkeypatch: pytest.MonkeyPatch, reset_proxy: None
) -> None:
    _ = reset_proxy
    _install_fake_runtime(monkeypatch)

    # Uses deterministic port _PROXY_PORT = 43123

    def sleep_noop(seconds: float) -> None:
        _ = seconds

    monkeypatch.setattr("syke.llm.litellm_proxy.time.sleep", sleep_noop)

    def failing_urlopen(req: request.Request, timeout: float) -> _Response:
        _ = (req, timeout)
        raise URLError("not ready")

    monkeypatch.setattr("syke.llm.litellm_proxy.request.urlopen", failing_urlopen)

    with pytest.raises(RuntimeError, match="failed health check"):
        _ = litellm_proxy.start_litellm_proxy("/tmp/litellm.yaml")

    assert litellm_proxy.is_litellm_proxy_running() is False


def test_stop_is_safe_when_not_started(reset_proxy: None) -> None:
    _ = reset_proxy
    litellm_proxy.stop_litellm_proxy()
    assert litellm_proxy.is_litellm_proxy_running() is False


def test_start_proxy_returns_existing_port_if_already_running(
    monkeypatch: pytest.MonkeyPatch, reset_proxy: None
) -> None:
    """If proxy is already running, start_litellm_proxy returns existing port without restarting."""
    _ = reset_proxy
    _install_fake_runtime(monkeypatch)

    # Uses deterministic port _PROXY_PORT = 43123

    calls: list[str] = []

    def fake_urlopen(req: request.Request, timeout: float) -> _Response:
        _ = timeout
        calls.append(str(req.full_url) if hasattr(req, "full_url") else str(req))
        return _Response()

    monkeypatch.setattr("syke.llm.litellm_proxy.request.urlopen", fake_urlopen)
    # Mock filesystem metadata to simulate proxy already running cross-process
    monkeypatch.setattr(
        litellm_proxy,
        "_read_proxy_metadata",
        lambda: {
            "pid": 12345,
            "port": 43123,
            "config_hash": "testhash12345678",
            "config_path": "/tmp/litellm.yaml",
        },
    )

    # Start proxy - should reuse existing from metadata
    port1 = litellm_proxy.start_litellm_proxy("/tmp/litellm.yaml")
    assert port1 == 43123
    # Note: is_litellm_proxy_running() returns False because we mocked metadata
    # but didn't actually start a proxy via LiteLLMProxy.start()

    # Start again — should return same port without new health checks
    health_check_count_first = len(calls)
    port2 = litellm_proxy.start_litellm_proxy("/tmp/other.yaml")
    assert port2 == 43123
    assert port2 == port1
    # No additional health checks (reused existing)
    assert len(calls) == health_check_count_first
