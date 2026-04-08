from __future__ import annotations

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from syke.config import PROJECT_ROOT


@pytest.mark.skipif(importlib.util.find_spec("build") is None, reason="build not installed")
def test_built_wheel_contains_runtime_and_packaged_assets(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
        cwd=str(PROJECT_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )

    wheel = next(tmp_path.glob("syke-*.whl"))
    with zipfile.ZipFile(wheel) as artifact:
        names = set(artifact.namelist())

    for required in (
        "syke/runtime/locator.py",
        "syke/daemon/ipc.py",
        "syke/llm/backends/skills/pi_synthesis.md",
        "syke/observe/catalog.py",
        "syke/observe/seeds/adapter-claude-code.md",
        "syke/observe/seeds/adapter-codex.md",
        "syke/observe/seeds/adapter-opencode.md",
        "syke/observe/seeds/adapter-cursor.md",
        "syke/observe/seeds/adapter-copilot.md",
        "syke/observe/seeds/adapter-antigravity.md",
        "syke/observe/seeds/adapter-hermes.md",
        "syke/observe/seeds/adapter-gemini-cli.md",
    ):
        assert required in names, f"{required} missing from built wheel"


@pytest.mark.skipif(importlib.util.find_spec("build") is None, reason="build not installed")
def test_built_wheel_excludes_stale_build_lib_modules(tmp_path: Path) -> None:
    stale_rel = Path("syke/_stale_build_canary.py")
    stale_path = PROJECT_ROOT / "build" / "lib" / stale_rel
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_text("CANARY = True\n", encoding="utf-8")

    try:
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
            cwd=str(PROJECT_ROOT),
            check=True,
            capture_output=True,
            text=True,
        )

        wheel = next(tmp_path.glob("syke-*.whl"))
        with zipfile.ZipFile(wheel) as artifact:
            names = set(artifact.namelist())

        assert str(stale_rel).replace("\\", "/") not in names
    finally:
        if stale_path.exists():
            stale_path.unlink()
