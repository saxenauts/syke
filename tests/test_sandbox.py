"""Tests for the OS sandbox profile generation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from syke.runtime.sandbox import (
    _harness_read_paths,
    _parent_listing_paths,
    generate_seatbelt_profile,
    sandbox_available,
    wrap_command,
    write_sandbox_profile,
)


def test_profile_starts_with_deny_default(tmp_path: Path) -> None:
    profile = generate_seatbelt_profile(tmp_path)
    lines = profile.split("\n")
    assert "(deny default)" in lines
    assert "(deny file-read*)" in lines


def test_profile_contains_workspace_write(tmp_path: Path) -> None:
    profile = generate_seatbelt_profile(tmp_path)
    assert f'(allow file-write* (subpath "{tmp_path}"))' in profile


def test_profile_contains_workspace_read(tmp_path: Path) -> None:
    profile = generate_seatbelt_profile(tmp_path)
    assert f'(allow file-read* (subpath "{tmp_path}"))' in profile


def test_profile_contains_system_paths(tmp_path: Path) -> None:
    profile = generate_seatbelt_profile(tmp_path)
    assert '(allow file-read* (subpath "/usr"))' in profile
    assert '(allow file-read* (subpath "/bin"))' in profile
    assert '(allow file-read* (subpath "/etc"))' in profile


def test_profile_contains_harness_paths(tmp_path: Path) -> None:
    """Harness paths from catalog appear as subpath read allows."""
    profile = generate_seatbelt_profile(tmp_path)
    harness_paths = _harness_read_paths()
    for p in harness_paths:
        assert f'(allow file-read* (subpath "{p}"))' in profile


def test_profile_does_not_contain_ssh(tmp_path: Path) -> None:
    profile = generate_seatbelt_profile(tmp_path)
    assert ".ssh" not in profile


def test_profile_does_not_contain_gnupg(tmp_path: Path) -> None:
    profile = generate_seatbelt_profile(tmp_path)
    assert ".gnupg" not in profile


def test_parent_listing_paths_are_literal(tmp_path: Path) -> None:
    profile = generate_seatbelt_profile(tmp_path)
    # Parent directory traversal should use literal, not subpath
    lines = [l for l in profile.split("\n") if "Parent directory traversal" in l or "(literal" in l]
    for line in lines:
        if "(literal" in line:
            assert "file-read*" in line
            assert "subpath" not in line


def test_parent_listing_covers_ancestors() -> None:
    paths = _parent_listing_paths(["/Users/test/.claude/projects"])
    assert "/" in paths
    assert "/Users" in paths
    assert "/Users/test" in paths
    assert "/Users/test/.claude" in paths


def test_unique_temp_file_per_call(tmp_path: Path) -> None:
    if not sandbox_available():
        return
    p1 = write_sandbox_profile(tmp_path)
    p2 = write_sandbox_profile(tmp_path)
    assert p1 is not None and p2 is not None
    assert p1 != p2  # Different files — no race
    p1.unlink(missing_ok=True)
    p2.unlink(missing_ok=True)


def test_sandbox_available_returns_bool() -> None:
    result = sandbox_available()
    assert isinstance(result, bool)


def test_sandbox_unavailable_on_non_darwin() -> None:
    with patch.object(sys, "platform", "linux"):
        assert sandbox_available() is False


def test_wrap_command_prepends_sandbox_exec() -> None:
    cmd = ["/usr/bin/node", "agent.js"]
    wrapped = wrap_command(cmd, Path("/tmp/test.sb"))
    assert wrapped[0] == "/usr/bin/sandbox-exec"
    assert wrapped[1] == "-f"
    assert wrapped[2] == "/tmp/test.sb"
    assert wrapped[3:] == cmd
