from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

_SHARD_SUFFIXES = (".calls", ".deps", ".impact")


class build_py(_build_py):
    """Ensure wheel builds are clean and exclude local trimmrog shard modules."""

    def run(self) -> None:
        build_lib = Path(self.build_lib)
        if build_lib.exists():
            shutil.rmtree(build_lib)
        super().run()

    def find_package_modules(self, package: str, package_dir: str):
        modules = super().find_package_modules(package, package_dir)
        return [m for m in modules if not m[1].endswith(_SHARD_SUFFIXES)]


setup(cmdclass={"build_py": build_py})
