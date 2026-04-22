from __future__ import annotations

import json
from pathlib import Path

import pytest

from syke.source_selection import get_selected_sources, set_selected_sources


def test_selected_sources_round_trip_persists_ordered_unique_values() -> None:
    saved = set_selected_sources("test", ["codex", "claude-code", "codex"])
    loaded = get_selected_sources("test")

    assert saved == ("codex", "claude-code")
    assert loaded == ("codex", "claude-code")


def test_selected_sources_returns_none_when_not_configured() -> None:
    assert get_selected_sources("test") is None


def test_set_selected_sources_rejects_unknown_source() -> None:
    with pytest.raises(ValueError, match="Unknown source: fake-source"):
        set_selected_sources("test", ["fake-source"])


def test_get_selected_sources_ignores_corrupt_file(tmp_path: Path, monkeypatch) -> None:
    selection_file = tmp_path / "source_selection.json"
    selection_file.write_text("{", encoding="utf-8")

    monkeypatch.setattr(
        "syke.source_selection._selection_path",
        lambda _user_id: selection_file,
    )

    assert get_selected_sources("test") == ()


def test_get_selected_sources_ignores_invalid_payload_shape(tmp_path: Path, monkeypatch) -> None:
    selection_file = tmp_path / "source_selection.json"
    selection_file.write_text(
        json.dumps({"schema_version": 1, "selected_sources": "codex"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "syke.source_selection._selection_path",
        lambda _user_id: selection_file,
    )

    assert get_selected_sources("test") == ()


def test_get_selected_sources_fails_closed_when_payload_has_unknown_source(
    tmp_path: Path, monkeypatch
) -> None:
    selection_file = tmp_path / "source_selection.json"
    selection_file.write_text(
        json.dumps({"schema_version": 1, "selected_sources": ["fake-source"]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "syke.source_selection._selection_path",
        lambda _user_id: selection_file,
    )

    assert get_selected_sources("test") == ()
