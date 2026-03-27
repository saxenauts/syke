from __future__ import annotations

from pathlib import Path

import pytest

from syke.runtime.locator import (
    SykeRuntimeDescriptor,
    describe_runtime_target,
    ensure_syke_launcher,
    resolve_background_syke_runtime,
    resolve_syke_runtime,
)


def _descriptor_for(
    path: Path,
    *,
    mode: str = "external_cli",
    matches_current_checkout: bool = False,
    package_version: str = "0.4.6",
    install_origin: Path | None = None,
) -> SykeRuntimeDescriptor:
    return SykeRuntimeDescriptor(
        mode=mode,
        syke_command=(str(path),),
        target_path=path,
        package_version=package_version,
        install_origin=install_origin,
        matches_current_checkout=matches_current_checkout,
    )


def test_ensure_syke_launcher_writes_exec_script(tmp_path: Path, monkeypatch) -> None:
    launcher_path = tmp_path / "bin" / "syke"
    runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/usr/local/bin/syke",),
        target_path=Path("/usr/local/bin/syke"),
        launcher_path=launcher_path,
    )

    monkeypatch.setattr("syke.runtime.locator.SYKE_BIN_DIR", launcher_path.parent)
    monkeypatch.setattr("syke.runtime.locator.SYKE_BIN", launcher_path)

    result = ensure_syke_launcher(runtime)

    assert result == launcher_path
    text = launcher_path.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert 'exec /usr/local/bin/syke "$@"' in text


def test_resolve_syke_runtime_prefers_current_script(monkeypatch) -> None:
    first = Path("/tmp/venv/bin/syke")
    second = Path("/usr/local/bin/syke")

    monkeypatch.setattr(
        "syke.runtime.locator._candidate_console_scripts",
        lambda: [first, second],
    )
    monkeypatch.setattr(
        "syke.runtime.locator._describe_console_script",
        lambda path: _descriptor_for(path),
    )

    runtime = resolve_syke_runtime()

    assert runtime.syke_command == (str(first),)
    assert runtime.target_path == first


def test_resolve_background_runtime_prefers_matching_safe_install(monkeypatch) -> None:
    protected = Path("/Users/me/Documents/syke/.venv/bin/syke")
    safe = Path("/Users/me/.local/bin/syke")

    runtime_descr = _descriptor_for(
        protected,
        mode="source_dev",
        matches_current_checkout=True,
    )
    safe_descr = _descriptor_for(
        safe,
        mode="external_cli",
        matches_current_checkout=True,
        install_origin=Path("/Users/me/syke"),
    )

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(
        "syke.runtime.locator._candidate_console_scripts",
        lambda: [protected, safe],
    )
    monkeypatch.setattr(
        "syke.runtime.locator._describe_console_script",
        lambda path: runtime_descr if path == protected else safe_descr,
    )
    monkeypatch.setattr(
        "syke.runtime.locator.is_tcc_protected",
        lambda path: path == protected,
    )

    runtime = resolve_background_syke_runtime()

    assert runtime.target_path == safe
    assert runtime.matches_current_checkout
    assert runtime.install_origin == safe_descr.install_origin


def test_resolve_background_runtime_rejects_only_tcc_protected_target(monkeypatch) -> None:
    protected = Path("/Users/me/Documents/syke/.venv/bin/syke")

    runtime_descr = _descriptor_for(
        protected,
        mode="source_dev",
        matches_current_checkout=True,
    )

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("syke.runtime.locator._candidate_console_scripts", lambda: [protected])
    monkeypatch.setattr(
        "syke.runtime.locator._describe_console_script",
        lambda path: runtime_descr,
    )
    monkeypatch.setattr("syke.runtime.locator.is_tcc_protected", lambda path: True)

    with pytest.raises(RuntimeError) as excinfo:
        resolve_background_syke_runtime()

    assert "Cannot install daemon for this source checkout" in str(excinfo.value)


def test_resolve_background_runtime_reports_safe_candidates(monkeypatch) -> None:
    protected = Path("/Users/me/Documents/syke/.venv/bin/syke")
    safe = Path("/Users/me/.local/bin/syke")

    runtime_descr = _descriptor_for(
        protected,
        mode="source_dev",
        matches_current_checkout=True,
    )
    safe_descr = _descriptor_for(
        safe,
        mode="external_cli",
        matches_current_checkout=False,
        install_origin=Path("/Users/me/.local/share/uv/tools/syke"),
    )

    descriptor_lookup = {protected: runtime_descr, safe: safe_descr}

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(
        "syke.runtime.locator._candidate_console_scripts",
        lambda: [protected, safe],
    )
    monkeypatch.setattr(
        "syke.runtime.locator._describe_console_script",
        lambda path: descriptor_lookup[path],
    )
    monkeypatch.setattr(
        "syke.runtime.locator.is_tcc_protected",
        lambda path: path == protected,
    )

    with pytest.raises(RuntimeError) as excinfo:
        resolve_background_syke_runtime()

    msg = str(excinfo.value)
    assert describe_runtime_target(safe_descr) in msg
    assert "Safe installed candidates found:" in msg


def test_resolve_background_runtime_rejects_editable_install_from_protected_checkout(monkeypatch) -> None:
    editable_tool = Path("/Users/me/.local/bin/syke")

    runtime_descr = _descriptor_for(
        editable_tool,
        mode="external_cli",
        matches_current_checkout=True,
        install_origin=Path("/Users/me/Documents/syke"),
    )
    runtime_descr = SykeRuntimeDescriptor(
        **{
            **runtime_descr.__dict__,
            "editable_install": True,
        }
    )

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("syke.runtime.locator.resolve_syke_runtime", lambda: runtime_descr)
    monkeypatch.setattr("syke.runtime.locator._candidate_console_scripts", lambda: [editable_tool])
    monkeypatch.setattr("syke.runtime.locator._describe_console_script", lambda path: runtime_descr)
    monkeypatch.setattr(
        "syke.runtime.locator.is_tcc_protected",
        lambda path: path == runtime_descr.install_origin,
    )

    with pytest.raises(RuntimeError) as excinfo:
        resolve_background_syke_runtime()

    message = str(excinfo.value)
    assert "non-editable tool build" in message
    assert "not safe for launchd" in message
