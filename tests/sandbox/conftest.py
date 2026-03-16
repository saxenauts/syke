"""Sandbox fixtures — isolated DB and DynamicAdapter factories."""

from __future__ import annotations

from pathlib import Path

import pytest

from syke.db import SykeDB
from syke.sense.dynamic_adapter import DynamicAdapter


SANDBOX_USER = "sandbox-user"

_CLAUDE_PARSE_LINE = """\
import json

def parse_line(line):
    data = json.loads(line)
    msg = data.get("message", {})
    content_blocks = msg.get("content", [])
    text = ""
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            break
    return {
        "timestamp": data.get("timestamp"),
        "session_id": data.get("sessionId"),
        "parent_session_id": data.get("parentSessionId"),
        "role": data.get("type"),
        "content": text,
        "event_type": "turn" if data.get("type") in ("user", "assistant") else data.get("type"),
    }
"""

_CODEX_PARSE_LINE = """\
import json

def parse_line(line):
    data = json.loads(line)
    if data.get("type") == "session_meta":
        return None
    payload = data.get("payload", {})
    role = payload.get("role", "assistant")
    content_blocks = payload.get("content", [])
    text = ""
    for block in content_blocks:
        if isinstance(block, dict):
            text = block.get("text", "")
            break
    return {
        "timestamp": data.get("timestamp"),
        "session_id": None,
        "role": role,
        "content": text,
        "event_type": "turn" if payload.get("type") == "message" else payload.get("type", "turn"),
    }
"""


@pytest.fixture
def sandbox_db(tmp_path):
    with SykeDB(tmp_path / "sandbox.db") as db:
        yield db


@pytest.fixture
def user_id():
    return SANDBOX_USER


@pytest.fixture
def sandbox_dir(tmp_path):
    d = tmp_path / "sandbox"
    d.mkdir()
    (d / ".claude" / "projects").mkdir(parents=True)
    (d / ".claude" / "transcripts").mkdir(parents=True)
    (d / ".codex" / "sessions").mkdir(parents=True)
    return d


def _write_adapter_to_disk(tmp_path: Path, source: str, code: str) -> Path:
    adapter_dir = tmp_path / "adapters" / source
    adapter_dir.mkdir(parents=True, exist_ok=True)
    (adapter_dir / "adapter.py").write_text(code)
    return adapter_dir


@pytest.fixture
def claude_adapter(sandbox_db, user_id, sandbox_dir, tmp_path):
    adapter_dir = _write_adapter_to_disk(tmp_path, "claude-code", _CLAUDE_PARSE_LINE)
    adapter = DynamicAdapter(
        db=sandbox_db,
        user_id=user_id,
        source_name="claude-code",
        adapter_dir=adapter_dir,
        discover_roots=[sandbox_dir / ".claude"],
    )
    return adapter, sandbox_dir


@pytest.fixture
def codex_adapter(sandbox_db, user_id, sandbox_dir, tmp_path):
    adapter_dir = _write_adapter_to_disk(tmp_path, "codex", _CODEX_PARSE_LINE)
    adapter = DynamicAdapter(
        db=sandbox_db,
        user_id=user_id,
        source_name="codex",
        adapter_dir=adapter_dir,
        discover_roots=[sandbox_dir / ".codex"],
    )
    return adapter, sandbox_dir


def run_adapter(adapter, home_dir):
    from unittest.mock import patch

    with patch.dict("os.environ", {"HOME": str(home_dir)}):
        return adapter.ingest()
