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
        "syke/observe/descriptors/codex.toml",
        "syke/observe/skills/generate_adapter.md",
    ):
        assert required in names, f"{required} missing from built wheel"
