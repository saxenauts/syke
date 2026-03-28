from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from syke.observe.bootstrap import ensure_adapters
from syke.observe.registry import HarnessRegistry, _ADAPTER_REGISTRY
from syke.sync import sync_source


def _write_session_file(root: Path) -> None:
    session_dir = root / ".claude" / "projects" / "demo"
    session_dir.mkdir(parents=True)
    payload = [
        {
            "timestamp": "2026-03-27T12:00:00",
            "session_id": "s1",
            "role": "user",
            "content": "hello",
            "event_type": "turn",
        },
        {
            "timestamp": "2026-03-27T12:00:01",
            "session_id": "s1",
            "role": "assistant",
            "content": "hi",
            "event_type": "turn",
        },
    ]
    (session_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(line) for line in payload) + "\n",
        encoding="utf-8",
    )


class _Tracker:
    def track(self, _name: str):
        return contextlib.nullcontext(type("_Metrics", (), {"events_processed": 0})())


def test_ensure_adapters_bootstraps_claude_code_and_preserves_descriptor(
    tmp_path: Path,
    db,
    user_id: str,
) -> None:
    _write_session_file(tmp_path)
    _ADAPTER_REGISTRY.pop("claude-code", None)

    with (
        patch.dict("os.environ", {"HOME": str(tmp_path)}),
        patch("syke.config.DATA_DIR", tmp_path / ".syke-data"),
        patch("syke.llm.simple.build_llm_fn", side_effect=RuntimeError("no llm")),
    ):
        results = ensure_adapters(user_id, sources=["claude-code"], llm_fn=None)
        registry = HarnessRegistry(
            dynamic_adapters_dir=tmp_path / ".syke-data" / user_id / "adapters"
        )
        adapter = registry.get_adapter("claude-code", db, user_id)
        ingested = adapter.ingest() if adapter is not None else None

    try:
        assert len(results) == 1
        assert results[0].source == "claude-code"
        assert results[0].status == "generated"
        assert (tmp_path / ".syke-data" / user_id / "adapters" / "claude-code" / "adapter.py").exists()
        assert (
            tmp_path / ".syke-data" / user_id / "adapters" / "claude-code" / "descriptor.toml"
        ).exists()
        assert adapter is not None
        assert ingested is not None
        assert ingested.events_count == 2
    finally:
        _ADAPTER_REGISTRY.pop("claude-code", None)


def test_sync_source_bootstraps_missing_adapter_on_demand(tmp_path: Path, db, user_id: str) -> None:
    _write_session_file(tmp_path)
    _ADAPTER_REGISTRY.pop("claude-code", None)
    output = io.StringIO()

    with (
        patch.dict("os.environ", {"HOME": str(tmp_path)}),
        patch("syke.config.DATA_DIR", tmp_path / ".syke-data"),
        patch("syke.llm.simple.build_llm_fn", side_effect=RuntimeError("no llm")),
    ):
        count = sync_source(
            db,
            user_id,
            "claude-code",
            _Tracker(),
            Console(file=output, force_terminal=False, color_system=None),
        )

    try:
        assert count == 2
        assert (tmp_path / ".syke-data" / user_id / "adapters" / "claude-code" / "adapter.py").exists()
        assert "claude-code" in output.getvalue()
    finally:
        _ADAPTER_REGISTRY.pop("claude-code", None)
