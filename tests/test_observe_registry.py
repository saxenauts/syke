from __future__ import annotations

from pathlib import Path

from syke.observe.registry import get_deployed_adapter_md_path


def test_get_deployed_adapter_md_path_prefers_flat_layout(tmp_path: Path) -> None:
    adapters_dir = tmp_path / "adapters"
    adapters_dir.mkdir(parents=True, exist_ok=True)
    expected = adapters_dir / "codex.md"
    expected.write_text("# codex", encoding="utf-8")

    actual = get_deployed_adapter_md_path("codex", adapters_dir=adapters_dir)
    assert actual == expected


def test_get_deployed_adapter_md_path_supports_legacy_nested_layout(tmp_path: Path) -> None:
    adapters_dir = tmp_path / "adapters"
    nested_dir = adapters_dir / "codex"
    nested_dir.mkdir(parents=True, exist_ok=True)
    expected = nested_dir / "adapter.md"
    expected.write_text("# codex", encoding="utf-8")

    actual = get_deployed_adapter_md_path("codex", adapters_dir=adapters_dir)
    assert actual == expected
