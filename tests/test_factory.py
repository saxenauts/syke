from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

from syke.db import SykeDB
from syke.observe import factory as factory_module
from syke.observe.bootstrap import BootstrapResult, ensure_adapters
from syke.observe.catalog import active_sources, get_source, iter_discovered_files
from syke.observe.factory import connect_source, discover, get_seed_adapter_path
from syke.observe.registry import HarnessRegistry
from syke.observe.validator import validate_adapter
from tests.observe_artifact_helpers import (
    write_antigravity_workflow,
    write_claude_code_session,
    write_codex_session,
    write_copilot_cli_session,
    write_cursor_state_db,
    write_gemini_cli_session,
    write_hermes_session,
    write_opencode_db,
)


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


def test_seed_adapter_exists_for_all_active_sources() -> None:
    for spec in active_sources():
        path = get_seed_adapter_path(spec.source)
        assert path is not None, f"missing seed for {spec.source}"
        assert path.name == f"{spec.source}.py"


def test_registry_can_load_shipped_seed_without_bootstrap(tmp_path: Path) -> None:
    db = SykeDB(tmp_path / "syke.db", event_db_path=tmp_path / "events.db")
    db.initialize()
    try:
        registry = HarnessRegistry(dynamic_adapters_dir=tmp_path / "missing-adapters")
        adapter = registry.get_adapter("claude-code", db, "test-user")
        assert adapter is not None
        assert adapter.source == "claude-code"
    finally:
        db.close()


def test_discovery_prefers_latest_cursor_root_over_stale_dotdir(tmp_path: Path) -> None:
    stale = tmp_path / ".cursor" / "extensions" / "junk.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")

    live = (
        tmp_path
        / "Library"
        / "Application Support"
        / "Cursor"
        / "User"
        / "workspaceStorage"
        / "ws-1"
        / "state.vscdb"
    )
    write_cursor_state_db(live, "cursor-session-1", [{"user": "hi", "assistant": "hello"}])

    spec = get_source("cursor")
    assert spec is not None
    files = iter_discovered_files(spec, home=tmp_path)

    assert files == [live.resolve()]


def test_discovery_ignores_gemini_settings_when_chat_artifacts_exist(tmp_path: Path) -> None:
    settings_path = tmp_path / ".gemini" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("{}", encoding="utf-8")
    chat_path = write_gemini_cli_session(
        tmp_path,
        "project-hash",
        "gemini-session-1",
        [
            {"type": "user", "content": "hello"},
            {"type": "gemini", "content": "world"},
        ],
    )

    spec = get_source("gemini-cli")
    assert spec is not None
    files = iter_discovered_files(spec, home=tmp_path)

    assert files == [chat_path.resolve()]


def test_seed_validation_passes_for_synthetic_active_sources(tmp_path: Path) -> None:
    claude_path = write_claude_code_session(
        tmp_path,
        "claude-001",
        [
            {"role": "user", "text": "hello"},
            {
                "role": "assistant",
                "text": "world",
                "tools": [
                    {
                        "id": "tool-1",
                        "name": "Read",
                        "input": {"path": "README.md"},
                    }
                ],
            },
            {
                "role": "user",
                "tool_results": [
                    {
                        "tool_use_id": "tool-1",
                        "content": "ok",
                    }
                ],
            },
        ],
    )
    codex_path = write_codex_session(
        tmp_path,
        "codex-001",
        [{"role": "user", "text": "hello"}, {"role": "assistant", "text": "world"}],
    )
    opencode_path = write_opencode_db(
        tmp_path / ".local" / "share" / "opencode" / "opencode-prod.db",
        [
            {
                "id": "opencode-001",
                "turns": [
                    {"role": "user", "text": "hello"},
                    {"role": "assistant", "text": "world"},
                ],
            }
        ],
    )
    cursor_path = write_cursor_state_db(
        tmp_path
        / "Library"
        / "Application Support"
        / "Cursor"
        / "User"
        / "workspaceStorage"
        / "ws-1"
        / "state.vscdb",
        "cursor-001",
        [{"user": "hello", "assistant": "world"}],
    )
    copilot_path = write_copilot_cli_session(
        tmp_path,
        "copilot-001",
        [{"user": "hello", "assistant": "world"}],
    )
    antigravity_dir = write_antigravity_workflow(
        tmp_path,
        "workflow-001",
        task="Build the thing",
        implementation_plan="Implement the thing carefully",
        walkthrough="Here is what happened",
    )
    hermes_path = write_hermes_session(
        tmp_path,
        "20260316_000001_test",
        [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "world"},
        ],
    )
    gemini_path = write_gemini_cli_session(
        tmp_path,
        "gemini-project",
        "gemini-001",
        [
            {"type": "user", "content": "hello"},
            {
                "type": "gemini",
                "content": "world",
                "tool_calls": [
                    {
                        "id": "tool-1",
                        "name": "read_file",
                        "args": {"path": "README.md"},
                        "result": [{"text": "ok"}],
                        "status": "success",
                    }
                ],
            },
        ],
    )

    antigravity_paths = sorted(path for path in antigravity_dir.parent.parent.rglob("*") if path.is_file())
    source_paths = {
        "claude-code": [claude_path],
        "codex": [codex_path],
        "opencode": [opencode_path],
        "cursor": [cursor_path],
        "copilot": [copilot_path],
        "antigravity": antigravity_paths,
        "hermes": [hermes_path, hermes_path.parent.parent / "state.db"],
        "gemini-cli": [gemini_path],
    }

    for source, paths in source_paths.items():
        seed = get_seed_adapter_path(source)
        assert seed is not None
        result = validate_adapter(source, seed, [Path(path) for path in paths])
        assert result.ok is True, f"{source} failed: {result.summary}"


def test_bootstrap_uses_shipped_seed_before_factory(tmp_path: Path) -> None:
    hermes_path = write_hermes_session(
        tmp_path,
        "20260316_000002_test",
        [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "world"},
        ],
    )
    source_paths = [hermes_path.parent.parent / "state.db", hermes_path]
    registry = HarnessRegistry(dynamic_adapters_dir=tmp_path / "adapters")

    with (
        patch("syke.observe.bootstrap.user_data_dir", return_value=tmp_path / "data"),
        patch("syke.observe.bootstrap.iter_discovered_files", return_value=source_paths),
        patch("syke.observe.bootstrap.connect_source", side_effect=AssertionError("factory should not run")),
    ):
        results = ensure_adapters("seed-user", sources=["hermes"], registry=registry)

    assert results == [BootstrapResult("hermes", "generated", "strict validation passed")]
    deployed = tmp_path / "data" / "adapters" / "hermes" / "adapter.py"
    assert deployed.is_file()


def test_connect_source_recovers_if_agent_times_out_after_writing_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_file = tmp_path / "hermes-session.txt"
    source_file.write_text("hello from hermes\n", encoding="utf-8")
    output_path = tmp_path / "workspace" / "adapter.py"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    spec = get_source("hermes")
    assert spec is not None

    monkeypatch.setattr(factory_module, "discovered_roots", lambda spec_arg: [tmp_path])
    monkeypatch.setattr(factory_module, "iter_discovered_files", lambda spec_arg: [source_file])
    monkeypatch.setattr(factory_module, "_factory_output_path", lambda source: output_path)
    monkeypatch.setattr(factory_module, "write_sandbox_config", lambda *args, **kwargs: None)

    adapter_code = textwrap.dedent(
        """
        from __future__ import annotations

        from datetime import UTC, datetime
        from pathlib import Path

        from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn


        class HermesObserveAdapter(ObserveAdapter):
            source = "hermes"

            def __init__(self, db, user_id, data_dir=None):
                super().__init__(db, user_id)
                self.data_dir = Path(data_dir) if data_dir else None

            def discover(self):
                if self.data_dir is None:
                    return []
                if self.data_dir.is_file():
                    return [self.data_dir]
                return sorted(path for path in self.data_dir.rglob("*.txt") if path.is_file())

            def iter_sessions(self, since=0, paths=None):
                candidates = self._normalize_candidate_paths(paths)
                if candidates is None:
                    candidates = self.discover()
                for path in candidates:
                    text = path.read_text(encoding="utf-8").strip()
                    if not text:
                        continue
                    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
                    yield ObservedSession(
                        session_id=path.stem,
                        source_path=path,
                        start_time=timestamp,
                        turns=[
                            ObservedTurn(
                                role="user",
                                content=text,
                                timestamp=timestamp,
                            )
                        ],
                    )
        """
    ).strip() + "\n"

    def fake_llm(prompt: str) -> str:
        _ = prompt
        output_path.write_text(adapter_code, encoding="utf-8")
        raise RuntimeError("Pi did not complete within 600.0s")

    ok, message = connect_source(spec, adapters_dir=tmp_path / "adapters", llm_fn=fake_llm)

    assert ok is True
    assert "strict validation passed" in message
    assert "recovered after agent error" in message
    assert (tmp_path / "adapters" / "hermes" / "adapter.py").is_file()
