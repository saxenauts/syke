"""OS-level sandbox for the Pi agent runtime.

Generates a macOS seatbelt profile with deny-default reads. The profile
is personalized per user — harness read paths come from the catalog at
launch time. Only catalog-known harness directories + system paths are
readable. Everything else (~/Documents, ~/.ssh, ~/.gnupg) is denied.

Write access is restricted to ~/.syke/ + workspace + temp dirs.
Network is wide-open outbound (port filtering was tested but parked).

For replay / benchmark: workspaces are placed under ~/.syke-lab/ so
they fall outside ~/Documents (denied) and inside the workspace allow
rule. Per-eval containment uses SYKE_SANDBOX_HARNESS_PATHS to replace
the catalog's live harness paths with only the frozen slice directory.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

from syke.observe.catalog import active_sources
from syke.pi_state import get_pi_agent_dir
from syke.runtime.child_env import child_temp_paths

logger = logging.getLogger(__name__)

# Directories that must never be readable, even if a broad allow
# is accidentally added. Placed as explicit denies after all allows.
_SENSITIVE_DIRS = [
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".docker",
    ".kube",
    ".config/gcloud",
]

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


def _harness_read_paths(selected_sources: tuple[str, ...] | None = None) -> list[str]:
    """Resolve read paths for the sandbox.

    Default behavior is catalog-driven and reads the user's live harness roots.
    For replay / benchmark isolation, `SYKE_SANDBOX_HARNESS_PATHS` can replace
    the catalog entirely with an explicit os.pathsep-delimited allow-list.
    """
    override = os.environ.get("SYKE_SANDBOX_HARNESS_PATHS")
    if override is not None:
        paths: list[str] = []
        seen: set[str] = set()
        for raw in override.split(os.pathsep):
            raw = raw.strip()
            if not raw:
                continue
            try:
                expanded = str(Path(raw).expanduser().resolve())
            except OSError:
                continue
            if expanded not in seen:
                seen.add(expanded)
                paths.append(expanded)
        return paths

    selected_set = set(selected_sources) if selected_sources is not None else None

    paths: list[str] = []
    seen: set[str] = set()
    for spec in active_sources():
        if selected_set is not None and spec.source not in selected_set:
            continue
        for root in spec.discover.roots:
            try:
                expanded = str(Path(root.path).expanduser().resolve())
            except OSError:
                continue
            if expanded not in seen:
                seen.add(expanded)
                paths.append(expanded)
    return paths


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


def _pi_runtime_paths() -> list[str]:
    """Paths Pi needs outside the workspace itself.

    Pi binaries live under ~/.syke/bin and ~/.syke/pi, while auth/settings live
    under the active Pi agent dir, which may be redirected via
    SYKE_PI_AGENT_DIR for replay / benchmark isolation.
    """
    paths = [
        str((Path.home() / ".syke" / "bin").resolve()),
        str((Path.home() / ".syke" / "pi").resolve()),
        str(get_pi_agent_dir()),
    ]
    return list(dict.fromkeys(paths))


def _write_paths(
    workspace_root: Path,
    *,
    temp_paths: list[str] | None = None,
) -> list[str]:
    """Paths the agent can write to."""
    workspace = str(workspace_root.expanduser().resolve())
    paths = [
        workspace,
        *(temp_paths or child_temp_paths()),
        "/dev",
        *_pi_runtime_paths(),
    ]
    aliased: list[str] = []
    for p in paths:
        aliased.extend(_path_aliases(p))
    return list(dict.fromkeys(aliased))


def generate_seatbelt_profile(
    workspace_root: Path,
    *,
    selected_sources: tuple[str, ...] | None = None,
    extra_temp_dirs: tuple[str, ...] | None = None,
) -> str:
    """Generate a macOS seatbelt profile scoped to this user's harnesses.

    deny-default: everything is blocked unless explicitly allowed.
    The workspace (which for replay lives under ~/.syke/replay/) is
    readable and writable. Harness catalog paths are readable.
    ~/.syke/ is readable and writable (Pi runtime + settings locks).
    """
    workspace = str(workspace_root.expanduser().resolve())
    temp_paths = child_temp_paths(extra_temp_dirs=extra_temp_dirs)

    harness_paths = _harness_read_paths(selected_sources=selected_sources)
    all_scoped_paths = [workspace, *temp_paths] + harness_paths + _pi_runtime_paths()
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

    # Temp dirs (read + write)
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

    # Pi runtime — allow only the launcher/runtime dirs plus the active Pi
    # agent dir, not the full ~/.syke tree.
    lines.append("; Pi runtime (launcher + active Pi agent dir)")
    for p in _pi_runtime_paths():
        lines.append(f'(allow file-read* (subpath "{p}"))')
        lines.append(f'(allow file-map-executable (subpath "{p}"))')

    # Resolve the node binary symlink to allow its real location.
    node_bin = Path.home() / ".syke" / "bin" / "node"
    if node_bin.is_symlink():
        real_node_dir = str(node_bin.resolve().parent.parent)
        lines.append(f"; Resolved node runtime ({real_node_dir})")
        for p in _path_aliases(real_node_dir):
            lines.append(f'(allow file-read* (subpath "{p}"))')
            lines.append(f'(allow file-map-executable (subpath "{p}"))')
    lines.append("")

    # Harness data — catalog-scoped, read only
    if harness_paths:
        lines.append("; Harness data — catalog-scoped, read only")
        for p in harness_paths:
            for alias in _path_aliases(p):
                lines.append(f'(allow file-read* (subpath "{alias}"))')
        lines.append("")

    # Parent directory traversal — literal (listing only, not content)
    lines.append("; Parent directory traversal (listing only)")
    for p in parent_paths:
        lines.append(f'(allow file-read* (literal "{p}"))')
    lines.append("")

    # Write access — workspace + active Pi agent dir + temp only
    lines.append("; Write access — workspace + active Pi agent dir + temp only")
    for p in _write_paths(workspace_root, temp_paths=temp_paths):
        lines.append(f'(allow file-write* (subpath "{p}"))')
    lines.append("")

    # Sensitive path denies — defense-in-depth.
    home = str(Path.home())
    lines.append("; Sensitive paths — explicit deny (defense-in-depth)")
    for sensitive in _SENSITIVE_DIRS:
        full = f"{home}/{sensitive}"
        for alias in _path_aliases(full):
            lines.append(f'(deny file-read* (subpath "{alias}"))')
    lines.append("")

    logger.info(
        "Sandbox profile: %d harness read paths, %d parent listing paths",
        len(harness_paths),
        len(parent_paths),
    )
    return "\n".join(lines)


def sandbox_available() -> bool:
    """Check if OS sandbox is available on this platform."""
    if sys.platform != "darwin":
        return False
    return Path("/usr/bin/sandbox-exec").exists()


def write_sandbox_profile(
    workspace_root: Path,
    *,
    selected_sources: tuple[str, ...] | None = None,
    extra_temp_dirs: tuple[str, ...] | None = None,
) -> Path | None:
    """Write the seatbelt profile to a unique temp file. Returns the path."""
    if not sandbox_available():
        return None
    profile = generate_seatbelt_profile(
        workspace_root,
        selected_sources=selected_sources,
        extra_temp_dirs=extra_temp_dirs,
    )
    fd, path_str = tempfile.mkstemp(suffix=".sb", prefix="syke-sandbox-")
    os.write(fd, profile.encode("utf-8"))
    os.close(fd)
    profile_path = Path(path_str)
    logger.info("Sandbox profile written to %s", profile_path)
    return profile_path


def wrap_command(cmd: list[str], profile_path: Path) -> list[str]:
    """Prepend sandbox-exec to a command."""
    return ["/usr/bin/sandbox-exec", "-f", str(profile_path)] + cmd
