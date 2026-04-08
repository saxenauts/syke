"""OS-level sandbox for the Pi agent runtime.

Generates a macOS seatbelt profile with deny-default reads. The profile
is personalized per user — harness read paths come from the catalog at
launch time. Only catalog-known harness directories + system paths are
readable. Everything else (~/Documents, ~/.ssh, ~/.gnupg) is denied.

Write access is restricted to ~/.syke/ workspace + temp dirs.
Network is port-restricted: HTTPS (443), HTTP (80), DNS (53), localhost.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

from syke.observe.catalog import active_sources

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
]


def _harness_read_paths() -> list[str]:
    """Resolve read paths from the harness catalog. Per-user, per-machine."""
    paths: list[str] = []
    seen: set[str] = set()
    for spec in active_sources():
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
                parents.add(s)
                # macOS may resolve /Users as /private/Users in some contexts
                if not s.startswith("/private"):
                    parents.add(f"/private{s}")
    return sorted(parents)


def _write_paths(workspace_root: Path) -> list[str]:
    """Paths the agent can write to."""
    workspace = str(workspace_root.expanduser().resolve())
    tmpdir = tempfile.gettempdir()
    paths = [
        workspace,
        tmpdir,
        "/dev",
    ]
    # Add /private variants for macOS path resolution
    for p in list(paths):
        if not p.startswith("/private") and not p.startswith("/dev"):
            paths.append(f"/private{p}")
    return list(dict.fromkeys(paths))


def generate_seatbelt_profile(workspace_root: Path) -> str:
    """Generate a macOS seatbelt profile scoped to this user's harnesses."""
    workspace = str(workspace_root.expanduser().resolve())
    tmpdir = tempfile.gettempdir()

    harness_paths = _harness_read_paths()
    all_scoped_paths = [workspace, tmpdir] + harness_paths
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

    # Network — port-restricted outbound
    lines.append("; Network — port-restricted outbound")
    lines.append('(allow network-outbound (remote tcp "*:443"))')   # HTTPS (LLM APIs)
    lines.append('(allow network-outbound (remote tcp "*:80"))')    # HTTP redirects
    lines.append('(allow network-outbound (remote udp "*:53"))')    # DNS
    lines.append('(allow network-outbound (remote tcp "*:53"))')    # DNS over TCP
    lines.append('(allow network-outbound (remote tcp "localhost:*"))')  # Local models
    lines.append("(allow system-socket)")
    lines.append("")

    # System read paths (subpath = full read access)
    lines.append("; System paths (Node.js runtime)")
    for p in _SYSTEM_READ_PATHS:
        lines.append(f'(allow file-read* (subpath "{p}"))')
    lines.append("")

    # Temp dirs (read + write)
    lines.append("; Temp directories")
    lines.append(f'(allow file-read* (subpath "{tmpdir}"))')
    if not tmpdir.startswith("/private"):
        lines.append(f'(allow file-read* (subpath "/private{tmpdir}"))')
    lines.append("")

    # Workspace (full read + write)
    lines.append("; Workspace — full access")
    lines.append(f'(allow file-read* (subpath "{workspace}"))')
    if not workspace.startswith("/private"):
        lines.append(f'(allow file-read* (subpath "/private{workspace}"))')
    lines.append("")

    # Harness data — catalog-scoped, read only
    if harness_paths:
        lines.append("; Harness data — catalog-scoped, read only")
        for p in harness_paths:
            lines.append(f'(allow file-read* (subpath "{p}"))')
            if not p.startswith("/private"):
                lines.append(f'(allow file-read* (subpath "/private{p}"))')
        lines.append("")

    # Parent directory traversal — literal (listing only, not content)
    lines.append("; Parent directory traversal (listing only)")
    for p in parent_paths:
        lines.append(f'(allow file-read* (literal "{p}"))')
    lines.append("")

    # Write access — workspace + temp only
    lines.append("; Write access — workspace + temp only")
    for p in _write_paths(workspace_root):
        lines.append(f'(allow file-write* (subpath "{p}"))')
    lines.append("")

    # Sensitive path denies — defense-in-depth.
    # deny-default already blocks these, but explicit denies override
    # any accidental broad allows added later.
    home = str(Path.home())
    lines.append("; Sensitive paths — explicit deny (defense-in-depth)")
    for sensitive in _SENSITIVE_DIRS:
        full = f"{home}/{sensitive}"
        lines.append(f'(deny file-read* (subpath "{full}"))')
        if not full.startswith("/private"):
            lines.append(f'(deny file-read* (subpath "/private{full}"))')
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


def write_sandbox_profile(workspace_root: Path) -> Path | None:
    """Write the seatbelt profile to a unique temp file. Returns the path."""
    if not sandbox_available():
        return None
    profile = generate_seatbelt_profile(workspace_root)
    fd, path_str = tempfile.mkstemp(suffix=".sb", prefix="syke-sandbox-")
    os.write(fd, profile.encode("utf-8"))
    os.close(fd)
    profile_path = Path(path_str)
    logger.info("Sandbox profile written to %s", profile_path)
    return profile_path


def wrap_command(cmd: list[str], profile_path: Path) -> list[str]:
    """Prepend sandbox-exec to a command."""
    return ["/usr/bin/sandbox-exec", "-f", str(profile_path)] + cmd
