"""Sense factory — discover harnesses, generate adapters, heal broken ones.

Dumb orchestration. The skill file (skills/generate_adapter.md) carries all intelligence.
This module is just plumbing: read samples, call LLM, test output, write to disk.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Callable
from pathlib import Path
from string import Template

logger = logging.getLogger(__name__)

_SKILL_PATH = Path(__file__).parent / "skills" / "generate_adapter.md"
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

    Returns (success, message).
    """
    path = Path(path)
    if not path.exists():
        return False, f"Path not found: {path}"

    samples = _read_samples(path)
    if not samples:
        return False, "No data found to analyze"

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


def _guess_format(path: Path) -> str:
    """Single-pass format detection — checks first matching file."""
    for f in path.rglob("*"):
        if f.is_file():
            suffix = f.suffix.lower()
            if suffix == ".jsonl":
                return "jsonl"
            if suffix == ".json":
                return "json"
            if suffix in {".db", ".sqlite", ".sqlite3"}:
                return "sqlite"
    return "unknown"
