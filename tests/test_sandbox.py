"""Tests for the OS sandbox profile generation."""

from __future__ import annotations

import os
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


def test_profile_respects_selected_sources_filter(tmp_path: Path) -> None:
    all_paths = _harness_read_paths()
    selected_paths = _harness_read_paths(selected_sources=("codex",))
    selected_set = set(selected_paths)
    skipped_paths = [path for path in all_paths if path not in selected_set]

    assert selected_paths
    assert skipped_paths

    profile = generate_seatbelt_profile(tmp_path, selected_sources=("codex",))
    for path in selected_paths:
        assert f'(allow file-read* (subpath "{path}"))' in profile
    for path in skipped_paths:
        assert f'(allow file-read* (subpath "{path}"))' not in profile


def test_harness_paths_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv(
        "SYKE_SANDBOX_HARNESS_PATHS",
        os.pathsep.join(["/tmp/frozen-slice", "/tmp/frozen-slice-2"]),
    )
    resolved = _harness_read_paths()
    assert len(resolved) == 2
    assert resolved[0].endswith("/tmp/frozen-slice")
    assert resolved[1].endswith("/tmp/frozen-slice-2")


def test_profile_scopes_pi_agent_dir(monkeypatch, tmp_path: Path) -> None:
    agent_dir = (tmp_path / "pi-agent").resolve()
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(agent_dir))

    profile = generate_seatbelt_profile(tmp_path)

    assert f'(allow file-read* (subpath "{agent_dir}"))' in profile
    assert f'(allow file-write* (subpath "{agent_dir}"))' in profile

    syke_home = str((Path.home() / ".syke").resolve())
    assert f'(allow file-read* (subpath "{syke_home}"))' not in profile
    assert f'(allow file-write* (subpath "{syke_home}"))' not in profile


def test_profile_denies_ssh(tmp_path: Path) -> None:
    profile = generate_seatbelt_profile(tmp_path)
    for line in profile.split("\n"):
        if ".ssh" in line:
            assert "(deny" in line, f".ssh appears in non-deny rule: {line}"


def test_profile_denies_gnupg(tmp_path: Path) -> None:
    profile = generate_seatbelt_profile(tmp_path)
    for line in profile.split("\n"):
        if ".gnupg" in line:
            assert "(deny" in line, f".gnupg appears in non-deny rule: {line}"


def test_parent_listing_paths_are_literal(tmp_path: Path) -> None:
    profile = generate_seatbelt_profile(tmp_path)
    # Parent directory traversal should use literal, not subpath
    lines = [
        line_text
        for line_text in profile.split("\n")
        if "Parent directory traversal" in line_text or "(literal" in line_text
    ]
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


def test_network_allows_outbound(tmp_path: Path) -> None:
    """Network outbound must be allowed (API calls)."""
    profile = generate_seatbelt_profile(tmp_path)
    assert "(allow network-outbound)" in profile


def test_network_allows_system_socket(tmp_path: Path) -> None:
    profile = generate_seatbelt_profile(tmp_path)
    assert "(allow system-socket)" in profile


def test_sensitive_dirs_explicitly_denied(tmp_path: Path) -> None:
    """Sensitive dirs have explicit deny rules even though deny-default covers them."""
    from syke.runtime.sandbox import _SENSITIVE_DIRS

    profile = generate_seatbelt_profile(tmp_path)
    for d in _SENSITIVE_DIRS:
        assert f"/.{d.lstrip('.')}" in profile or f"/{d}" in profile, f"Missing deny for {d}"
        # Must be a deny, not an allow
        for line in profile.split("\n"):
            if d in line:
                assert "(deny" in line, f"Sensitive dir {d} appears in a non-deny rule: {line}"
