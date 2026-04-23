from __future__ import annotations

import importlib.util
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from syke.config import PROJECT_ROOT


@pytest.mark.skipif(importlib.util.find_spec("build") is None, reason="build not installed")
def test_built_sdist_excludes_internal_repo_surfaces(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(tmp_path)],
        cwd=str(PROJECT_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )

    sdist = next(tmp_path.glob("syke-*.tar.gz"))
    with tarfile.open(sdist) as artifact:
        names = {member.name for member in artifact.getmembers()}

    assert any(name.endswith("/README.md") for name in names)
    assert any(name.endswith("/LICENSE") for name in names)
    assert any(name.endswith("/pyproject.toml") for name in names)
    assert any(name.endswith("/syke/entrypoint.py") for name in names)
    assert any(name.endswith("/syke/observe/seeds/adapter-codex.md") for name in names)

    forbidden_prefixes = (
        "tests/",
        "docs/",
        "scripts/",
        "research/",
        "_internal/",
        ".github/",
    )
    for name in names:
        suffix = name.split("/", 1)[1] if "/" in name else name
        assert not suffix.startswith(forbidden_prefixes), f"Unexpected file in sdist: {name}"
