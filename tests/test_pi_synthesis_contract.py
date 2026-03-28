from __future__ import annotations

from pathlib import Path

from syke.llm.backends import pi_synthesis
from syke.memory.memex import update_memex


def test_sync_memex_prefers_canonical_db_over_stale_artifact(
    db,
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)

    update_memex(db, user_id, "prior memex")
    update_memex(db, user_id, "canonical db memex")
    memex_path.write_text("stale artifact memex\n", encoding="utf-8")

    result = pi_synthesis._sync_memex_to_db(
        db,
        user_id,
        previous_content="prior memex",
        previous_artifact_content="stale artifact memex",
    )

    assert result == {
        "ok": True,
        "updated": True,
        "source": "db",
        "artifact_written": True,
    }
    assert db.get_memex(user_id)["content"] == "canonical db memex"
    assert memex_path.read_text(encoding="utf-8") == "canonical db memex\n"


def test_sync_memex_imports_artifact_when_db_did_not_change(
    db,
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)

    update_memex(db, user_id, "prior memex")
    memex_path.write_text("artifact memex\n", encoding="utf-8")

    result = pi_synthesis._sync_memex_to_db(
        db,
        user_id,
        previous_content="prior memex",
        previous_artifact_content=None,
    )

    assert result == {
        "ok": True,
        "updated": True,
        "source": "artifact",
        "artifact_written": False,
    }
    assert db.get_memex(user_id)["content"] == "artifact memex"
    assert memex_path.read_text(encoding="utf-8") == "artifact memex\n"


def test_sync_memex_projects_existing_canonical_memex_without_artifact(
    db,
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)

    update_memex(db, user_id, "canonical memex")

    result = pi_synthesis._sync_memex_to_db(
        db,
        user_id,
        previous_content="canonical memex",
        previous_artifact_content=None,
    )

    assert result == {
        "ok": True,
        "updated": False,
        "source": "db",
        "artifact_written": True,
    }
    assert memex_path.read_text(encoding="utf-8") == "canonical memex\n"


def test_sync_memex_does_not_import_stale_artifact_when_nothing_changed(
    db,
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)

    update_memex(db, user_id, "canonical memex")
    memex_path.write_text("stale artifact memex\n", encoding="utf-8")

    result = pi_synthesis._sync_memex_to_db(
        db,
        user_id,
        previous_content="canonical memex",
        previous_artifact_content="stale artifact memex",
    )

    assert result == {
        "ok": True,
        "updated": False,
        "source": "db",
        "artifact_written": True,
    }
    assert db.get_memex(user_id)["content"] == "canonical memex"
    assert memex_path.read_text(encoding="utf-8") == "canonical memex\n"
