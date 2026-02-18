"""PyPI version check with 24-hour cache.

Pure stdlib — no new dependencies.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from syke.config import SYKE_HOME

PYPI_URL = "https://pypi.org/pypi/syke/json"
CACHE_PATH = SYKE_HOME / "version_cache.json"
CACHE_TTL_SECONDS = 86400  # 24 hours


def _version_gt(a: str, b: str) -> bool:
    """Return True if version a is greater than version b (semver tuple compare)."""
    try:
        return tuple(int(x) for x in a.split(".")) > tuple(int(x) for x in b.split("."))
    except (ValueError, AttributeError):
        return False


def _read_cache() -> str | None:
    """Read cached latest version without hitting the network.

    Returns the cached version string if the cache exists and is within TTL,
    otherwise returns None.
    """
    try:
        if not CACHE_PATH.exists():
            return None
        data = json.loads(CACHE_PATH.read_text())
        age = time.time() - data.get("timestamp", 0)
        if age > CACHE_TTL_SECONDS:
            return None
        return data.get("version")
    except Exception:
        return None


def _write_cache(version: str) -> None:
    """Write version to cache file with current timestamp."""
    try:
        SYKE_HOME.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps({"version": version, "timestamp": time.time()}))
    except Exception:
        pass


def get_latest_version(timeout: int = 5) -> str | None:
    """Return the latest PyPI version of syke, using a 24h cache.

    Returns None on any network or parsing failure — never raises.
    """
    cached = _read_cache()
    if cached is not None:
        return cached

    try:
        with urllib.request.urlopen(PYPI_URL, timeout=timeout) as resp:
            data = json.loads(resp.read())
        version = data["info"]["version"]
        _write_cache(version)
        return version
    except Exception:
        return None


def check_update_available(installed: str) -> tuple[bool, str | None]:
    """Check if a newer version of syke is available on PyPI.

    Returns (update_available, latest_version).
    latest_version is None if the check failed.
    """
    latest = get_latest_version()
    if latest is None:
        return False, None
    return _version_gt(latest, installed), latest
