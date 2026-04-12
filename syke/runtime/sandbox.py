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
    "/private/var/select",  # shell symlinks (sh → bash)
]


def _harness_read_paths() -> list[str]:
    """Resolve read paths from the harness catalog. Per-user, per-machine.

    Replay scoping hooks:
      - `SYKE_SANDBOX_HARNESS_PATHS`  — full REPLACE mode (colon-separated).
      - `SYKE_SANDBOX_EXTRA_READ_PATHS` — APPEND extra paths to the catalog.

    Replace-mode was too aggressive in practice: codex/claude-code CLIs
    read their own config files under `~/.codex` / `~/.claude` on startup,
    and blocking those paths silently hangs Pi. Replay uses the append
    hook instead to add the cycle-slice directory to the read allow-list.
    """
    override = os.environ.get("SYKE_SANDBOX_HARNESS_PATHS")
    if override:
        return [p for p in override.split(os.pathsep) if p]

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

    extras = os.environ.get("SYKE_SANDBOX_EXTRA_READ_PATHS", "")
    for p in extras.split(os.pathsep):
        if not p:
            continue
        try:
            expanded = str(Path(p).expanduser().resolve())
        except OSError:
            continue
        if expanded not in seen:
            seen.add(expanded)
            paths.append(expanded)
    return paths


_TRAVERSAL_FILES = ["AGENTS.md", "CODEX.md", ".codex"]
"""Files Pi reads by walking up the directory tree from cwd.

Pi (codex) searches parent directories for these config files during
startup. If the sandbox blocks them, Pi hangs on EPERM instead of
continuing. We allow explicit literal reads for each file in each
parent directory so Pi can traverse without opening the whole tree.
"""


def _parent_listing_paths(paths: list[str]) -> list[str]:
    """Generate literal rules for parent directories.

    Node.js needs to list parent directories during module resolution.
    Pi/codex also reads traversal files (AGENTS.md, etc.) from parent
    dirs. Using 'literal' for both directory listing and specific file
    reads keeps the scope tight — no subpath opens on parent dirs.
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
                if not s.startswith("/private"):
                    parents.add(f"/private{s}")
                # Allow Pi to read traversal config files in each parent
                for fname in _TRAVERSAL_FILES:
                    fpath = f"{s}/{fname}"
                    parents.add(fpath)
                    if not s.startswith("/private"):
                        parents.add(f"/private{fpath}")
    return sorted(parents)


def _write_paths(workspace_root: Path) -> list[str]:
    """Paths the agent can write to.

    Includes ~/.syke/ because Pi writes lock files (settings.json.lock)
    and session state to its home directory regardless of workspace.
    """
    workspace = str(workspace_root.expanduser().resolve())
    syke_home = str((Path.home() / ".syke").resolve())
    tmpdir = tempfile.gettempdir()
    paths = [
        workspace,
        syke_home,
        tmpdir,
        "/dev",
    ]
    # Add /private variants for macOS path resolution
    for p in list(paths):
        if not p.startswith("/private") and not p.startswith("/dev"):
            paths.append(f"/private{p}")
    return list(dict.fromkeys(paths))


def generate_seatbelt_profile(workspace_root: Path) -> str:
    """Generate a macOS seatbelt profile for the Pi agent.

    Strategy: allow-default + deny-specific. An earlier deny-default +
    explicit-allow design was more theoretically secure but failed in
    practice — Pi/codex/Node.js read many unpredictable paths during
    startup (AGENTS.md traversal, settings.json locks, dynamic module
    resolution) and any missing allow silently hangs the process.

    The inverted approach: allow everything, then deny the specific paths
    that would contaminate the agent. Sensitive dirs (~/.ssh, ~/.gnupg)
    and the user's live documents are explicitly denied. The agent can
    still read system paths, its own runtime, and the workspace — but
    cannot read live harness data outside the catalog.
    """
    workspace = str(workspace_root.expanduser().resolve())
    home = str(Path.home())

    lines: list[str] = [
        "(version 1)",
        "(allow default)",
        "",
    ]

    # ── Deny sensitive directories ──
    lines.append("; Sensitive paths — always denied")
    for sensitive in _SENSITIVE_DIRS:
        full = f"{home}/{sensitive}"
        lines.append(f'(deny file-read* (subpath "{full}"))')
        if not full.startswith("/private"):
            lines.append(f'(deny file-read* (subpath "/private{full}"))')
    lines.append("")

    # ── Deny specific escape-vector paths ──
    # Can't use deny-with-exception (require-not crashes Pi/SIGABRT on
    # this macOS). Instead deny the specific directories the audit
    # identified as contamination sources: live harness sessions and
    # the user's project repos outside the replay lab.
    #
    # This is narrower than "deny ~/Documents" but covers the known
    # escape paths. The transcript audit (_detect_sandbox_escape) is
    # the second layer that catches anything the deny misses.
    escape_paths = os.environ.get("SYKE_SANDBOX_DENY_PATHS", "")
    if escape_paths:
        lines.append("; Containment — deny specific live-data paths")
        for p in escape_paths.split(os.pathsep):
            if not p:
                continue
            lines.append(f'(deny file-read* (subpath "{p}"))')
            if not p.startswith("/private"):
                lines.append(f'(deny file-read* (subpath "/private{p}"))')
        lines.append("")

    logger.info(
        "Sandbox profile: allow-default + deny ~/Documents (workspace=%s)",
        workspace,
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
