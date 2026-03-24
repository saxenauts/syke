"""Sense factory — discover harnesses, generate adapters, heal broken ones.

Dumb orchestration. The skill files carry all intelligence.
This module is just plumbing: read samples, call LLM, test output, write to disk.
Handles two adapter shapes: parse_line() for JSONL, ObserveAdapter subclass for SQLite.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Callable
from pathlib import Path
from string import Template

logger = logging.getLogger(__name__)

_SKILL_PATH = Path(__file__).parent / "skills" / "generate_adapter.md"
_SQLITE_SKILL_PATH = Path(__file__).parent / "skills" / "generate_sqlite_adapter.md"
_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)

# Known harness directories → source names
KNOWN_HARNESSES: dict[str, str] = {
    ".claude": "claude-code",
    ".codex": "codex",
    ".cursor": "cursor",
    ".hermes": "hermes",
    ".continue": "continue",
    ".gemini": "gemini",
    ".local/share/opencode": "opencode",
    ".pi/agent": "pi",
}


# ---------------------------------------------------------------------------
# Discover
# ---------------------------------------------------------------------------


def discover(home: Path | None = None) -> list[dict]:
    """Scan filesystem for known AI harness installations.

    Returns list of {"source": str, "path": Path, "format": str}.
    """
    home = home or Path.home()
    results: list[dict] = []
    for dirname, source in KNOWN_HARNESSES.items():
        path = home / dirname
        if path.exists():
            fmt = _guess_format(path)
            results.append({"source": source, "path": path, "format": fmt})
    return results


# ---------------------------------------------------------------------------
# Generate adapter code
# ---------------------------------------------------------------------------


def generate(source_name: str, samples: list[str], llm_fn: Callable | None = None) -> str | None:
    """Ask LLM to write a parse_line() function. Returns code string or None."""
    if not llm_fn:
        return _template_fallback(samples)

    try:
        skill = _SKILL_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Skill file missing at %s", _SKILL_PATH)
        return _template_fallback(samples)

    # Use Template ($ syntax) so JSON { } in samples don't crash
    prompt = Template(skill).safe_substitute(
        source_name=source_name,
        samples="\n".join(samples[:10]),
    )

    try:
        raw = llm_fn(prompt)
        code = _strip_fencing(raw)
        return code if code else None
    except Exception:
        logger.warning("LLM generation failed for %s", source_name, exc_info=True)
        return _template_fallback(samples)


def _template_fallback(samples: list[str]) -> str:
    """Dead-simple fallback when no LLM is available."""
    return textwrap.dedent("""\
        import json

        def parse_line(line):
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    return None
                usage = data.get("usage") or {}
                if not isinstance(usage, dict):
                    usage = {}
                return {
                    "timestamp": data.get("timestamp") or data.get("created_at") or data.get("ts"),
                    "session_id": data.get("session_id") or data.get("sessionId") or data.get("session"),
                    "role": data.get("role") or data.get("type"),
                    "content": data.get("content") or data.get("message") or data.get("text"),
                    "event_type": data.get("event_type") or "turn",
                    "model": data.get("model"),
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "tool_name": data.get("tool_name") or data.get("name"),
                }
            except (json.JSONDecodeError, ValueError, AttributeError):
                return None
    """)


def _strip_fencing(text: str) -> str:
    # Try markdown fences first
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    # No fences — try to extract from 'def parse_line' onwards
    idx = text.find("def parse_line")
    if idx >= 0:
        # Include any imports above the function
        prefix = text[:idx]
        last_import = max(prefix.rfind("\nimport "), prefix.rfind("\nfrom "))
        start = last_import + 1 if last_import >= 0 else idx
        return text[start:].strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Test generated code
# ---------------------------------------------------------------------------


_COVERAGE_FIELDS = (
    "session_id", "role", "event_type", "content", "timestamp",
    "model", "input_tokens", "output_tokens", "tool_name",
)

# Minimum field coverage to pass the quality gate
_COVERAGE_GATES = {
    "session_id": 0.5,
    "role": 0.3,
    "event_type": 0.9,
}


def check_parse(
    code: str, samples: list[str], timeout: int = 15,
) -> tuple[bool, int, dict[str, float]]:
    """Run parse_line() on samples in a subprocess.

    Returns (success, events_parsed, field_coverage).
    field_coverage maps each canonical field to the fraction of events that filled it.
    """
    empty_coverage: dict[str, float] = {f: 0.0 for f in _COVERAGE_FIELDS}

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "adapter.py").write_text(code)
        (td_path / "samples.txt").write_text("\n".join(samples))

        runner = textwrap.dedent(f"""\
            import json, sys
            sys.path.insert(0, {td!r})
            from adapter import parse_line
            fields = {list(_COVERAGE_FIELDS)!r}
            counts = {{f: 0 for f in fields}}
            total = 0
            for line in open({str(td_path / 'samples.txt')!r}):
                line = line.strip()
                if not line:
                    continue
                try:
                    result = parse_line(line)
                    if isinstance(result, dict):
                        total += 1
                        for f in fields:
                            if result.get(f) is not None:
                                counts[f] += 1
                except Exception:
                    pass
            coverage = {{f: counts[f] / total if total > 0 else 0.0 for f in fields}}
            print(json.dumps({{"total": total, "coverage": coverage}}))
        """)
        (td_path / "run.py").write_text(runner)

        try:
            proc = subprocess.run(
                [sys.executable, str(td_path / "run.py")],
                capture_output=True, text=True, timeout=timeout,
            )
            if proc.returncode != 0:
                return False, 0, empty_coverage
            result = json.loads(proc.stdout.strip())
            total = result["total"]
            coverage = result["coverage"]
            if total == 0:
                return False, 0, empty_coverage
            # Quality gate: check minimum field coverage
            for field, threshold in _COVERAGE_GATES.items():
                if coverage.get(field, 0.0) < threshold:
                    return False, total, coverage
            return True, total, coverage
        except (subprocess.TimeoutExpired, ValueError, json.JSONDecodeError, KeyError):
            return False, 0, empty_coverage


# ---------------------------------------------------------------------------
# Deploy + Heal
# ---------------------------------------------------------------------------


def deploy(source_name: str, code: str, adapters_dir: Path) -> bool:
    """Write adapter code to disk."""
    target = adapters_dir / source_name
    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / "adapter.py").write_text(code, encoding="utf-8")
        logger.info("Deployed adapter for %s to %s", source_name, target)
        return True
    except OSError:
        logger.warning("Failed to deploy adapter for %s", source_name, exc_info=True)
        return False


def heal(source: str, samples: list[str], llm_fn: Callable | None = None, adapters_dir: Path | None = None) -> bool:
    """Generate a new adapter from failure samples and deploy it."""
    code = generate(source, samples, llm_fn=llm_fn)
    if code is None:
        return False

    ok, n, coverage = check_parse(code, samples)
    if not ok:
        logger.warning("Generated adapter for %s failed (parsed %d, coverage=%s)", source, n, coverage)
        return False

    logger.info("Adapter for %s: %d events, coverage=%s", source, n, coverage)
    if adapters_dir:
        return deploy(source, code, adapters_dir)
    return True


def connect(path: Path | str, llm_fn: Callable | None = None, adapters_dir: Path | None = None) -> tuple[bool, str]:
    """Connect a new harness: read samples → generate → test → deploy.

    Auto-detects format (JSONL vs SQLite) and uses the appropriate generation path.
    Returns (success, message).
    """
    path = Path(path)
    if not path.exists():
        return False, f"Path not found: {path}"

    # Look up known harness name, fall back to directory name
    source_name = None
    resolved = path.resolve()
    home = Path.home()
    for dirname, name in KNOWN_HARNESSES.items():
        if resolved == (home / dirname).resolve():
            source_name = name
            break
    if source_name is None:
        source_name = path.name.lstrip(".") or "unknown"

    fmt = _guess_format(path)

    if fmt == "sqlite":
        return _connect_sqlite(source_name, path, llm_fn, adapters_dir)

    # JSONL / JSON path
    samples = _read_samples(path)
    if not samples:
        return False, "No data found to analyze"

    code = generate(source_name, samples, llm_fn=llm_fn)
    if code is None:
        return False, "Code generation failed"

    ok, n, coverage = check_parse(code, samples)
    if not ok:
        return False, f"Generated adapter failed test (parsed {n} events, coverage={coverage})"

    cov_summary = ", ".join(f"{k}={v:.0%}" for k, v in coverage.items() if v > 0)
    if adapters_dir:
        deployed = deploy(source_name, code, adapters_dir)
        status = " (deployed)" if deployed else " (deploy failed)"
        return True, f"Adapter generated: {n} events [{cov_summary}]{status}"

    return True, f"Adapter generated: {n} events [{cov_summary}] (not deployed)"


def _connect_sqlite(
    source_name: str, path: Path, llm_fn: Callable | None, adapters_dir: Path | None,
) -> tuple[bool, str]:
    """Connect a SQLite harness: read schema → generate ObserveAdapter → test → deploy."""
    db_path_str, schema_samples = _read_sqlite_samples(path)
    if not db_path_str:
        return False, "No SQLite database found"

    code = generate_sqlite(source_name, schema_samples, llm_fn=llm_fn)
    if code is None:
        return False, "SQLite adapter generation failed (requires LLM)"

    ok, n, coverage = check_parse_sqlite(code, db_path_str)
    if not ok:
        return False, f"Generated SQLite adapter failed test (parsed {n} events, coverage={coverage})"

    cov_summary = ", ".join(f"{k}={v:.0%}" for k, v in coverage.items() if v > 0)
    if adapters_dir:
        deployed = deploy(source_name, code, adapters_dir)
        status = " (deployed)" if deployed else " (deploy failed)"
        return True, f"SQLite adapter generated: {n} events [{cov_summary}]{status}"

    return True, f"SQLite adapter generated: {n} events [{cov_summary}] (not deployed)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_samples(path: Path, max_lines: int = 50) -> list[str]:
    samples: list[str] = []
    for ext in ("*.jsonl", "*.json"):
        for f in path.rglob(ext):
            try:
                for line in f.open():
                    line = line.strip()
                    if line:
                        samples.append(line)
                        if len(samples) >= max_lines:
                            return samples
            except (OSError, UnicodeDecodeError):
                continue
    return samples


def _read_sqlite_samples(path: Path, max_rows: int = 10) -> tuple[str | None, str]:
    """Read schema + sample rows from a SQLite database.

    Returns (db_path_str, schema_and_samples_text).
    If no DB found, returns (None, "").
    """
    db_files = list(path.rglob("*.db")) + list(path.rglob("*.sqlite"))
    if not db_files:
        return None, ""

    db_path = db_files[0]
    lines: list[str] = [f"Database: {db_path.name}", ""]

    try:
        conn = sqlite3.connect(str(db_path))
        # Schema
        tables = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        lines.append("## Schema")
        for name, sql in tables:
            lines.append(f"\n{sql};")

        # Sample rows per table (skip internal/migration tables)
        lines.append("\n## Sample rows")
        for name, _ in tables:
            if name.endswith("_fts") or name.endswith("_fts_data") or "migration" in name.lower():
                continue
            try:
                cols = [desc[0] for desc in conn.execute(f"SELECT * FROM [{name}] LIMIT 0").description]
                rows = conn.execute(f"SELECT * FROM [{name}] ORDER BY rowid DESC LIMIT {max_rows}").fetchall()
                if rows:
                    lines.append(f"\n### {name} ({len(rows)} rows, columns: {', '.join(cols)})")
                    for row in rows[:5]:
                        row_dict = dict(zip(cols, row))
                        # Truncate long values
                        for k, v in row_dict.items():
                            if isinstance(v, str) and len(v) > 200:
                                row_dict[k] = v[:200] + "..."
                        lines.append(json.dumps(row_dict, default=str, ensure_ascii=False))
            except sqlite3.OperationalError:
                continue
        conn.close()
    except sqlite3.Error as e:
        return None, f"SQLite error: {e}"

    return str(db_path), "\n".join(lines)


def generate_sqlite(
    source_name: str, schema_samples: str, llm_fn: Callable | None = None,
) -> str | None:
    """Ask LLM to write an ObserveAdapter subclass for a SQLite harness."""
    if not llm_fn:
        logger.warning("No LLM available for SQLite adapter generation (no template fallback)")
        return None

    try:
        skill = _SQLITE_SKILL_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("SQLite skill file missing at %s", _SQLITE_SKILL_PATH)
        return None

    prompt = Template(skill).safe_substitute(
        source_name=source_name,
        schema_and_samples=schema_samples,
    )

    try:
        raw = llm_fn(prompt)
        code = _strip_fencing(raw)
        if code and "class " not in code:
            # The LLM returned a parse_line instead of a class — reject
            logger.warning("SQLite generation for %s returned non-class code", source_name)
            return None
        return code if code else None
    except Exception:
        logger.warning("LLM generation failed for SQLite adapter %s", source_name, exc_info=True)
        return None


def check_parse_sqlite(
    code: str, db_path: str, timeout: int = 30,
) -> tuple[bool, int, dict[str, float]]:
    """Test a generated ObserveAdapter subclass against a real SQLite DB.

    Copies the DB to a temp dir, instantiates the adapter, calls iter_sessions(),
    and checks field coverage on the resulting events.
    """
    empty_coverage: dict[str, float] = {f: 0.0 for f in _COVERAGE_FIELDS}

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "adapter.py").write_text(code)

        # Copy the real DB read-only
        src_db = Path(db_path)
        test_db = td_path / "source.db"
        shutil.copy2(src_db, test_db)

        runner = textwrap.dedent(f"""\
            import json, sys, sqlite3
            from pathlib import Path
            from datetime import UTC, datetime

            sys.path.insert(0, {td!r})

            # Import the generated adapter module
            import importlib.util
            spec = importlib.util.spec_from_file_location("gen_adapter", {str(td_path / 'adapter.py')!r})
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Find the ObserveAdapter subclass
            adapter_cls = None
            for name in dir(mod):
                obj = getattr(mod, name)
                if isinstance(obj, type) and hasattr(obj, 'iter_sessions') and hasattr(obj, 'discover') and name != 'ObserveAdapter':
                    adapter_cls = obj
                    break

            if adapter_cls is None:
                print(json.dumps({{"total": 0, "coverage": {{}}, "error": "No adapter class found"}}))
                sys.exit(0)

            # Instantiate with a dummy SykeDB (we only call iter_sessions, not ingest)
            class FakeDB:
                db_path = ":memory:"
                def event_exists_by_external_id(self, *a): return False
                def insert_event(self, *a): return True
                def transaction(self): return __import__('contextlib').nullcontext()
                def start_ingestion_run(self, *a): return "fake"
                def complete_ingestion_run(self, *a): pass

            test_db_path = Path({str(test_db)!r})
            try:
                adapter = adapter_cls(FakeDB(), "test-user", source_db_path=test_db_path)
            except TypeError:
                try:
                    adapter = adapter_cls(FakeDB(), "test-user")
                except Exception:
                    print(json.dumps({{"total": 0, "coverage": {{}}, "error": "Could not instantiate adapter"}}))
                    sys.exit(0)

            # Ensure source_db_path points to the test copy
            if hasattr(adapter, 'source_db_path'):
                adapter.source_db_path = test_db_path
            if hasattr(adapter, 'db_path') and not hasattr(adapter, 'source_db_path'):
                adapter.db_path = test_db_path

            fields = {list(_COVERAGE_FIELDS)!r}
            counts = {{f: 0 for f in fields}}
            total = 0

            try:
                for session in adapter.iter_sessions():
                    # Count the session envelope
                    total += 1
                    if hasattr(session, 'session_id') and session.session_id:
                        counts["session_id"] = counts.get("session_id", 0) + 1
                    if hasattr(session, 'start_time') and session.start_time:
                        counts["timestamp"] = counts.get("timestamp", 0) + 1

                    # Count turns
                    for turn in (session.turns if hasattr(session, 'turns') else []):
                        total += 1
                        if hasattr(session, 'session_id') and session.session_id:
                            counts["session_id"] = counts.get("session_id", 0) + 1
                        if hasattr(turn, 'role') and turn.role:
                            counts["role"] = counts.get("role", 0) + 1
                        if hasattr(turn, 'content') and turn.content:
                            counts["content"] = counts.get("content", 0) + 1
                        if hasattr(turn, 'timestamp') and turn.timestamp:
                            counts["timestamp"] = counts.get("timestamp", 0) + 1
                        if hasattr(turn, 'metadata') and turn.metadata:
                            meta = turn.metadata
                            if meta.get("model"):
                                counts["model"] = counts.get("model", 0) + 1
                            if meta.get("usage", {{}}).get("input_tokens"):
                                counts["input_tokens"] = counts.get("input_tokens", 0) + 1
                            if meta.get("usage", {{}}).get("output_tokens"):
                                counts["output_tokens"] = counts.get("output_tokens", 0) + 1
                        # event_type is always "turn" for turns
                        counts["event_type"] = counts.get("event_type", 0) + 1
            except Exception as e:
                print(json.dumps({{"total": 0, "coverage": {{}}, "error": str(e)}}))
                sys.exit(0)

            coverage = {{f: counts[f] / total if total > 0 else 0.0 for f in fields}}
            print(json.dumps({{"total": total, "coverage": coverage}}))
        """)
        (td_path / "run.py").write_text(runner)

        try:
            proc = subprocess.run(
                [sys.executable, str(td_path / "run.py")],
                capture_output=True, text=True, timeout=timeout,
            )
            if proc.returncode != 0:
                logger.warning("SQLite check stderr: %s", proc.stderr[:500])
                return False, 0, empty_coverage
            result = json.loads(proc.stdout.strip())
            if "error" in result:
                logger.warning("SQLite check error: %s", result["error"])
                return False, 0, empty_coverage
            total = result["total"]
            coverage = result["coverage"]
            if total == 0:
                return False, 0, empty_coverage
            for field, threshold in _COVERAGE_GATES.items():
                if coverage.get(field, 0.0) < threshold:
                    return False, total, coverage
            return True, total, coverage
        except (subprocess.TimeoutExpired, ValueError, json.JSONDecodeError, KeyError):
            return False, 0, empty_coverage


def _guess_format(path: Path) -> str:
    """Detect format, prioritizing SQLite (DB files are unambiguous, JSON/JSONL files may be config)."""
    has_json = False
    has_jsonl = False
    for f in path.rglob("*"):
        if f.is_file():
            suffix = f.suffix.lower()
            if suffix in {".db", ".sqlite", ".sqlite3"}:
                return "sqlite"
            if suffix == ".jsonl":
                has_jsonl = True
            elif suffix == ".json":
                has_json = True
    if has_jsonl:
        return "jsonl"
    if has_json:
        return "json"
    return "unknown"
