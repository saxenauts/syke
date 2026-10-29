"""Pi process environment for Syke's control-held runtime state."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from syke.memory.learned import read_regular_text
from syke.pi_state import build_pi_agent_env

SELF_LEARN_SKILL_SOURCE = Path(__file__).parent / "skills" / "self-learn" / "SKILL.md"


def launch_skill_paths(workspace_root: Path) -> list[Path]:
    """Skills Pi loads for Syke: its packaged self-learn guide, then its own."""
    own = workspace_root.expanduser().resolve() / "skills"
    return [SELF_LEARN_SKILL_SOURCE, *([own] if own.is_dir() and not own.is_symlink() else [])]


def skills_fingerprint(workspace_root: Path) -> str:
    """Change marker for Syke's own skills, so a new or edited skill restarts Pi."""
    own = workspace_root.expanduser().resolve() / "skills"
    if not own.is_dir():
        return ""
    digest = hashlib.sha256()
    for path in sorted(own.rglob("SKILL.md")):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{path.relative_to(own)}:{stat.st_mtime_ns}:{stat.st_size}\n".encode())
    return digest.hexdigest()[:12]


def record_launch_identity(control_root: Path, files: list[Path]) -> str:
    """Keep the identity text and skills a Pi runtime starts with, for traces."""
    texts = []
    for item in files:
        paths = sorted(item.rglob("SKILL.md")) if item.is_dir() else [item]
        for path in paths:
            try:
                texts.append({"path": str(path), "text": read_regular_text(path, errors="replace")})
            except OSError:
                continue
    body = json.dumps({"files": texts}, ensure_ascii=False, sort_keys=True)
    identity_id = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    folder = control_root / "prompts"
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / f"{identity_id}.json"
    if not snapshot.exists():
        partial = folder / f".{identity_id}.{os.getpid()}.tmp"
        partial.write_text(body, encoding="utf-8")
        partial.replace(snapshot)
    with (folder / "index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": datetime.now(UTC).isoformat(), "id": identity_id}) + "\n")
    return identity_id


def configure_pi_workspace(
    workspace_root: Path,
    *,
    session_dir: Path,
) -> dict[str, str]:
    """Prepare Syke's private Pi resources and return its environment."""
    workspace = workspace_root.expanduser().resolve()
    sessions = session_dir.expanduser().resolve()
    runtime_tmp = sessions.parent / "runtime" / "tmp"
    if runtime_tmp == workspace or runtime_tmp.is_relative_to(workspace):
        raise ValueError("Pi runtime state must be outside the owned workspace")
    runtime_tmp.mkdir(parents=True, exist_ok=True)
    env = build_pi_agent_env(
        {
            "SYKE_PI_TMPDIR": str(runtime_tmp),
            "TMPDIR": str(runtime_tmp),
            "TMP": str(runtime_tmp),
            "TEMP": str(runtime_tmp),
        }
    )
    return env
