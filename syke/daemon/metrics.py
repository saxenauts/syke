"""Daemon health checks plus shared metrics facade."""

from __future__ import annotations

from syke.config import user_data_dir, user_syke_db_path
from syke.metrics import MetricsTracker, RunMetrics, setup_logging

__all__ = ["MetricsTracker", "RunMetrics", "run_health_check", "setup_logging"]


def run_health_check(user_id: str) -> dict:
    """Run health checks and return results."""
    checks: dict[str, dict] = {}

    import sys

    checks["python"] = {
        "ok": sys.version_info >= (3, 12),
        "detail": f"Python {sys.version.split()[0]}",
    }

    db_path = user_syke_db_path(user_id)
    db = None
    try:
        from syke.db import SykeDB

        db = SykeDB(db_path)
        db.initialize()
        event_count = db.count_events(user_id)
        memory_count = db.count_memories(user_id, active_only=True)
        cycle_count = db.conn.execute(
            "SELECT COUNT(*) FROM cycle_records WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        trace_count = db.conn.execute(
            "SELECT COUNT(*) FROM rollout_traces WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        sources = db.get_sources(user_id)
        checks["database"] = {
            "ok": True,
            "detail": (
                f"{event_count} events, {memory_count} active memories, {cycle_count} cycles, "
                f"{trace_count} rollout traces from {', '.join(sources) or 'no sources'}"
            ),
        }
    except Exception as e:
        checks["database"] = {"ok": False, "detail": str(e)}

    data_dir = user_data_dir(user_id)
    checks["data_dir"] = {
        "ok": data_dir.exists(),
        "detail": str(data_dir),
    }

    # Memex check — reuse open db connection
    if db is not None:
        try:
            memex = db.get_memex(user_id)
            checks["memex"] = {
                "ok": memex is not None,
                "detail": "Memex exists" if memex is not None else "No memex yet",
            }
        except Exception as e:
            checks["memex"] = {"ok": False, "detail": str(e)}
    else:
        checks["memex"] = {"ok": False, "detail": "Database unavailable"}

    checks["trace_store"] = {
        "ok": db_path.exists(),
        "detail": str(db_path) if db_path.exists() else "No trace store yet",
    }

    # Synthesis freshness — reuse health.py
    if db is not None:
        try:
            from syke.health import synthesis_health

            synth = synthesis_health(db, user_id)
            synth_state = synth.get("assessment", "unknown")
            checks["synthesis"] = {
                "ok": synth_state in ("active", "recent", "never_run"),
                "detail": synth_state,
            }
        except Exception as e:
            checks["synthesis"] = {"ok": False, "detail": str(e)}

    # Signals — surface degradation (stale sources, orphan memories, etc.)
    if db is not None:
        try:
            from syke.health import signals

            sigs = signals(db, user_id)
            checks["signals"] = {
                "ok": len(sigs) == 0,
                "detail": [s.get("detail", s.get("type", "unknown")) for s in sigs] if sigs else [],
            }
        except Exception as e:
            checks["signals"] = {"ok": False, "detail": str(e)}

    # Runtime: is Pi alive and reachable via IPC?
    try:
        from syke.daemon.ipc import daemon_runtime_status

        rt = daemon_runtime_status(user_id, timeout=0.5)
        checks["runtime"] = {
            "ok": bool(rt.get("alive")),
            "detail": rt.get("detail", "unknown"),
        }
    except Exception as e:
        checks["runtime"] = {"ok": False, "detail": str(e)}

    if db is not None:
        db.close()

    all_critical_ok = all(checks[k]["ok"] for k in ["python", "database"])
    return {"healthy": all_critical_ok, "checks": checks}
