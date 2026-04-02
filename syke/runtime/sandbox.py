"""
Sandbox configuration for the Pi agent runtime.

Generates sandbox.json for Pi's dual-layer sandboxing:
- Filesystem: allow/deny lists for reads and writes
- Network: disabled by default, expandable per config
- OS enforcement: sandbox-exec (macOS) / bubblewrap (Linux)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_sandbox_config(
    workspace_root: Path,
    *,
    allow_network: bool = False,
    allowed_domains: list[str] | None = None,
    extra_read_roots: list[Path] | None = None,
) -> dict:
    """
    Generate sandbox.json configuration for the Pi agent.

    The agent gets:
    - Full read/write access to workspace (except events.db)
    - No access outside workspace
    - No network by default
    - No access to credentials or secrets
    """
    workspace = str(workspace_root)
    allowed_reads = [workspace]
    for root in extra_read_roots or []:
        try:
            allowed_reads.append(str(root.expanduser().resolve()))
        except OSError:
            continue

    config: dict = {
        # --- Reads ---
        "allowRead": sorted(set(allowed_reads)),
        "denyRead": [
            "~/.ssh",
            "~/.aws",
            "~/.gnupg",
            "~/.env",
            "~/.syke/.env",
            "~/.syke/config.toml",
        ],
        # --- Writes ---
        "allowWrite": [
            workspace,
        ],
        "denyWrite": [
            # events.db is immutable — OS-enforced via file permissions AND sandbox
            str(workspace_root / "events.db"),
            str(workspace_root / "events.db-wal"),
            str(workspace_root / "events.db-shm"),
            # Never touch credentials
            "~/.ssh",
            "~/.aws",
            "~/.gnupg",
        ],
    }

    # --- Network ---
    if allow_network and allowed_domains:
        config["allowedDomains"] = allowed_domains
    elif not allow_network:
        # Block all network access by default
        config["deniedDomains"] = ["*"]

    return config


def write_sandbox_config(
    workspace_root: Path,
    **kwargs,
) -> Path:
    """
    Write sandbox.json to the workspace .pi directory.

    Pi auto-discovers .pi/sandbox.json in the working directory.
    """
    pi_dir = workspace_root / ".pi"
    pi_dir.mkdir(exist_ok=True)

    config = generate_sandbox_config(workspace_root, **kwargs)
    config_path = pi_dir / "sandbox.json"

    config_path.write_text(json.dumps(config, indent=2) + "\n")
    logger.info(f"Sandbox config written to {config_path}")
    return config_path
