#!/usr/bin/env python3
"""Validate the contents of release-built Syke artifacts."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path

REQUIRED_WHEEL_FILES = (
    "syke/runtime/locator.py",
    "syke/runtime/syke_self.md",
    "syke/runtime/skills/self-learn/SKILL.md",
    "syke/daemon/ipc.py",
    "syke/observe/catalog.py",
    "syke/observe/seeds/adapter-claude-code.md",
    "syke/observe/seeds/adapter-codex.md",
    "syke/observe/seeds/adapter-pi.md",
    "syke/observe/seeds/adapter-opencode.md",
    "syke/observe/seeds/adapter-cursor.md",
    "syke/observe/seeds/adapter-copilot.md",
    "syke/observe/seeds/adapter-antigravity.md",
    "syke/observe/seeds/adapter-hermes.md",
)

RETIRED_PACKAGE_FILES = ("syke/observe/seeds/adapter-gemini-cli.md",)

REQUIRED_SDIST_SUFFIXES = (
    "/README.md",
    "/LICENSE",
    "/pyproject.toml",
    "/syke/entrypoint.py",
    "/syke/observe/seeds/adapter-codex.md",
)

FORBIDDEN_SDIST_PREFIXES = (
    "tests/",
    "docs/",
    "scripts/",
    "research/",
    "_internal/",
    ".github/",
)

STALE_BUILD_CANARY = "syke/_stale_build_canary.py"


def check_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as artifact:
        names = set(artifact.namelist())

    missing = sorted(set(REQUIRED_WHEEL_FILES) - names)
    if missing:
        raise RuntimeError(f"{path.name} is missing required files: {', '.join(missing)}")
    forbidden = sorted(set(RETIRED_PACKAGE_FILES) & names)
    if forbidden:
        raise RuntimeError(f"{path.name} contains retired files: {', '.join(forbidden)}")
    if STALE_BUILD_CANARY in names:
        raise RuntimeError(f"{path.name} contains stale build output: {STALE_BUILD_CANARY}")


def check_sdist(path: Path) -> None:
    with tarfile.open(path) as artifact:
        names = {member.name for member in artifact.getmembers()}

    missing = [
        suffix for suffix in REQUIRED_SDIST_SUFFIXES if not any(n.endswith(suffix) for n in names)
    ]
    if missing:
        raise RuntimeError(f"{path.name} is missing required files: {', '.join(missing)}")
    retired = sorted(
        retired
        for retired in RETIRED_PACKAGE_FILES
        if any(name.endswith(f"/{retired}") or name == retired for name in names)
    )
    if retired:
        raise RuntimeError(f"{path.name} contains retired files: {', '.join(retired)}")

    for name in names:
        relative = name.split("/", 1)[1] if "/" in name else name
        if relative.startswith(FORBIDDEN_SDIST_PREFIXES):
            raise RuntimeError(f"{path.name} contains internal repository file: {name}")


def check_artifact(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix == ".whl":
        check_wheel(path)
    elif path.name.endswith(".tar.gz"):
        check_sdist(path)
    else:
        raise ValueError(f"Unsupported artifact type: {path}")
    print(f"[artifact-contents] passed: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()
    for artifact in args.artifacts:
        check_artifact(artifact)


if __name__ == "__main__":
    main()
