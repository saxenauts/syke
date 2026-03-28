"""Tests for sense factory — discover, generate, test, heal, connect, deploy."""

from __future__ import annotations

import json
import sqlite3
import textwrap
from datetime import UTC, datetime
from pathlib import Path

from syke.observe.factory import (
    connect,
    check_parse,
    check_parse_jsonl_adapter,
    check_parse_sqlite,
    deploy,
    discover,
    generate,
    generate_jsonl_adapter,
    generate_sqlite,
    _template_fallback,
    _read_samples,
    _supports_paths_scoped_iter_sessions,
    heal,
)


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


def test_discover_finds_known_harnesses(tmp_path):
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    (tmp_path / ".claude" / "sessions" / "test.jsonl").write_text('{"x":1}\n')
    results = discover(home=tmp_path)
    assert any(r["source"] == "claude-code" for r in results)


def test_discover_empty_home(tmp_path):
    assert discover(home=tmp_path) == []


def test_discover_ignores_unknown_dirs(tmp_path):
    (tmp_path / ".random-tool").mkdir()
    results = discover(home=tmp_path)
    assert not any(r["source"] == "random-tool" for r in results)


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def test_generate_template_fallback():
    samples = [json.dumps({"timestamp": "2026-01-01", "role": "user", "content": "hi"})]
    code = generate("test", samples, llm_fn=None)
    assert code is not None
    assert "parse_line" in code
    assert "json" in code


def test_generate_with_llm_fn():
    def fake_llm(prompt):
        return "```python\nimport json\ndef parse_line(line):\n    return json.loads(line)\n```"

    code = generate("test", ["{}"], llm_fn=fake_llm)
    assert code is not None
    assert "parse_line" in code
    assert "```" not in code  # fencing stripped


def test_generate_llm_exception_falls_back():
    def broken_llm(prompt):
        raise RuntimeError("API down")

    code = generate("test", ["{}"], llm_fn=broken_llm)
    assert code is not None  # should fallback to template
    assert "parse_line" in code


# ---------------------------------------------------------------------------
# test_parse
# ---------------------------------------------------------------------------


def test_check_parse_valid_code():
    code = textwrap.dedent("""\
        import json
        def parse_line(line):
            d = json.loads(line)
            return {
                "session_id": d.get("session_id"),
                "role": d.get("role"),
                "event_type": "turn",
                "content": d.get("content"),
                "timestamp": d.get("timestamp"),
            }
    """)
    samples = [
        json.dumps({"session_id": "s1", "role": "user", "content": "hi", "timestamp": "2026-01-01"}),
        json.dumps({"session_id": "s1", "role": "assistant", "content": "hello", "timestamp": "2026-01-01"}),
    ]
    ok, n, coverage = check_parse(code, samples)
    assert ok
    assert n == 2
    assert coverage["session_id"] == 1.0
    assert coverage["role"] == 1.0
    assert coverage["event_type"] == 1.0


def test_check_parse_broken_code():
    code = "def parse_line(line): raise Exception('boom')"
    ok, n, coverage = check_parse(code, ["{}"])
    assert not ok
    assert n == 0


def test_check_parse_syntax_error():
    code = "def parse_line(line:\n    return None"
    ok, n, _cov = check_parse(code, ["{}"])
    assert not ok


def test_check_parse_returns_none():
    code = "def parse_line(line): return None"
    ok, n, _cov = check_parse(code, ["{}"])
    assert not ok
    assert n == 0


def test_check_parse_empty_samples():
    code = 'import json\ndef parse_line(line): return json.loads(line)'
    ok, n, _cov = check_parse(code, [])
    assert not ok  # no events parsed = failure


def test_check_parse_fails_without_session_id():
    """Adapter that returns dicts but without session_id should fail the quality gate."""
    code = textwrap.dedent("""\
        import json
        def parse_line(line):
            return {"event_type": "turn", "content": "x", "role": "user"}
    """)
    samples = ['{"a": 1}', '{"b": 2}']
    ok, n, coverage = check_parse(code, samples)
    assert not ok  # session_id coverage is 0%, below 50% gate
    assert n == 2
    assert coverage["session_id"] == 0.0


def test_check_parse_coverage_reported():
    """Coverage dict should report all canonical fields."""
    code = textwrap.dedent("""\
        import json
        def parse_line(line):
            return {"session_id": "s", "role": "user", "event_type": "turn",
                    "content": "x", "timestamp": "t", "model": "gpt-4"}
    """)
    ok, n, coverage = check_parse(code, ['{"a": 1}'])
    assert ok
    assert coverage["model"] == 1.0
    assert coverage["input_tokens"] == 0.0  # not filled, but not gated


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


def test_deploy_writes_file(tmp_path):
    code = "import json\ndef parse_line(line): return json.loads(line)\n"
    ok = deploy("test-source", code, tmp_path)
    assert ok
    assert (tmp_path / "test-source" / "adapter.py").read_text() == code


def test_deploy_creates_dirs(tmp_path):
    deep = tmp_path / "a" / "b"
    ok = deploy("src", "pass", deep)
    assert ok
    assert (deep / "src" / "adapter.py").is_file()


# ---------------------------------------------------------------------------
# heal
# ---------------------------------------------------------------------------


def test_heal_with_template(tmp_path):
    samples = [json.dumps({"timestamp": "2026-01-01", "role": "user", "content": "hi",
                           "session_id": "s1", "event_type": "turn"})]
    ok = heal("test", samples, adapters_dir=tmp_path)
    assert ok
    assert (tmp_path / "test" / "adapter.py").exists()


def test_heal_no_adapters_dir():
    samples = [json.dumps({"timestamp": "2026-01-01", "role": "user", "content": "hi",
                           "session_id": "s1", "event_type": "turn"})]
    ok = heal("test", samples, adapters_dir=None)
    assert ok  # test passes, just not deployed


def test_heal_bad_samples():
    ok = heal("test", ["not json", "also not json"])
    # Template fallback returns None for non-json, test_parse may fail
    # This is fine — heal returns False when test fails
    assert isinstance(ok, bool)


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


def test_connect_missing_path(tmp_path):
    ok, msg = connect(tmp_path / "nonexistent")
    assert not ok
    assert "not found" in msg


def test_connect_empty_dir(tmp_path):
    ok, msg = connect(tmp_path)
    assert not ok
    assert "No data" in msg


def test_connect_with_data(tmp_path):
    data = tmp_path / ".test-harness"
    data.mkdir()
    f = data / "sessions.jsonl"
    lines = [json.dumps({"timestamp": "2026-01-01", "role": "user", "content": f"msg {i}",
                         "session_id": "s1", "event_type": "turn"}) for i in range(5)]
    f.write_text("\n".join(lines) + "\n")

    ok, msg = connect(data, adapters_dir=tmp_path / "adapters")
    assert ok
    assert "5 events" in msg


# ---------------------------------------------------------------------------
# _read_samples
# ---------------------------------------------------------------------------


def test_read_samples_jsonl(tmp_path):
    f = tmp_path / "data.jsonl"
    f.write_text('{"a":1}\n{"b":2}\n')
    samples = _read_samples(tmp_path)
    assert len(samples) == 2


def test_read_samples_max_lines(tmp_path):
    f = tmp_path / "data.jsonl"
    f.write_text("\n".join(f'{{"n":{i}}}' for i in range(100)) + "\n")
    samples = _read_samples(tmp_path, max_lines=10)
    assert len(samples) == 10


def test_read_samples_binary_file(tmp_path):
    f = tmp_path / "data.jsonl"
    f.write_bytes(b'\x00\x01\x02\xff\xfe')
    samples = _read_samples(tmp_path)
    # Should not crash on binary
    assert isinstance(samples, list)


def test_read_samples_redacts_sensitive_values(tmp_path):
    f = tmp_path / "data.jsonl"
    f.write_text(
        json.dumps(
            {
                "type": "message",
                "role": "user",
                "content": "very secret code block\nline 2",
                "url": "https://example.com/private",
                "path": "/Users/test/private/file.txt",
                "api_key": "sk-123",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    samples = _read_samples(tmp_path)

    assert len(samples) == 1
    sample = samples[0]
    assert "very secret code block" not in sample
    assert "https://example.com/private" not in sample
    assert "/Users/test/private/file.txt" not in sample
    assert "sk-123" not in sample


# ---------------------------------------------------------------------------
# template_fallback
# ---------------------------------------------------------------------------


def test_template_fallback_produces_valid_code():
    code = _template_fallback([])
    assert "parse_line" in code
    samples = [json.dumps({
        "timestamp": "2026-01-01", "role": "user", "content": "hi",
        "session_id": "s1", "event_type": "turn",
    })]
    ok, _, coverage = check_parse(code, samples)
    assert ok
    assert coverage["session_id"] == 1.0


# ---------------------------------------------------------------------------
# generated adapter contract
# ---------------------------------------------------------------------------


def _write_generation_jsonl_fixture(tmp_path: Path) -> Path:
    data_dir = tmp_path / "jsonl-source"
    data_dir.mkdir()
    (data_dir / "session-1.jsonl").write_text(
        "\n".join(
            [
                json.dumps(record)
                for record in [
                    {
                        "timestamp": "2026-03-28T10:00:00Z",
                        "role": "user",
                        "content": "hello",
                        "model": "gpt-test",
                        "input_tokens": 10,
                        "output_tokens": 0,
                    },
                    {
                        "timestamp": "2026-03-28T10:00:01Z",
                        "role": "assistant",
                        "content": "hi",
                        "model": "gpt-test",
                        "input_tokens": 10,
                        "output_tokens": 12,
                    },
                    {
                        "timestamp": "2026-03-28T10:00:02Z",
                        "role": "user",
                        "content": "question",
                        "model": "gpt-test",
                        "input_tokens": 8,
                        "output_tokens": 0,
                    },
                    {
                        "timestamp": "2026-03-28T10:00:03Z",
                        "role": "assistant",
                        "content": "answer",
                        "model": "gpt-test",
                        "input_tokens": 8,
                        "output_tokens": 9,
                    },
                    {
                        "timestamp": "2026-03-28T10:00:04Z",
                        "role": "user",
                        "content": "thanks",
                        "model": "gpt-test",
                        "input_tokens": 6,
                        "output_tokens": 0,
                    },
                    {
                        "timestamp": "2026-03-28T10:00:05Z",
                        "role": "assistant",
                        "content": "done",
                        "model": "gpt-test",
                        "input_tokens": 6,
                        "output_tokens": 7,
                    },
                ]
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return data_dir


def _write_generation_sqlite_fixture(tmp_path: Path) -> Path:
    db_path = tmp_path / "source.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                started_at REAL NOT NULL
            );
            CREATE TABLE turns (
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ts REAL NOT NULL,
                model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER
            );
            INSERT INTO sessions (id, started_at) VALUES ('s1', 1711610400.0);
            INSERT INTO turns (session_id, role, content, ts, model, input_tokens, output_tokens)
            VALUES
                ('s1', 'user', 'hello', 1711610400.0, 'gpt-test', 10, 0),
                ('s1', 'assistant', 'hi', 1711610401.0, 'gpt-test', 10, 12),
                ('s1', 'user', 'question', 1711610402.0, 'gpt-test', 8, 0),
                ('s1', 'assistant', 'answer', 1711610403.0, 'gpt-test', 8, 9),
                ('s1', 'user', 'thanks', 1711610404.0, 'gpt-test', 6, 0),
                ('s1', 'assistant', 'done', 1711610405.0, 'gpt-test', 6, 7);
            """
        )
    return db_path


_JSONL_ADAPTER_WITH_PATHS = textwrap.dedent("""\
    import json
    from collections.abc import Iterable
    from datetime import datetime
    from pathlib import Path
    from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn

    class GeneratedJsonlAdapter(ObserveAdapter):
        source = "test-jsonl"

        def __init__(self, db, user_id, data_dir=None):
            super().__init__(db, user_id)
            self.data_dir = Path(data_dir) if data_dir is not None else Path(".")

        def discover(self) -> list[Path]:
            return sorted(self.data_dir.glob("*.jsonl"))

        def _ts(self, value: str) -> datetime:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))

        def iter_sessions(self, since=0, paths=None):
            explicit_paths = self._normalize_candidate_paths(paths)
            candidates = explicit_paths if explicit_paths is not None else self.discover()
            for path in candidates:
                if explicit_paths is None and since and path.stat().st_mtime < since:
                    continue
                turns = []
                start_time = None
                with path.open(encoding="utf-8") as handle:
                    for raw in handle:
                        data = json.loads(raw)
                        ts = self._ts(data["timestamp"])
                        if start_time is None:
                            start_time = ts
                        turns.append(
                            ObservedTurn(
                                role=data["role"],
                                content=data["content"],
                                timestamp=ts,
                                metadata={
                                    "model": data.get("model"),
                                    "usage": {
                                        "input_tokens": data.get("input_tokens"),
                                        "output_tokens": data.get("output_tokens"),
                                    },
                                },
                            )
                        )
                if turns:
                    yield ObservedSession(
                        session_id=path.stem,
                        source_path=str(path),
                        start_time=start_time,
                        end_time=turns[-1].timestamp,
                        turns=turns,
                        metadata={},
                    )
""")


_JSONL_ADAPTER_OLD_SIGNATURE = _JSONL_ADAPTER_WITH_PATHS.replace(
    "def iter_sessions(self, since=0, paths=None):",
    "def iter_sessions(self, since=0):",
)


_JSONL_ADAPTER_IGNORES_PATHS = textwrap.dedent("""\
    import json
    from datetime import datetime
    from pathlib import Path
    from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn

    class GeneratedJsonlAdapter(ObserveAdapter):
        source = "test-jsonl"

        def __init__(self, db, user_id, data_dir=None):
            super().__init__(db, user_id)
            self.data_dir = Path(data_dir) if data_dir is not None else Path(".")

        def discover(self) -> list[Path]:
            return sorted(self.data_dir.glob("*.jsonl"))

        def _ts(self, value: str) -> datetime:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))

        def iter_sessions(self, since=0, paths=None):
            for path in self.discover():
                turns = []
                start_time = None
                with path.open(encoding="utf-8") as handle:
                    for raw in handle:
                        data = json.loads(raw)
                        ts = self._ts(data["timestamp"])
                        if start_time is None:
                            start_time = ts
                        turns.append(
                            ObservedTurn(
                                role=data["role"],
                                content=data["content"],
                                timestamp=ts,
                                metadata={
                                    "model": data.get("model"),
                                    "usage": {
                                        "input_tokens": data.get("input_tokens"),
                                        "output_tokens": data.get("output_tokens"),
                                    },
                                },
                            )
                        )
                if turns:
                    yield ObservedSession(
                        session_id=path.stem,
                        source_path=str(path),
                        start_time=start_time,
                        end_time=turns[-1].timestamp,
                        turns=turns,
                        metadata={},
                    )
""")


_SQLITE_ADAPTER_WITH_PATHS = textwrap.dedent("""\
    import sqlite3
    from datetime import UTC, datetime
    from pathlib import Path
    from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn

    class GeneratedSqliteAdapter(ObserveAdapter):
        source = "test-sqlite"

        def __init__(self, db, user_id, source_db_path=None):
            super().__init__(db, user_id)
            self.source_db_path = Path(source_db_path) if source_db_path is not None else Path("source.db")

        def discover(self) -> list[Path]:
            return [self.source_db_path] if self.source_db_path.exists() else []

        def iter_sessions(self, since=0, paths=None):
            explicit_paths = self._normalize_candidate_paths(paths)
            resolved_path = self.source_db_path.expanduser().resolve()
            if not resolved_path.exists():
                return
            if explicit_paths is not None and resolved_path not in explicit_paths:
                return

            conn = sqlite3.connect(f"file:{resolved_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                for session in conn.execute(
                    "SELECT id, started_at FROM sessions WHERE started_at > ? ORDER BY started_at",
                    (since,),
                ):
                    turns = []
                    for row in conn.execute(
                        "SELECT role, content, ts, model, input_tokens, output_tokens FROM turns "
                        "WHERE session_id = ? ORDER BY ts",
                        (session["id"],),
                    ):
                        turns.append(
                            ObservedTurn(
                                role=row["role"],
                                content=row["content"],
                                timestamp=datetime.fromtimestamp(row["ts"], tz=UTC),
                                metadata={
                                    "model": row["model"],
                                    "usage": {
                                        "input_tokens": row["input_tokens"],
                                        "output_tokens": row["output_tokens"],
                                    },
                                },
                            )
                        )
                    if turns:
                        yield ObservedSession(
                            session_id=session["id"],
                            source_path=str(resolved_path),
                            start_time=datetime.fromtimestamp(session["started_at"], tz=UTC),
                            end_time=turns[-1].timestamp,
                            turns=turns,
                            metadata={},
                        )
            finally:
                conn.close()
""")


_SQLITE_ADAPTER_OLD_SIGNATURE = _SQLITE_ADAPTER_WITH_PATHS.replace(
    "def iter_sessions(self, since=0, paths=None):",
    "def iter_sessions(self, since=0):",
)


_SQLITE_ADAPTER_IGNORES_PATHS = textwrap.dedent("""\
    import sqlite3
    from datetime import UTC, datetime
    from pathlib import Path
    from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn

    class GeneratedSqliteAdapter(ObserveAdapter):
        source = "test-sqlite"

        def __init__(self, db, user_id, source_db_path=None):
            super().__init__(db, user_id)
            self.source_db_path = Path(source_db_path) if source_db_path is not None else Path("source.db")

        def discover(self) -> list[Path]:
            return [self.source_db_path] if self.source_db_path.exists() else []

        def iter_sessions(self, since=0, paths=None):
            resolved_path = self.source_db_path.expanduser().resolve()
            if not resolved_path.exists():
                return

            conn = sqlite3.connect(f"file:{resolved_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                for session in conn.execute(
                    "SELECT id, started_at FROM sessions WHERE started_at > ? ORDER BY started_at",
                    (since,),
                ):
                    turns = []
                    for row in conn.execute(
                        "SELECT role, content, ts, model, input_tokens, output_tokens FROM turns "
                        "WHERE session_id = ? ORDER BY ts",
                        (session["id"],),
                    ):
                        turns.append(
                            ObservedTurn(
                                role=row["role"],
                                content=row["content"],
                                timestamp=datetime.fromtimestamp(row["ts"], tz=UTC),
                                metadata={
                                    "model": row["model"],
                                    "usage": {
                                        "input_tokens": row["input_tokens"],
                                        "output_tokens": row["output_tokens"],
                                    },
                                },
                            )
                        )
                    if turns:
                        yield ObservedSession(
                            session_id=session["id"],
                            source_path=str(resolved_path),
                            start_time=datetime.fromtimestamp(session["started_at"], tz=UTC),
                            end_time=turns[-1].timestamp,
                            turns=turns,
                            metadata={},
                        )
            finally:
                conn.close()
""")


def test_supports_paths_scoped_iter_sessions_detects_contract():
    assert _supports_paths_scoped_iter_sessions(_JSONL_ADAPTER_WITH_PATHS)
    assert not _supports_paths_scoped_iter_sessions(_JSONL_ADAPTER_OLD_SIGNATURE)


def test_check_parse_jsonl_adapter_accepts_current_paths_contract(tmp_path):
    data_dir = _write_generation_jsonl_fixture(tmp_path)
    ok, total, coverage = check_parse_jsonl_adapter(_JSONL_ADAPTER_WITH_PATHS, str(data_dir))
    assert ok
    assert total > 0
    assert coverage["session_id"] > 0


def test_check_parse_jsonl_adapter_rejects_old_iter_sessions_signature(tmp_path):
    data_dir = _write_generation_jsonl_fixture(tmp_path)
    ok, total, _coverage = check_parse_jsonl_adapter(_JSONL_ADAPTER_OLD_SIGNATURE, str(data_dir))
    assert not ok
    assert total == 0


def test_check_parse_jsonl_adapter_rejects_ignored_paths_scope(tmp_path):
    data_dir = _write_generation_jsonl_fixture(tmp_path)
    ok, total, _coverage = check_parse_jsonl_adapter(_JSONL_ADAPTER_IGNORES_PATHS, str(data_dir))
    assert not ok
    assert total == 0


def test_check_parse_sqlite_accepts_current_paths_contract(tmp_path):
    db_path = _write_generation_sqlite_fixture(tmp_path)
    ok, total, coverage = check_parse_sqlite(_SQLITE_ADAPTER_WITH_PATHS, str(db_path))
    assert ok
    assert total > 0
    assert coverage["session_id"] > 0


def test_check_parse_sqlite_rejects_old_iter_sessions_signature(tmp_path):
    db_path = _write_generation_sqlite_fixture(tmp_path)
    ok, total, _coverage = check_parse_sqlite(_SQLITE_ADAPTER_OLD_SIGNATURE, str(db_path))
    assert not ok
    assert total == 0


def test_check_parse_sqlite_rejects_ignored_paths_scope(tmp_path):
    db_path = _write_generation_sqlite_fixture(tmp_path)
    ok, total, _coverage = check_parse_sqlite(_SQLITE_ADAPTER_IGNORES_PATHS, str(db_path))
    assert not ok
    assert total == 0
