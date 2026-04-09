"""Metrics and logging facade over rollout traces and runtime state."""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from syke.config import user_data_dir, user_syke_db_path

# Structured logger
logger = logging.getLogger("syke")


def _writability_status(path: Path, *, label: str) -> dict[str, object]:
    base_dir = path.parent
    probe_dir = base_dir if base_dir.exists() else base_dir.parent
    writable = probe_dir.exists() and os.access(probe_dir, os.W_OK)
    detail = f"{label} writable at {path}" if writable else f"{label} not writable at {path}"
    return {
        "ok": writable,
        "path": str(path),
        "detail": detail,
    }


def runtime_metrics_status(user_id: str) -> dict[str, dict[str, object]]:
    data_dir = user_data_dir(user_id)
    file_logging = _writability_status(data_dir / "syke.log", label="File logging")
    if _LAST_FILE_LOGGING_ERROR is not None:
        file_logging = {
            **file_logging,
            "ok": False,
            "detail": f"File logging disabled: {_LAST_FILE_LOGGING_ERROR}",
        }

    trace_store = _writability_status(user_syke_db_path(user_id), label="Trace store")
    if _LAST_METRICS_PERSIST_ERROR is not None:
        trace_store = {
            **trace_store,
            "ok": False,
            "detail": f"Trace store disabled: {_LAST_METRICS_PERSIST_ERROR}",
        }

    return {
        "file_logging": file_logging,
        "trace_store": trace_store,
    }


_LAST_FILE_LOGGING_ERROR: str | None = None
_LAST_METRICS_PERSIST_ERROR: str | None = None


def setup_logging(user_id: str, verbose: bool = False) -> None:
    """Configure logging with file and console handlers."""
    global _LAST_FILE_LOGGING_ERROR
    level = logging.DEBUG if verbose else logging.INFO

    # Console handler — clean output
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(message)s"))

    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.addHandler(console)
    logger.propagate = False

    try:
        log_dir = user_data_dir(user_id)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "syke.log"
        file_handler = logging.FileHandler(log_file)
    except OSError as exc:
        _LAST_FILE_LOGGING_ERROR = str(exc)
        logger.debug("File logging disabled: %s", exc, exc_info=True)
        return

    _LAST_FILE_LOGGING_ERROR = None

    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(file_handler)


@dataclass
class RunMetrics:
    """Metrics for a single operation (ingestion, synthesis, etc.)."""

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
    """Reads operational summaries from the canonical rollout trace store."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._runs: list[RunMetrics] = []

    def record(self, metrics: RunMetrics) -> None:
        """Compatibility shim: rollout traces are now the canonical store."""
        self._runs.append(metrics)
        logger.info(
            f"{metrics.operation}: {metrics.duration_seconds:.1f}s, "
            f"${(metrics.cost_usd or 0):.4f}, "
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
        """Compatibility shim for setup validation recording."""
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
        cycle_summary = self._load_cycle_summary()

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
            "last_run": runs[-1] if runs else cycle_summary["last_cycle"],
            "synthesis_cycles_total": cycle_summary["total_cycles"],
            "synthesis_cycles_completed": cycle_summary["completed_cycles"],
            "synthesis_cycles_failed": cycle_summary["failed_cycles"],
            "synthesis_cycles_incomplete": cycle_summary["incomplete_cycles"],
            "synthesis_cycles_events_processed": cycle_summary["events_processed"],
            "synthesis_cycles_cost_usd": cycle_summary["total_cost_usd"],
            "last_cycle": cycle_summary["last_cycle"],
        }

    def _load_all(self) -> list[dict]:
        """Load all rollout summaries from syke.db."""
        try:
            from syke.db import SykeDB

            with SykeDB(user_syke_db_path(self.user_id)) as db:
                rows = db.conn.execute(
                    """
                    SELECT *
                    FROM rollout_traces
                    WHERE user_id = ?
                    ORDER BY completed_at ASC
                    """,
                    (self.user_id,),
                ).fetchall()
        except Exception as exc:
            logger.debug("Failed to load rollout traces: %s", exc, exc_info=True)
            return []

        runs = []
        for row in rows:
            entry = dict(row)
            try:
                tool_name_counts = json.loads(entry.get("tool_name_counts") or "{}")
            except (json.JSONDecodeError, TypeError):
                tool_name_counts = {}
            try:
                extras = json.loads(entry.get("extras") or "{}")
            except (json.JSONDecodeError, TypeError):
                extras = {}
            details = {
                "tool_calls": int(entry.get("tool_calls_count") or 0),
                "num_turns": int(entry.get("num_turns") or 0),
                "tool_name_counts": tool_name_counts,
                "status": entry.get("status"),
                "provider": entry.get("provider"),
                "model": entry.get("model"),
                "response_id": entry.get("response_id"),
                "stop_reason": entry.get("stop_reason"),
                "runtime_reused": bool(entry.get("runtime_reused"))
                if entry.get("runtime_reused") is not None
                else None,
                "transport": entry.get("transport"),
                "trace_id": entry.get("id"),
                **extras,
            }
            runs.append(
                {
                    "operation": entry.get("kind"),
                    "user_id": entry.get("user_id"),
                    "started_at": entry.get("started_at"),
                    "completed_at": entry.get("completed_at"),
                    "duration_seconds": float(entry.get("duration_ms") or 0) / 1000.0,
                    "input_tokens": int(entry.get("input_tokens") or 0),
                    "output_tokens": int(entry.get("output_tokens") or 0),
                    "thinking_tokens": 0,
                    "cost_usd": float(entry.get("cost_usd") or 0.0),
                    "events_processed": int(extras.get("events_processed") or 0),
                    "success": entry.get("status") == "completed",
                    "error": entry.get("error"),
                    "num_turns": int(entry.get("num_turns") or 0),
                    "duration_api_ms": int(entry.get("duration_ms") or 0),
                    "details": details,
                }
            )
        return runs

    def _load_cycle_summary(self) -> dict:
        summary = {
            "total_cycles": 0,
            "completed_cycles": 0,
            "failed_cycles": 0,
            "incomplete_cycles": 0,
            "events_processed": 0,
            "total_cost_usd": 0.0,
            "last_cycle": None,
        }
        try:
            from syke.config import user_syke_db_path
            from syke.db import SykeDB

            with SykeDB(user_syke_db_path(self.user_id)) as db:
                rollup = db.conn.execute(
                    """
                    SELECT
                        COALESCE(
                            SUM(CASE WHEN status != 'running' THEN 1 ELSE 0 END),
                            0
                        ) AS total_cycles,
                        COALESCE(
                            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END),
                            0
                        ) AS completed_cycles,
                        COALESCE(
                            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END),
                            0
                        ) AS failed_cycles,
                        COALESCE(
                            SUM(CASE WHEN status = 'incomplete' THEN 1 ELSE 0 END),
                            0
                        ) AS incomplete_cycles,
                        COALESCE(
                            SUM(
                                CASE WHEN status != 'running' THEN events_processed ELSE 0 END
                            ),
                            0
                        ) AS events_processed,
                        COALESCE(
                            SUM(CASE WHEN status != 'running' THEN cost_usd ELSE 0 END),
                            0
                        ) AS total_cost_usd
                    FROM cycle_records
                    WHERE user_id = ?
                    """,
                    (self.user_id,),
                ).fetchone()
                if rollup:
                    summary.update(
                        {
                            "total_cycles": int(rollup["total_cycles"] or 0),
                            "completed_cycles": int(rollup["completed_cycles"] or 0),
                            "failed_cycles": int(rollup["failed_cycles"] or 0),
                            "incomplete_cycles": int(rollup["incomplete_cycles"] or 0),
                            "events_processed": int(rollup["events_processed"] or 0),
                            "total_cost_usd": round(float(rollup["total_cost_usd"] or 0.0), 4),
                        }
                    )

                last_row = db.conn.execute(
                    """
                    SELECT status, started_at, completed_at, events_processed, cost_usd
                    FROM cycle_records
                    WHERE user_id = ? AND status != 'running'
                    ORDER BY started_at DESC
                    LIMIT 1
                    """,
                    (self.user_id,),
                ).fetchone()
                if last_row:
                    summary["last_cycle"] = {
                        "operation": "synthesis_cycle",
                        "status": last_row["status"],
                        "completed_at": last_row["completed_at"] or last_row["started_at"],
                        "events_processed": int(last_row["events_processed"] or 0),
                        "cost_usd": round(float(last_row["cost_usd"] or 0.0), 4),
                        "success": last_row["status"] == "completed",
                    }
        except Exception:
            return summary
        return summary


def run_health_check(user_id: str) -> dict:
    """Run health checks and return results."""
    from syke.config import user_syke_db_path

    checks: dict[str, dict] = {}

    # 1. Python environment
    import sys

    checks["python"] = {
        "ok": sys.version_info >= (3, 12),
        "detail": f"Python {sys.version.split()[0]}",
    }

    # 2. Active provider
    try:
        from syke.llm.env import resolve_provider

        provider = resolve_provider()
        checks["provider"] = {
            "ok": True,
            "detail": provider.id,
        }
    except Exception as e:
        checks["provider"] = {
            "ok": False,
            "detail": str(e),
        }

    # 3. Pi runtime
    from syke.llm.pi_client import PI_BIN

    checks["pi_runtime"] = {
        "ok": PI_BIN.exists(),
        "detail": str(PI_BIN) if PI_BIN.exists() else "Run 'syke setup' to install Pi runtime",
    }

    # 4. Database
    db_path = user_syke_db_path(user_id)
    try:
        from syke.db import SykeDB

        db = SykeDB(db_path)
        db.initialize()
        memory_count = db.count_memories(user_id, active_only=True)
        cycle_count = db.conn.execute(
            "SELECT COUNT(*) FROM cycle_records WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        db.close()
        checks["database"] = {
            "ok": True,
            "detail": f"{memory_count} active memories, {cycle_count} cycles",
        }
    except Exception as e:
        checks["database"] = {"ok": False, "detail": str(e)}

    # 5. Data directory
    data_dir = user_data_dir(user_id)
    checks["data_dir"] = {
        "ok": data_dir.exists(),
        "detail": str(data_dir),
    }

    # 6. Memex
    try:
        from syke.db import SykeDB

        db = SykeDB(user_syke_db_path(user_id))
        db.initialize()
        memex = db.get_memex(user_id)
        db.close()
        checks["memex"] = {
            "ok": memex is not None,
            "detail": "Memex exists" if memex is not None else "No memex yet \u2014 run: syke sync",
        }
    except Exception as e:
        checks["memex"] = {"ok": False, "detail": f"Error checking memex: {str(e)}"}

    # 7. Trace store
    trace_db = user_syke_db_path(user_id)
    checks["trace_store"] = {
        "ok": trace_db.exists(),
        "detail": str(trace_db) if trace_db.exists() else "No trace store yet",
    }

    # Overall
    all_critical_ok = all(checks[k]["ok"] for k in ["python", "provider", "pi_runtime", "database"])

    return {
        "healthy": all_critical_ok,
        "checks": checks,
    }
