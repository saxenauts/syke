from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from syke.llm.pi_client import PiRuntime


@pytest.fixture
def pi_runtime(tmp_path: Path) -> Iterator[PiRuntime]:
    if os.environ.get("SYKE_RUN_PI_INTEGRATION") != "1":
        pytest.skip("Pi integration tests are opt-in. Set SYKE_RUN_PI_INTEGRATION=1.")

    workspace_dir = tmp_path / "pi-workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    runtime = PiRuntime(workspace_dir=workspace_dir)

    try:
        runtime.start()
    except FileNotFoundError as exc:
        pytest.skip(f"Pi binary unavailable: {exc}")

    try:
        yield runtime
    finally:
        runtime.stop()


def test_basic_prompt(pi_runtime: PiRuntime) -> None:
    result = pi_runtime.prompt("What is 2+2? Reply with just the number.", timeout=30)
    assert result.ok, f"Pi prompt failed: status={result.status} error={result.error!r}"
    assert result.output.strip()


def test_runtime_status(pi_runtime: PiRuntime) -> None:
    status = pi_runtime.status()
    assert status["alive"] is True
    assert isinstance(status["workspace"], str)
    assert status["model"]


def test_multiple_prompts_reuse_same_runtime(pi_runtime: PiRuntime) -> None:
    first_status = pi_runtime.status()
    first_pid = first_status["pid"]
    assert first_pid is not None

    first = pi_runtime.prompt("Reply with exactly: first", timeout=30)
    assert first.ok, f"First Pi prompt failed: status={first.status} error={first.error!r}"

    second = pi_runtime.prompt("Reply with exactly: second", timeout=30)
    assert second.ok, f"Second Pi prompt failed: status={second.status} error={second.error!r}"

    second_status = pi_runtime.status()
    assert second_status["alive"] is True
    assert second_status["pid"] == first_pid
    assert second_status["uptime_s"] is not None
