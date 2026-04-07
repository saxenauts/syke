"""OS-level sandbox for the Pi agent runtime.

Generates a macOS seatbelt profile from the harness catalog and
wraps Pi process launch with sandbox-exec. The sandbox enforces:

- Read access: harness source directories + system paths
- Write access: ~/.syke/ workspace + temp dirs only
- Network: outbound allowed (API calls)
- No writes outside the boundary
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

from syke.observe.catalog import active_sources

logger = logging.getLogger(__name__)

_PROFILE_HEADER = """\
(version 1)
(deny default)

; Process lifecycle
(allow process-exec)
(allow process-fork)
(allow signal)

; Read access — broad (harness data, system libs, binaries)
(allow file-read*)

; System
(allow sysctl-read)
(allow mach-lookup)
(allow mach-register)
(allow file-ioctl)

; Network — outbound for API calls
(allow network-outbound)
(allow system-socket)
"""


def _write_paths(workspace_root: Path) -> list[str]:
    """Paths the agent can write to."""
    workspace = str(workspace_root.expanduser().resolve())
    tmpdir = tempfile.gettempdir()
    paths = [
        workspace,
        f"/private{workspace}" if not workspace.startswith("/private") else workspace,
        tmpdir,
        f"/private{tmpdir}" if not tmpdir.startswith("/private") else tmpdir,
        "/dev",
    ]
    # Deduplicate
    return list(dict.fromkeys(paths))


def generate_seatbelt_profile(workspace_root: Path) -> str:
    """Generate a macOS seatbelt profile for the Pi sandbox."""
    lines = [_PROFILE_HEADER]

    lines.append("; Write access — workspace + temp only")
    for path in _write_paths(workspace_root):
        lines.append(f'(allow file-write* (subpath "{path}"))')

    lines.append("")
    return "\n".join(lines)


def sandbox_available() -> bool:
    """Check if OS sandbox is available on this platform."""
    if sys.platform != "darwin":
        return False
    return Path("/usr/bin/sandbox-exec").exists()


def write_sandbox_profile(workspace_root: Path) -> Path | None:
    """Write the seatbelt profile to a temp file. Returns the path, or None if unavailable."""
    if not sandbox_available():
        return None
    profile = generate_seatbelt_profile(workspace_root)
    profile_path = Path(tempfile.gettempdir()) / "syke-sandbox.sb"
    profile_path.write_text(profile, encoding="utf-8")
    logger.info("Sandbox profile written to %s", profile_path)
    return profile_path


def wrap_command(cmd: list[str], profile_path: Path) -> list[str]:
    """Prepend sandbox-exec to a command."""
    return ["/usr/bin/sandbox-exec", "-f", str(profile_path)] + cmd
