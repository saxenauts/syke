"""Metrics and logging facade over rollout traces and runtime state."""

from __future__ import annotations

import json
import logging
import os
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


def _ensure_private_file(path: Path) -> None:
    path.touch(exist_ok=True)
    os.chmod(path, 0o600)


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
        _ensure_private_file(log_file)
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


class MetricsTracker:
    """Reads operational summaries from the canonical rollout trace store."""

    def __init__(self, user_id: str):
        self.user_id = user_id

    def get_summary(self) -> dict:
        """Load all metrics and produce a summary."""
        runs = self._load_all()
        cycle_summary = self._load_cycle_summary()

        total_cost = sum(r.get("cost_usd", 0) for r in runs)
        total_tokens = sum(
            r.get("input_tokens", 0) + r.get("output_tokens", 0) + r.get("thinking_tokens", 0)
            for r in runs
        )
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
            "by_operation": by_operation,
            "last_run": runs[-1] if runs else cycle_summary["last_cycle"],
            "synthesis_cycles_total": cycle_summary["total_cycles"],
            "synthesis_cycles_completed": cycle_summary["completed_cycles"],
            "synthesis_cycles_failed": cycle_summary["failed_cycles"],
            "synthesis_cycles_incomplete": cycle_summary["incomplete_cycles"],
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
                            "total_cost_usd": round(float(rollup["total_cost_usd"] or 0.0), 4),
                        }
                    )

                last_row = db.conn.execute(
                    """
                    SELECT status, started_at, completed_at, cost_usd
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
                        "cost_usd": round(float(last_row["cost_usd"] or 0.0), 4),
                        "success": last_row["status"] == "completed",
                    }
        except Exception:
            return summary
        return summary
