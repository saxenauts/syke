"""Tests for the OS sandbox profile generation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from syke.runtime.child_env import (
    build_child_process_env,
    darwin_user_temp_dir,
    temp_paths_from_env,
)
from syke.runtime.sandbox import (
    _parent_listing_paths,
    _path_aliases,
    generate_seatbelt_profile,
    sandbox_available,
    sandbox_enabled,
    sandbox_read_paths,
    write_sandbox_profile,
)


def _workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


def _sandboxed(cmd: list[str], profile_path: Path) -> list[str]:
    return ["/usr/bin/sandbox-exec", "-f", str(profile_path), *cmd]


def test_profile_declares_the_current_authority_boundary(monkeypatch, tmp_path: Path) -> None:
    from syke.runtime import sandbox as sandbox_module

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    control = tmp_path / "control"
    runtime = control / "runtime"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    profile = generate_seatbelt_profile(
        workspace,
        control_root=control,
        runtime_root=runtime,
    )

    assert "(deny default)" in profile
    assert "(deny file-read*)" in profile
    assert "(allow network-outbound)" in profile
    assert "(allow system-socket)" in profile
    assert '(allow file-read* (subpath "/usr"))' in profile
    assert f'(allow file-read* (subpath "{workspace.resolve()}"))' in profile
    assert f'(allow file-write* (subpath "{workspace.resolve()}"))' in profile
    assert f'(allow file-read* (subpath "{home.resolve()}"))' in profile
    assert f'(allow file-write* (subpath "{home.resolve()}"))' not in profile

    core = Path(sandbox_module.__file__).resolve().parents[1]
    assert f'(allow file-read* (subpath "{core}"))' in profile
    assert f'(allow file-write* (subpath "{core}"))' not in profile
    assert f'(allow file-read* (subpath "{control.resolve()}"))' in profile
    assert f'(allow file-write* (subpath "{runtime.resolve()}"))' in profile
    for name in ("sessions", "receipts", "records", "recovery"):
        assert f'(deny file-write* (subpath "{control.resolve() / name}"))' in profile
    assert "signals" not in profile
    assert "Legacy runtime pointer" not in profile


def test_sandbox_read_paths_default_to_home(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    assert sandbox_read_paths() == (str(home.resolve()),)


@pytest.mark.platform
def test_profile_enforces_home_read_only_and_syke_write_boundary(
    monkeypatch, tmp_path: Path
) -> None:
    if not sandbox_available():
        pytest.fail("macOS sandbox-exec is unavailable")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    source_file = home / "notes.txt"
    source_file.write_text("remember this\n", encoding="utf-8")
    dot_dir = home / ".private"
    dot_dir.mkdir()
    dot_file = dot_dir / "note.txt"
    dot_file.write_text("also readable\n", encoding="utf-8")
    ssh_config = home / ".ssh" / "config"
    ssh_config.parent.mkdir()
    ssh_config.write_text("ordinary config\n", encoding="utf-8")
    codex_config = home / ".codex" / "config.toml"
    codex_config.parent.mkdir()
    codex_config.write_text("model = 'configured'\n", encoding="utf-8")
    pi_auth = home / ".syke" / "pi-agent" / "auth.json"
    pi_auth.parent.mkdir(parents=True)
    pi_auth.write_text("placeholder credential\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    control = tmp_path / "control"
    workspace.mkdir()
    protected_file = control / "receipts" / "cycle.json"
    protected_file.parent.mkdir(parents=True)
    protected_file.write_text('{"status":"completed"}\n', encoding="utf-8")
    runtime = control / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("SYKE_CONTROL_ROOT", str(control))

    profile_path = write_sandbox_profile(workspace)
    assert profile_path is not None
    try:
        read_result = subprocess.run(
            _sandboxed(
                [
                    "/bin/cat",
                    str(source_file),
                    str(dot_file),
                    str(codex_config),
                ],
                profile_path,
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        credential_results = [
            subprocess.run(
                _sandboxed(["/bin/cat", str(secret)], profile_path),
                check=False,
                capture_output=True,
                text=True,
            )
            for secret in (pi_auth, ssh_config)
        ]
        workspace_result = subprocess.run(
            _sandboxed(["/usr/bin/touch", str(workspace / "allowed")], profile_path),
            check=False,
            capture_output=True,
            text=True,
        )
        runtime_result = subprocess.run(
            _sandboxed(["/usr/bin/touch", str(runtime / "allowed")], profile_path),
            check=False,
            capture_output=True,
            text=True,
        )
        control_result = subprocess.run(
            _sandboxed(["/usr/bin/touch", str(control / "blocked")], profile_path),
            check=False,
            capture_output=True,
            text=True,
        )
        protected_write_result = subprocess.run(
            _sandboxed(
                ["/bin/sh", "-c", 'printf changed >> "$1"', "sh", str(protected_file)],
                profile_path,
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        create_result = subprocess.run(
            _sandboxed(["/usr/bin/touch", str(home / "blocked")], profile_path),
            check=False,
            capture_output=True,
            text=True,
        )
        edit_result = subprocess.run(
            _sandboxed(
                ["/bin/sh", "-c", 'printf changed >> "$1"', "sh", str(source_file)],
                profile_path,
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        rename_result = subprocess.run(
            _sandboxed(["/bin/mv", str(dot_file), str(dot_dir / "renamed.txt")], profile_path),
            check=False,
            capture_output=True,
            text=True,
        )
        delete_result = subprocess.run(
            _sandboxed(["/bin/rm", str(source_file)], profile_path),
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        profile_path.unlink(missing_ok=True)

    assert read_result.returncode == 0
    assert read_result.stdout == "remember this\nalso readable\nmodel = 'configured'\n"
    for credential_result in credential_results:
        assert credential_result.returncode != 0
        assert "placeholder credential" not in credential_result.stdout
        assert "ordinary config" not in credential_result.stdout
    assert workspace_result.returncode == 0
    assert (workspace / "allowed").exists()
    assert runtime_result.returncode == 0
    assert (runtime / "allowed").exists()
    assert control_result.returncode != 0
    assert not (control / "blocked").exists()
    assert protected_write_result.returncode != 0
    assert protected_file.read_text(encoding="utf-8") == '{"status":"completed"}\n'
    assert create_result.returncode != 0
    assert edit_result.returncode != 0
    assert rename_result.returncode != 0
    assert delete_result.returncode != 0
    assert not (home / "blocked").exists()
    assert source_file.read_text(encoding="utf-8") == "remember this\n"
    assert dot_file.read_text(encoding="utf-8") == "also readable\n"
    assert not (dot_dir / "renamed.txt").exists()


@pytest.mark.platform
def test_model_tools_write_syke_db_but_cannot_delete_its_wal(tmp_path: Path) -> None:
    if not sandbox_available():
        pytest.fail("macOS sandbox-exec is unavailable")
    import sqlite3

    workspace = tmp_path / "workspace"
    control = tmp_path / "control"
    workspace.mkdir()
    (control / "runtime").mkdir(parents=True)
    db_path = workspace / "syke.db"
    profile_path = tmp_path / "profile.sb"
    profile_path.write_text(
        generate_seatbelt_profile(workspace, control_root=control), encoding="utf-8"
    )
    host = sqlite3.connect(db_path)
    host.execute("PRAGMA journal_mode=WAL")
    host.execute("CREATE TABLE t (x)")
    host.commit()

    write = subprocess.run(
        _sandboxed(["/usr/bin/sqlite3", str(db_path), "INSERT INTO t VALUES (1)"], profile_path),
        check=False,
        capture_output=True,
        text=True,
    )
    removals = [
        subprocess.run(_sandboxed(cmd, profile_path), check=False, capture_output=True)
        for cmd in (
            ["/bin/rm", "-f", f"{db_path}-wal", f"{db_path}-shm"],
            ["/bin/mv", f"{db_path}-wal", str(workspace / "moved")],
            ["/bin/rm", str(db_path)],
        )
    ]

    assert write.returncode == 0, write.stderr
    assert all(result.returncode != 0 for result in removals)
    assert Path(f"{db_path}-wal").exists()
    assert host.execute("SELECT count(*) FROM t").fetchone() == (1,)
    host.close()


def test_parent_listing_uses_literal_ancestors_and_macos_aliases(tmp_path: Path) -> None:
    paths = _parent_listing_paths(["/Users/test/.claude/projects", "/private/tmp/syke/bin"])
    assert {"/", "/Users", "/Users/test", "/Users/test/.claude"} <= set(paths)
    assert {"/tmp", "/tmp/syke"} <= set(paths)
    assert "/tmp/syke/bin" in _path_aliases("/private/tmp/syke/bin")

    profile = generate_seatbelt_profile(_workspace(tmp_path))
    parent_rules = [
        line for line in profile.splitlines() if line.startswith("(allow") and "literal" in line
    ]
    assert parent_rules
    assert all("file-read*" in line and "subpath" not in line for line in parent_rules)


def test_redirected_caller_tmpdir_cannot_strand_child_runtime_temp(
    monkeypatch, tmp_path: Path
) -> None:
    """#46 regression: a caller with a redirected $TMPDIR must never yield a
    profile whose temp allow-list excludes the temp dir the child will use.

    Mirrors the PiRuntime.start wiring: the child env comes from
    build_child_process_env and the profile gets that env's temp paths as
    extra_temp_dirs.
    """
    redirected = tmp_path / "harness-tmp"
    redirected.mkdir()
    monkeypatch.setenv("TMPDIR", str(redirected))
    monkeypatch.delenv("TMP", raising=False)
    monkeypatch.delenv("TEMP", raising=False)
    monkeypatch.delenv("SYKE_PI_TMPDIR", raising=False)

    env = build_child_process_env({}, provider=None)
    profile = generate_seatbelt_profile(
        _workspace(tmp_path),
        extra_temp_dirs=temp_paths_from_env(env),
    )

    child_tmp = env["TMPDIR"]
    assert f'(allow file-read* (subpath "{child_tmp}"))' in profile
    assert f'(allow file-write* (subpath "{child_tmp}"))' not in profile

    if sys.platform == "darwin":
        darwin_temp = darwin_user_temp_dir()
        if darwin_temp:
            # The child is steered to the macOS per-user temp, not the
            # caller's redirected dir, and the profile allows it.
            assert child_tmp == darwin_temp


@pytest.mark.platform
def test_unique_temp_file_per_call(tmp_path: Path) -> None:
    if not sandbox_available():
        pytest.fail("macOS sandbox-exec is unavailable")
    p1 = write_sandbox_profile(_workspace(tmp_path))
    p2 = write_sandbox_profile(_workspace(tmp_path))
    assert p1 is not None and p2 is not None
    assert p1 != p2  # Different files — no race
    p1.unlink(missing_ok=True)
    p2.unlink(missing_ok=True)


def test_sandbox_activation_requires_macos_availability_and_no_override(monkeypatch) -> None:
    with patch.object(sys, "platform", "linux"):
        assert sandbox_available() is False

    monkeypatch.setattr("syke.runtime.sandbox.sandbox_available", lambda: True)
    monkeypatch.delenv("SYKE_DISABLE_SANDBOX", raising=False)
    assert sandbox_enabled() is True

    monkeypatch.setenv("SYKE_DISABLE_SANDBOX", "1")
    assert sandbox_enabled() is False
