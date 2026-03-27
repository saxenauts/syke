"""Runtime locator for Syke's own executable surfaces.

This is the first slice of the broader runtime-locator plan. It gives Syke a
stable launcher boundary for daemon/service execution instead of binding
background jobs directly to whichever install surface happened to invoke setup.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from syke.config import PROJECT_ROOT, SYKE_HOME, _is_source_install

SYKE_BIN_DIR = SYKE_HOME / "bin"
SYKE_BIN = SYKE_BIN_DIR / "syke"

_SAFE_CLI_CANDIDATES = (
    Path.home() / ".local" / "bin" / "syke",
    Path("/opt/homebrew/bin/syke"),
    Path("/usr/local/bin/syke"),
)


@dataclass(frozen=True)
class SykeRuntimeDescriptor:
    mode: Literal["external_cli", "source_dev", "python_module"]
    syke_command: tuple[str, ...]
    target_path: Path | None
    launcher_path: Path = SYKE_BIN
    working_directory: Path | None = None
    package_version: str | None = None
    install_origin: Path | None = None
    matches_current_checkout: bool = False
    editable_install: bool = False


def is_tcc_protected(path: Path) -> bool:
    """Check if a path lives inside a macOS TCC-protected directory."""
    protected_dirs = (
        Path.home() / "Documents",
        Path.home() / "Desktop",
        Path.home() / "Downloads",
    )
    resolved = path.resolve()
    return any(resolved == directory.resolve() or directory.resolve() in resolved.parents for directory in protected_dirs)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _is_executable_file(path: Path) -> bool:
    return path.exists() and path.is_file() and os.access(path, os.X_OK)


def _current_checkout_root() -> Path | None:
    if not _is_source_install():
        return None
    return PROJECT_ROOT.resolve()


def _classify_script(path: Path) -> Literal["external_cli", "source_dev"]:
    resolved = path.resolve()
    if _is_source_install() and (resolved == PROJECT_ROOT or PROJECT_ROOT in resolved.parents):
        return "source_dev"
    return "external_cli"


def _candidate_console_scripts() -> list[Path]:
    candidates: list[Path] = []

    current_script = Path(sys.executable).resolve().parent / "syke"
    if _is_executable_file(current_script):
        candidates.append(current_script.resolve())

    path_script = shutil.which("syke")
    if path_script:
        path_candidate = Path(path_script).expanduser()
        if _is_executable_file(path_candidate):
            candidates.append(path_candidate.resolve())

    for candidate in _SAFE_CLI_CANDIDATES:
        if _is_executable_file(candidate):
            candidates.append(candidate.resolve())

    return _dedupe_paths(candidates)


def _find_dist_info_dir(script_path: Path) -> Path | None:
    install_root = script_path.resolve().parent.parent
    patterns = (
        "lib/python*/site-packages/syke-*.dist-info",
        "lib64/python*/site-packages/syke-*.dist-info",
        "Lib/site-packages/syke-*.dist-info",
    )
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(sorted(install_root.glob(pattern)))
    return matches[0] if matches else None


def _distribution_metadata_value(script_path: Path, key: str) -> str | None:
    dist_info_dir = _find_dist_info_dir(script_path)
    if dist_info_dir is None:
        return None
    metadata_path = dist_info_dir / "METADATA"
    if not metadata_path.exists():
        return None
    for line in metadata_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith(f"{key}:"):
            continue
        return line.partition(":")[2].strip() or None
    return None


def _install_metadata(script_path: Path) -> tuple[Path | None, bool]:
    dist_info_dir = _find_dist_info_dir(script_path)
    if dist_info_dir is None:
        return None, False
    direct_url_path = dist_info_dir / "direct_url.json"
    if not direct_url_path.exists():
        return None, False
    try:
        payload = json.loads(direct_url_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, False

    url = payload.get("url")
    if not isinstance(url, str) or not url:
        return None, False

    dir_info = payload.get("dir_info")
    editable = isinstance(dir_info, dict) and bool(dir_info.get("editable"))

    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None, editable

    raw_path = unquote(parsed.path or "")
    if sys.platform == "win32" and raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    if not raw_path:
        return None, editable
    return Path(raw_path).resolve(), editable


def _describe_console_script(path: Path) -> SykeRuntimeDescriptor:
    resolved = path.resolve()
    checkout_root = _current_checkout_root()
    install_origin, editable_install = _install_metadata(resolved)
    matches_current_checkout = False
    if checkout_root is not None:
        matches_current_checkout = (
            install_origin == checkout_root
            or resolved == checkout_root
            or checkout_root in resolved.parents
        )

    return SykeRuntimeDescriptor(
        mode=_classify_script(resolved),
        syke_command=(str(resolved),),
        target_path=resolved,
        package_version=_distribution_metadata_value(resolved, "Version"),
        install_origin=install_origin,
        matches_current_checkout=matches_current_checkout,
        editable_install=editable_install,
    )


def describe_runtime_target(runtime: SykeRuntimeDescriptor) -> str:
    target = runtime.target_path or Path(runtime.syke_command[0])
    details = [runtime.mode]
    if runtime.package_version:
        details.append(f"v{runtime.package_version}")
    if runtime.editable_install:
        details.append("editable")
    if runtime.matches_current_checkout:
        details.append("matches current checkout")
    elif runtime.install_origin is not None:
        details.append(f"origin {runtime.install_origin}")
    return f"{target} ({', '.join(details)})"


def resolve_syke_runtime(*, prefer_external: bool = False) -> SykeRuntimeDescriptor:
    """Resolve the current Syke runtime command for the active install surface."""
    for candidate in _candidate_console_scripts():
        descriptor = _describe_console_script(candidate)
        mode = descriptor.mode
        if prefer_external and mode == "source_dev":
            continue
        return descriptor

    python_executable = Path(sys.executable).resolve()
    return SykeRuntimeDescriptor(
        mode="python_module",
        syke_command=(str(python_executable), "-m", "syke"),
        target_path=python_executable,
        working_directory=PROJECT_ROOT if _is_source_install() else None,
        matches_current_checkout=_is_source_install(),
    )


def _raise_source_dev_background_error(
    runtime: SykeRuntimeDescriptor,
    safe_candidates: list[SykeRuntimeDescriptor],
) -> None:
    checkout_root = _current_checkout_root() or PROJECT_ROOT.resolve()
    lines = [
        "Cannot install daemon for this source checkout: the active Syke runtime is inside "
        "a macOS-protected directory, and no safe installed Syke target could prove it "
        "matches this checkout.",
        "",
        f"Current runtime: {describe_runtime_target(runtime)}",
        f"Current checkout: {checkout_root}",
    ]

    if safe_candidates:
        lines.append("Safe installed candidates found:")
        lines.extend(f"  - {describe_runtime_target(candidate)}" for candidate in safe_candidates)
    else:
        lines.append("Safe installed candidates found: none")

    lines.extend(
        [
            "",
            "Fix: install this checkout as a non-editable tool build, then retry:",
            "  pipx install .",
            "  uv tool install --force --reinstall --refresh --no-cache .",
            "",
            "Editable installs that import code from a repo under ~/Documents, ~/Desktop, or",
            "~/Downloads are not safe for launchd.",
            "Otherwise move the repo outside those protected directories.",
        ]
    )
    raise RuntimeError("\n".join(lines))


def _is_background_safe_runtime(runtime: SykeRuntimeDescriptor) -> bool:
    if runtime.target_path is None or is_tcc_protected(runtime.target_path):
        return False
    if runtime.editable_install and runtime.install_origin is not None and is_tcc_protected(runtime.install_origin):
        return False
    return True


def resolve_background_syke_runtime() -> SykeRuntimeDescriptor:
    """Resolve the Syke runtime command safe for background/service execution."""
    runtime = resolve_syke_runtime()
    if sys.platform != "darwin":
        return runtime

    if _is_background_safe_runtime(runtime):
        return runtime

    safe_candidates: list[SykeRuntimeDescriptor] = []
    for candidate in _candidate_console_scripts():
        descriptor = _describe_console_script(candidate)
        if _is_background_safe_runtime(descriptor):
            safe_candidates.append(descriptor)

    if runtime.matches_current_checkout:
        for candidate in safe_candidates:
            if candidate.matches_current_checkout:
                return candidate
        _raise_source_dev_background_error(runtime, safe_candidates)

    for candidate in safe_candidates:
        return candidate

    target = runtime.target_path or Path(sys.executable).resolve()
    raise RuntimeError(
        "Cannot install daemon: resolved Syke runtime path is inside a macOS-protected "
        f"directory ({target}). launchd will be blocked by TCC.\n\n"
        "Fix: install Syke to a non-protected location:\n"
        "  pipx install syke\n"
        "  uv tool install syke\n\n"
        "Or for the current branch:\n"
        "  pipx install .\n"
        "  uv tool install --force --reinstall --refresh --no-cache .\n\n"
        "If you are developing from source under ~/Documents, run the daemon in the foreground "
        "or move/install Syke outside a TCC-protected directory."
    )


def ensure_syke_launcher(runtime: SykeRuntimeDescriptor | None = None) -> Path:
    """Write the stable Syke launcher used by daemon/service registrations."""
    runtime = runtime or resolve_syke_runtime()
    launcher_path = runtime.launcher_path

    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_lines = ["#!/bin/sh"]
    if runtime.working_directory is not None:
        launcher_lines.append(f'cd {shlex.quote(str(runtime.working_directory))} || exit 1')
    command = " ".join(shlex.quote(part) for part in runtime.syke_command)
    launcher_lines.append(f'exec {command} "$@"')
    launcher_text = "\n".join(launcher_lines) + "\n"

    launcher_path.write_text(launcher_text, encoding="utf-8")
    launcher_path.chmod(
        launcher_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    return launcher_path
