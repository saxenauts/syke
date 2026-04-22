from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

import syke.llm.pi_client as pi_client
from syke.llm.pi_client import PiRuntime


@pytest.fixture
def pi_runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[PiRuntime]:
    if os.environ.get("SYKE_RUN_PI_INTEGRATION") != "1":
        pytest.skip("Pi integration tests are opt-in. Set SYKE_RUN_PI_INTEGRATION=1.")
    live_pi_agent_dir = os.environ.get("SYKE_LIVE_PI_AGENT_DIR")
    if not live_pi_agent_dir:
        pytest.skip(
            "Set SYKE_LIVE_PI_AGENT_DIR to a configured Pi agent dir, "
            "for example: SYKE_LIVE_PI_AGENT_DIR=$HOME/.syke/pi-agent"
        )

    pi_agent_dir = Path(live_pi_agent_dir).expanduser().resolve()
    if not (pi_agent_dir / "settings.json").exists():
        pytest.skip(f"Configured Pi settings missing at {pi_agent_dir / 'settings.json'}")

    live_home = pi_agent_dir.parent.parent
    workspace_dir = (
        Path(
            os.environ.get(
                "SYKE_LIVE_WORKSPACE_ROOT", str(pi_agent_dir.parent / "pi-integration-smoke")
            )
        )
        .expanduser()
        .resolve()
    )
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(pi_agent_dir))
    monkeypatch.setenv("HOME", str(live_home))
    monkeypatch.delenv("SYKE_DISABLE_SANDBOX", raising=False)
    monkeypatch.setattr(pi_client, "PI_LOCAL_PREFIX", pi_agent_dir.parent / "pi")
    monkeypatch.setattr(pi_client, "PI_BIN", pi_agent_dir.parent / "bin" / "pi")
    monkeypatch.setattr(pi_client, "PI_NODE_BIN", pi_agent_dir.parent / "bin" / "node")
    monkeypatch.setattr(
        pi_client,
        "PI_PACKAGE_ROOT",
        pi_agent_dir.parent / "pi" / "node_modules" / "@mariozechner" / "pi-coding-agent",
    )
    monkeypatch.setattr(pi_client, "PI_CLI_JS", pi_client.PI_PACKAGE_ROOT / "dist" / "cli.js")

    runtime = PiRuntime(workspace_dir=workspace_dir)

    try:
        runtime.start()
    except FileNotFoundError as exc:
        pytest.skip(f"Pi binary unavailable: {exc}")

    try:
        yield runtime
    finally:
        runtime.stop()
        shutil.rmtree(workspace_dir, ignore_errors=True)


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
