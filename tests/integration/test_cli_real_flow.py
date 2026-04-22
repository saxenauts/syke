from __future__ import annotations

import json

from syke.cli_support.context import get_db
from syke.entrypoint import cli


def test_record_then_status_reflects_real_db_state(cli_runner) -> None:
    record = cli_runner.invoke(cli, ["--user", "test", "record", "Ship release notes"])
    assert record.exit_code == 0

    status = cli_runner.invoke(cli, ["--user", "test", "status", "--json"])
    assert status.exit_code == 0
    payload = json.loads(status.output)

    assert payload["initialized"] is True
    assert payload["memex"]["present"] is False
    assert payload["memex"]["memory_count"] == 1


def test_memex_fallback_reports_memory_count_without_memex(cli_runner) -> None:
    result = cli_runner.invoke(cli, ["--user", "test", "record", "Remember this thread"])
    assert result.exit_code == 0

    memex = cli_runner.invoke(cli, ["--user", "test", "memex", "--json"])
    assert memex.exit_code == 0
    payload = json.loads(memex.output)

    assert payload["user"] == "test"
    assert (
        "[No memex yet. 1 memories are available in Syke's canonical database.]" in payload["memex"]
    )


def test_connect_installs_adapter_markdowns_in_workspace(cli_runner) -> None:
    from syke.runtime import workspace

    result = cli_runner.invoke(cli, ["--user", "test", "connect"])
    assert result.exit_code == 0

    adapters_dir = workspace.WORKSPACE_ROOT / "adapters"
    assert adapters_dir.exists()
    assert any(adapters_dir.glob("*.md"))

    db = get_db("test")
    try:
        assert db.count_memories("test") == 0
    finally:
        db.close()
