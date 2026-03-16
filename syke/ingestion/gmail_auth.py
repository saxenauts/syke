"""Gmail auth utilities — extracted from gmail.py for use by health checks."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


_HAS_GMAIL_DEPS = all(
    _module_available(m) for m in ("google.auth", "google_auth_oauthlib", "googleapiclient")
)


def _gog_authenticated(account: str) -> bool:
    if not shutil.which("gog"):
        return False
    try:
        r = subprocess.run(["gog", "auth", "list"], capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and "No tokens stored" not in r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return False


def _python_oauth_available() -> bool:
    return _HAS_GMAIL_DEPS
