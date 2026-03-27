"""Tests for removed Codex proxy surface."""

from __future__ import annotations

import pytest

from syke.llm.codex_proxy import (
    _read_codex_model,
    get_codex_proxy_port,
    start_codex_proxy,
    stop_codex_proxy,
    translate_request,
)


def test_read_codex_model_defaults_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _read_codex_model() == "gpt-5.3-codex"


def test_translate_request_removed() -> None:
    with pytest.raises(RuntimeError, match="Codex translation proxy was removed"):
        _ = translate_request({"messages": []})


def test_start_removed_stop_noop_port_none() -> None:
    with pytest.raises(RuntimeError, match="Codex translation proxy was removed"):
        _ = start_codex_proxy("test-token")
    stop_codex_proxy()
    assert get_codex_proxy_port() is None
