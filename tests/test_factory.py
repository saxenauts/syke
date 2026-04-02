from __future__ import annotations

from pathlib import Path

from syke.observe.catalog import active_sources, get_source, iter_discovered_files
from syke.observe.factory import discover, get_seed_adapter_path
from syke.observe.validator import validate_adapter


def test_catalog_contains_expected_rollout_sources() -> None:
    assert [spec.source for spec in active_sources()] == [
        "claude-code",
        "codex",
        "opencode",
        "cursor",
        "copilot",
        "antigravity",
        "hermes",
        "gemini-cli",
    ]


def test_discover_finds_claude_code_from_home_override(tmp_path: Path) -> None:
    session_dir = tmp_path / ".claude" / "projects" / "demo"
    session_dir.mkdir(parents=True)
    (session_dir / "session.jsonl").write_text('{"x":1}\n', encoding="utf-8")

    results = discover(home=tmp_path)

    assert any(item["source"] == "claude-code" for item in results)


def test_seed_adapter_exists_for_claude_code() -> None:
    path = get_seed_adapter_path("claude-code")
    assert path is not None
    assert path.name == "claude-code.py"


def test_seed_validation_passes_on_real_local_claude_data() -> None:
    seed = get_seed_adapter_path("claude-code")
    assert seed is not None
    spec = get_source("claude-code")
    assert spec is not None
    result = validate_adapter(
        "claude-code",
        seed,
        iter_discovered_files(spec)[:20],
    )
    assert result.ok is True
