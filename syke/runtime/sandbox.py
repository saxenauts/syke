"""OS-level sandbox for model-invoked tools.

Generates a macOS Seatbelt profile with deny-default access. The current
user's home directory is readable so Syke can inspect computer evidence at
its authoritative path. Ordinary home files remain non-writable.

Persistent write access is restricted to the supplied workspace and durable
runtime subtree. Sessions, receipts, records, and recovery state remain
read-only.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

from syke.config import user_control_dir
from syke.runtime.child_env import child_temp_paths

logger = logging.getLogger(__name__)

# System paths Node.js needs to start and run.
_SYSTEM_READ_PATHS = [
    "/usr",
    "/bin",
    "/sbin",
    "/etc",
    "/private/etc",
    "/System",
    "/Library",
    "/opt/homebrew",
    "/dev",
    "/private/var/db",  # dyld shared cache
    "/private/var/select",  # shell symlinks (sh → bash)
]


def sandbox_read_paths() -> tuple[str, ...]:
    """Return the computer read roots configured for model-invoked tools."""
    return (str(Path.home().expanduser().resolve()),)


def _credential_deny_paths() -> list[str]:
    """Paths under the readable home that model tools must never read.

    Home is readable so Syke can observe the computer, but credentials are not
    evidence. The Pi host process reads its own auth outside the sandbox.
    """
    from syke.pi_state import get_pi_agent_dir

    home = Path.home().expanduser().resolve()
    candidates = [
        get_pi_agent_dir(),
        home / ".syke" / "pi-agent",
        home / ".ssh",
        home / ".aws",
        home / ".gnupg",
        home / ".netrc",
        home / ".config" / "gh",
    ]
    return list(dict.fromkeys(str(Path(c).expanduser().resolve()) for c in candidates))


def _parent_listing_paths(paths: list[str]) -> list[str]:
    """Generate literal (directory-listing-only) rules for parent directories.

    Node.js needs to list parent directories during module resolution.
    Using 'literal' instead of 'subpath' allows directory listing without
    granting access to file contents.
    """
    parents: set[str] = set()
    for p in paths:
        current = Path(p)
        for parent in current.parents:
            s = str(parent)
            if s == "/":
                parents.add("/")
            else:
                parents.update(_path_aliases(s))
    return sorted(parents)


def _path_aliases(path: str) -> list[str]:
    """Return macOS aliases for symlinked roots like /tmp and /private/tmp."""
    if path in {"/", "/private"}:
        return [path]
    aliases = [path]
    if path.startswith("/private/"):
        aliases.append(path.removeprefix("/private"))
    elif not path.startswith("/dev"):
        aliases.append(f"/private{path}")
    return list(dict.fromkeys(aliases))


def _node_runtime_paths() -> list[str]:
    """Runtime files needed by the sandboxed one-shot tool worker."""
    node_link = Path.home() / ".syke" / "bin" / "node"
    paths = [str(node_link.parent.resolve())]
    if node_link.exists():
        paths.append(str(node_link.resolve().parent.parent))
    return list(dict.fromkeys(paths))


def _write_paths(workspace_root: Path, runtime_root: Path) -> list[str]:
    """Persistent paths the controller can write to."""
    workspace = str(workspace_root.expanduser().resolve())
    runtime = str(runtime_root.expanduser().resolve())
    aliased: list[str] = []
    for p in (workspace, runtime, "/dev"):
        aliased.extend(_path_aliases(p))
    return list(dict.fromkeys(aliased))


def generate_seatbelt_profile(
    workspace_root: Path,
    *,
    control_root: Path | None = None,
    runtime_root: Path | None = None,
    extra_temp_dirs: tuple[str, ...] | None = None,
) -> str:
    """Generate a macOS Seatbelt profile for Syke's model tools.

    deny-default: everything is blocked unless explicitly allowed.
    The user's home and protected Syke history are readable. Only the workspace
    and control/runtime are writable.
    """
    workspace_path = workspace_root.expanduser().resolve()
    control_path = (
        control_root.expanduser().resolve()
        if control_root is not None
        else user_control_dir("").resolve()
    )
    runtime_path = (
        runtime_root.expanduser().resolve()
        if runtime_root is not None
        else control_path / "runtime"
    )
    if runtime_path == control_path or not runtime_path.is_relative_to(control_path):
        raise ValueError("Syke runtime must be inside the control boundary")
    if (
        workspace_path == control_path
        or workspace_path.is_relative_to(control_path)
        or control_path.is_relative_to(workspace_path)
    ):
        raise ValueError("Syke workspace and control boundaries must not overlap")

    workspace = str(workspace_path)
    temp_paths = child_temp_paths(extra_temp_dirs=extra_temp_dirs)

    model_read_paths = list(sandbox_read_paths())
    protected_paths = [str(control_path)]
    protected_write_paths = [
        str(control_path / name) for name in ("sessions", "receipts", "records", "recovery")
    ]
    # Installed Syke code is inspectable but never writable.
    core_paths = [str(Path(__file__).resolve().parents[1])]
    all_scoped_paths = (
        [workspace, *temp_paths]
        + model_read_paths
        + _node_runtime_paths()
        + core_paths
        + protected_paths
    )
    parent_paths = _parent_listing_paths(all_scoped_paths)

    lines: list[str] = []

    # Deny everything by default
    lines.append("(version 1)")
    lines.append("(deny default)")
    lines.append("(deny file-read*)")
    lines.append("")

    # Process lifecycle
    lines.append("; Process lifecycle")
    lines.append("(allow process-exec)")
    lines.append("(allow process-fork)")
    lines.append("(allow signal)")
    lines.append("")

    # System calls Node.js needs
    lines.append("; System")
    lines.append("(allow sysctl-read)")
    lines.append("(allow mach-lookup)")
    lines.append("(allow mach-register)")
    lines.append("(allow file-ioctl)")
    lines.append("")

    # Network — outbound allowed.
    lines.append("; Network — outbound for API calls")
    lines.append("(allow network-outbound)")
    lines.append("(allow system-socket)")
    lines.append("")

    # System read paths (subpath = full read access)
    lines.append("; System paths (Node.js runtime)")
    for p in _SYSTEM_READ_PATHS:
        lines.append(f'(allow file-read* (subpath "{p}"))')
        lines.append(f'(allow file-map-executable (subpath "{p}"))')
    lines.append("")

    # Temp dirs are readable. Durable Pi spill output is redirected into the
    # separately writable runtime subtree below.
    lines.append("; Temp directories")
    for temp_path in temp_paths:
        for p in _path_aliases(temp_path):
            lines.append(f'(allow file-read* (subpath "{p}"))')
    lines.append("")

    # Workspace (full read + write)
    lines.append("; Workspace — full access")
    for p in _path_aliases(workspace):
        lines.append(f'(allow file-read* (subpath "{p}"))')
        lines.append(f'(allow file-map-executable (subpath "{p}"))')
    lines.append("")

    lines.append("; Node runtime for sandboxed tool workers")
    for p in _node_runtime_paths():
        lines.append(f'(allow file-read* (subpath "{p}"))')
        lines.append(f'(allow file-map-executable (subpath "{p}"))')
    lines.append("")

    lines.append("; Installed Syke core — inspectable, never writable")
    for p in core_paths:
        for alias in _path_aliases(p):
            lines.append(f'(allow file-read* (subpath "{alias}"))')
    lines.append("")

    # Computer evidence stays authoritative at its original path.
    if model_read_paths:
        lines.append("; Current user home — read only")
        for p in model_read_paths:
            for alias in _path_aliases(p):
                lines.append(f'(allow file-read* (subpath "{alias}"))')
        lines.append("")

    # Later Seatbelt rules win, so these denies override the home read above.
    lines.append("; Credentials — never readable by model tools")
    for p in _credential_deny_paths():
        for alias in _path_aliases(p):
            lines.append(f'(deny file-read* (subpath "{alias}"))')
    lines.append("")

    lines.append("; Syke control state — inspectable; writes scoped below")
    for p in protected_paths:
        for alias in _path_aliases(p):
            lines.append(f'(allow file-read* (subpath "{alias}"))')
    lines.append("")

    # Parent directory traversal — literal (listing only, not content)
    lines.append("; Parent directory traversal (listing only)")
    for p in parent_paths:
        lines.append(f'(allow file-read* (literal "{p}"))')
    lines.append("")

    # Write access — controller-owned learned state plus operational runtime.
    lines.append("; Write access — workspace and durable runtime")
    for p in _write_paths(workspace_path, runtime_path):
        lines.append(f'(allow file-write* (subpath "{p}"))')
    lines.append("")

    lines.append("; Protected Syke evidence — explicit write deny")
    for p in protected_write_paths:
        for alias in _path_aliases(p):
            lines.append(f'(deny file-write* (subpath "{alias}"))')
    lines.append("")

    # The -wal file holds saved memories not yet folded into syke.db, so
    # deleting or renaming it loses them. SQLite still writes all three.
    lines.append("; syke.db and its WAL — writable, never deleted or renamed")
    for name in ("syke.db", "syke.db-wal", "syke.db-shm"):
        for alias in _path_aliases(str(workspace_path / name)):
            lines.append(f'(deny file-write-unlink (literal "{alias}"))')
    lines.append("")

    logger.info(
        "Sandbox profile: %d computer read roots, %d parent listing paths",
        len(model_read_paths),
        len(parent_paths),
    )
    return "\n".join(lines)


def sandbox_available() -> bool:
    """Check if OS sandbox is available on this platform."""
    if sys.platform != "darwin":
        return False
    return Path("/usr/bin/sandbox-exec").exists()


def sandbox_enabled() -> bool:
    """Return whether Syke will apply its model-tool sandbox."""
    return sandbox_available() and not os.environ.get("SYKE_DISABLE_SANDBOX")


def sandbox_runtime_identity() -> str:
    """Return the stable sandbox binding used for Pi runtime reuse."""
    if not sandbox_enabled():
        return "disabled"
    return f"enabled:{os.pathsep.join(sandbox_read_paths())}"


def write_sandbox_profile(
    workspace_root: Path,
    *,
    control_root: Path | None = None,
    runtime_root: Path | None = None,
    extra_temp_dirs: tuple[str, ...] | None = None,
) -> Path | None:
    """Write the seatbelt profile to a unique temp file. Returns the path."""
    if not sandbox_available():
        return None
    profile = generate_seatbelt_profile(
        workspace_root,
        control_root=control_root,
        runtime_root=runtime_root,
        extra_temp_dirs=extra_temp_dirs,
    )
    fd, path_str = tempfile.mkstemp(suffix=".sb", prefix="syke-sandbox-")
    os.write(fd, profile.encode("utf-8"))
    os.close(fd)
    profile_path = Path(path_str)
    logger.info("Sandbox profile written to %s", profile_path)
    return profile_path
