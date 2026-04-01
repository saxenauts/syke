#!/usr/bin/env python3

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_release_tag.py vX.Y.Z", file=sys.stderr)
        return 1

    tag = sys.argv[1].strip()
    if not tag.startswith("v"):
        print(f"release tag must start with 'v': {tag}", file=sys.stderr)
        return 1

    version = tag[1:]
    repo_root = Path(__file__).resolve().parent.parent

    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = pyproject["project"]["version"]

    init_text = (repo_root / "syke" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    if match is None:
        print("failed to find __version__ in syke/__init__.py", file=sys.stderr)
        return 1
    package_version = match.group(1)

    if version != project_version or version != package_version:
        print(
            "release tag/version mismatch: "
            f"tag={version}, pyproject={project_version}, package={package_version}",
            file=sys.stderr,
        )
        return 1

    print(f"release tag matches project version: {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
