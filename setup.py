from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


class build_py(_build_py):
    """Ensure wheel builds do not leak stale modules from previous build/ state."""

    def run(self) -> None:
        build_lib = Path(self.build_lib)
        if build_lib.exists():
            shutil.rmtree(build_lib)
        super().run()


setup(cmdclass={"build_py": build_py})
