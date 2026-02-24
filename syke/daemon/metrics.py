"""Metrics and logging — tracks cost, tokens, timing, and operational health."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from syke.config import user_data_dir

# Structured logger
logger = logging.getLogger("syke")


def setup_logging(user_id: str, verbose: bool = False) -> None:
    """Configure logging with file and console handlers."""
    level = logging.DEBUG if verbose else logging.INFO

    # Console handler — clean output
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(message)s"))

    # File handler — structured with timestamps
    log_dir = user_data_dir(user_id)
    log_file = log_dir / "syke.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.addHandler(console)
    logger.addHandler(file_handler)


@dataclass
class RunMetrics:
    """Metrics for a single operation (ingestion, etc.)."""

    operation: str
    user_id: str
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cost_usd: float = 0.0
    events_processed: int = 0
    success: bool = True
    error: str | None = None
    method: str | None = None  # "agentic" | "agentic-v2" | "meta" for synthesis runs
    num_turns: int = 0  # API round-trips for synthesis
    duration_api_ms: float = 0.0  # Time spent waiting for API responses
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class MetricsTracker:
    """Tracks and persists operational metrics."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.metrics_file = user_data_dir(user_id) / "metrics.jsonl"
        self._runs: list[RunMetrics] = []

    def record(self, metrics: RunMetrics) -> None:
        """Record a completed operation's metrics."""
        self._runs.append(metrics)
        # Append to JSONL file
        with open(self.metrics_file, "a") as f:
            f.write(json.dumps(metrics.to_dict()) + "\n")
        logger.info(
            f"[metrics] {metrics.operation}: {metrics.duration_seconds:.1f}s, "
            f"${metrics.cost_usd:.4f}, "
            f"{metrics.input_tokens + metrics.output_tokens + metrics.thinking_tokens} tokens"
        )

    @contextmanager
    def track(self, operation: str, **details):
        """Context manager to track an operation's metrics."""
        metrics = RunMetrics(
            operation=operation,
            user_id=self.user_id,
            started_at=datetime.now(UTC).isoformat(),
            details=details,
        )
        start = time.monotonic()
        try:
            yield metrics
            metrics.success = True
        except Exception as e:
            metrics.success = False
            metrics.error = str(e)
            raise
        finally:
            metrics.duration_seconds = time.monotonic() - start
            metrics.completed_at = datetime.now(UTC).isoformat()
            self.record(metrics)

    def record_setup(self, steps: list[dict]) -> None:
        """Record setup validation results to metrics.jsonl."""
        entry = RunMetrics(
            operation="validate",
            user_id=self.user_id,
            started_at=steps[0].get("name", "") if steps else "",
            completed_at=datetime.now(UTC).isoformat(),
            success=all(s.get("status") == "pass" for s in steps),
            details={
                "steps": steps,
                "passed": sum(1 for s in steps if s.get("status") == "pass"),
                "failed": sum(1 for s in steps if s.get("status") == "fail"),
                "total_duration_ms": sum(s.get("duration_ms", 0) for s in steps),
            },
        )
        self.record(entry)

    def get_summary(self) -> dict:
        """Load all metrics and produce a summary."""
        runs = self._load_all()

        total_cost = sum(r.get("cost_usd", 0) for r in runs)
        total_tokens = sum(
            r.get("input_tokens", 0) + r.get("output_tokens", 0) + r.get("thinking_tokens", 0)
            for r in runs
        )
        total_events = sum(r.get("events_processed", 0) for r in runs)

        by_operation: dict[str, dict] = {}
        for r in runs:
            op = r.get("operation", "unknown")
            if op not in by_operation:
                by_operation[op] = {"count": 0, "cost_usd": 0.0, "total_tokens": 0, "errors": 0}
            by_operation[op]["count"] += 1
            by_operation[op]["cost_usd"] += r.get("cost_usd", 0)
            by_operation[op]["total_tokens"] += (
                r.get("input_tokens", 0) + r.get("output_tokens", 0) + r.get("thinking_tokens", 0)
            )
            if not r.get("success", True):
                by_operation[op]["errors"] += 1

        return {
            "total_runs": len(runs),
            "total_cost_usd": total_cost,
            "total_tokens": total_tokens,
            "total_events_processed": total_events,
            "by_operation": by_operation,
            "last_run": runs[-1] if runs else None,
        }

    def _load_all(self) -> list[dict]:
        """Load all metric records from the JSONL file."""
        if not self.metrics_file.exists():
            return []
        runs = []
        for line in self.metrics_file.read_text().splitlines():
            if line.strip():
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return runs


def run_health_check(user_id: str) -> dict:
    """Run health checks and return results."""
    from syke.config import ANTHROPIC_API_KEY, GITHUB_TOKEN, user_db_path

    checks: dict[str, dict] = {}

    # 1. Python environment
    import sys
    checks["python"] = {
        "ok": sys.version_info >= (3, 12),
        "detail": f"Python {sys.version.split()[0]}",
    }

    # 2. Anthropic API key
    checks["anthropic_key"] = {
        "ok": bool(ANTHROPIC_API_KEY) and ANTHROPIC_API_KEY.startswith("sk-"),
        "detail": "Set" if ANTHROPIC_API_KEY else "Missing — set ANTHROPIC_API_KEY in .env",
    }

    # 3. Database
    db_path = user_db_path(user_id)
    try:
        from syke.db import SykeDB
        db = SykeDB(db_path)
        db.initialize()
        event_count = db.count_events(user_id)
        sources = db.get_sources(user_id)
        db.close()
        checks["database"] = {
            "ok": True,
            "detail": f"{event_count} events from {', '.join(sources) or 'no sources'}",
        }
    except Exception as e:
        checks["database"] = {"ok": False, "detail": str(e)}

    # 4. Gmail (gog CLI or Python OAuth)
    import os as _os
    from syke.ingestion.gmail import _gog_authenticated, _python_oauth_available
    gmail_ok = False
    gmail_detail = "No backend available"
    _gmail_acct = _os.getenv("GMAIL_ACCOUNT", "")
    if _gmail_acct and _gog_authenticated(_gmail_acct):
        gmail_ok = True
        gmail_detail = f"gog CLI authenticated ({_gmail_acct})"
    elif _python_oauth_available():
        _tok = Path(_os.path.expanduser(
            _os.getenv("GMAIL_TOKEN_PATH", "~/.config/syke/gmail_token.json")
        ))
        if _tok.exists():
            gmail_ok = True
            gmail_detail = "Python OAuth (token cached)"
        else:
            _creds = Path(_os.path.expanduser(
                _os.getenv("GMAIL_CREDENTIALS_PATH", "~/.config/syke/gmail_credentials.json")
            ))
            if _creds.exists():
                gmail_ok = True
                gmail_detail = "Python OAuth (credentials ready, will prompt for consent)"
            else:
                gmail_detail = "google-auth-oauthlib installed but no credentials"
    checks["gmail"] = {"ok": gmail_ok, "detail": gmail_detail}

    # 5. GitHub token
    checks["github_token"] = {
        "ok": bool(GITHUB_TOKEN),
        "detail": "Set" if GITHUB_TOKEN else "Missing — set GITHUB_TOKEN in .env (optional)",
    }

    # 6. Data directory
    data_dir = user_data_dir(user_id)
    checks["data_dir"] = {
        "ok": data_dir.exists(),
        "detail": str(data_dir),
    }

    # 7. Memex
    try:
        from syke.db import SykeDB
        db = SykeDB(user_db_path(user_id))
        db.initialize()
        memex = db.get_memex(user_id)
        db.close()
        checks["memex"] = {
            "ok": memex is not None,
            "detail": "Memex exists" if memex is not None else "No memex yet — run: syke perceive",
        }
    except Exception as e:
        checks["memex"] = {"ok": False, "detail": f"Error checking memex: {str(e)}"}

    # 8. Metrics file
    metrics_file = data_dir / "metrics.jsonl"
    checks["metrics"] = {
        "ok": metrics_file.exists(),
        "detail": f"{metrics_file.stat().st_size} bytes" if metrics_file.exists() else "No metrics recorded yet",
    }

    # Overall
    all_critical_ok = all(
        checks[k]["ok"] for k in ["python", "anthropic_key", "database"]
    )

    return {
        "healthy": all_critical_ok,
        "checks": checks,
    }
