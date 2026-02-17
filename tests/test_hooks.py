"""Tests for lifecycle hook scripts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "hooks"


@pytest.mark.skipif(
    not (HOOKS_DIR / "syke-session-start.sh").exists(),
    reason="Hook scripts are gitignored — only run locally",
)
def test_session_start_returns_valid_json():
    """SessionStart hook outputs valid JSON with additionalContext."""
    script = HOOKS_DIR / "syke-session-start.sh"

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0

    data = json.loads(result.stdout)
    assert "additionalContext" in data
    assert "get_profile" in data["additionalContext"]


@pytest.mark.skipif(
    not (HOOKS_DIR / "syke-session-stop.sh").exists(),
    reason="Hook scripts are gitignored — only run locally",
)
def test_stop_hook_exits_zero():
    """Stop hook always exits 0 and never blocks."""
    script = HOOKS_DIR / "syke-session-stop.sh"

    # Don't capture output — the background sync process inherits pipe FDs
    # and would cause communicate() to hang. We only care about exit code.
    result = subprocess.run(
        ["bash", str(script)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    assert result.returncode == 0
