"""Local read-only HTTP server for the Syke timeline UI.

Runs inside the daemon, bound strictly to 127.0.0.1. Read-only against
~/.syke/syke.db: every request opens its own SQLite connection in URI
read-only mode and closes it before returning.

Threat floor: personal-machine.
- Loopback bind only.
- Host header validated (defends against DNS rebinding).
- No CORS, strict CSP, no third-party fetches.
- No write endpoints. Period.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import sqlite3
import threading
from collections import deque
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from syke.config import user_syke_db_path

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
# Keep this high enough for multi-month recovery timelines.
TIMELINE_MAX = 5000
LOG_LINES_MAX = 500
DAEMON_LOG_PATH = Path(os.path.expanduser("~/.config/syke/daemon.log"))


def _open_ro(db_path: str) -> sqlite3.Connection:
    """Open the live syke.db in read-only mode, separate from daemon writer."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def _to_text(v: Any) -> str:
    """Coerce a sqlite cell value to text. Some legacy rows were written as
    BLOBs into TEXT columns; sqlite returns those as bytes. Decoding here
    keeps every downstream renderer talking to plain str.
    """
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return str(v)


def _row_text(row: sqlite3.Row, key: str) -> str:
    return _to_text(row[key])


def _coerce_dict_text(d: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Decode named fields to text in-place if they came back as bytes."""
    for k in keys:
        if k in d and isinstance(d[k], (bytes, bytearray)):
            d[k] = bytes(d[k]).decode("utf-8", errors="replace")
    return d


def _parse_json(text: str | None, fallback: Any = None) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _full_text(text: str | None) -> str:
    """Timeline inspection mode: never clip transcript or result payloads."""
    return text or ""


def _iso_second(text: str | None) -> str:
    """Return YYYY-MM-DDTHH:MM:SS prefix used as timeline second key."""
    if not text:
        return ""
    return text[:19]


def _iso_to_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


# ─── Query layer ─────────────────────────────────────────────────────────────


def query_timeline(db_path: str, user_id: str, end_iso: str, *, minutes: int) -> dict[str, Any]:
    """Return cycles + asks within (end - minutes, end], newest first.

    Lightweight rows only — detail is fetched per-event on click. The window
    is expressed in minutes so the scrubber can zoom from 1 hour through
    multi-week ranges with one parameter.
    """
    end_dt = _iso_to_dt(end_iso) or datetime.now(UTC)
    start_dt = end_dt - timedelta(minutes=minutes)
    start_iso = start_dt.astimezone(UTC).isoformat()
    end_iso_norm = end_dt.astimezone(UTC).isoformat()

    events: list[dict[str, Any]] = []
    if not Path(db_path).exists():
        return {
            "user_id": user_id,
            "window": {
                "start": start_iso,
                "end": end_iso_norm,
                "minutes": minutes,
                "days": round(minutes / 1440, 4),
            },
            "count": 0,
            "events": events,
        }

    with _open_ro(db_path) as conn:
        rows = conn.execute(
            """SELECT id, started_at, completed_at, status, memex_updated,
                      memories_created, memories_updated, links_created,
                      duration_ms, cost_usd, model
               FROM cycle_records
               WHERE user_id = ? AND started_at > ? AND started_at <= ?
               ORDER BY started_at DESC, id DESC LIMIT ?""",
            (user_id, start_iso, end_iso_norm, TIMELINE_MAX),
        ).fetchall()
        # Pull synthesis trace rows in the same window so we can attach
        # num_turns / tool_calls_count to each cycle. cycle_records.model
        # only stores the runtime label ("pi"); the trace knows the real
        # model name. Both useful for the timeline tooltip + scrubber.
        synth_rows = conn.execute(
            """SELECT id, completed_at, num_turns, tool_calls_count, model
               FROM rollout_traces
               WHERE user_id = ? AND kind = 'synthesis'
                 AND completed_at > ? AND completed_at <= ?
               ORDER BY completed_at DESC, id DESC""",
            (user_id, start_iso, end_iso_norm),
        ).fetchall()
        # Index by completed_at second-precision for O(1) cycle→trace lookup.
        # Keep a queue per second so multiple cycles finishing in the same
        # second don't all get the same trace row.
        synth_by_sec: dict[str, deque[sqlite3.Row]] = {}
        for sr in synth_rows:
            ca = _iso_second(sr["completed_at"])
            if ca:
                synth_by_sec.setdefault(ca, deque()).append(sr)
        for r in rows:
            ca = _iso_second(r["completed_at"])
            bucket = synth_by_sec.get(ca) if ca else None
            sr = bucket.popleft() if bucket else None
            events.append(
                {
                    "kind": "cycle",
                    "id": r["id"],
                    "started_at": r["started_at"],
                    "completed_at": r["completed_at"],
                    "status": r["status"],
                    "memex_updated": int(r["memex_updated"] or 0),
                    "memories_created": int(r["memories_created"] or 0),
                    "memories_updated": int(r["memories_updated"] or 0),
                    "links_created": int(r["links_created"] or 0),
                    "duration_ms": int(r["duration_ms"] or 0),
                    "cost_usd": float(r["cost_usd"] or 0),
                    "model": (sr["model"] if sr else None) or r["model"],
                    "num_turns": int(sr["num_turns"]) if sr and sr["num_turns"] else 0,
                    "tool_calls_count": int(sr["tool_calls_count"])
                    if sr and sr["tool_calls_count"]
                    else 0,
                }
            )

        ask_rows = conn.execute(
            """SELECT id, started_at, completed_at, status, duration_ms,
                      cost_usd, model, num_turns, output_text
               FROM rollout_traces
               WHERE user_id = ? AND kind = 'ask'
                 AND started_at > ? AND started_at <= ?
               ORDER BY started_at DESC LIMIT ?""",
            (user_id, start_iso, end_iso_norm, TIMELINE_MAX),
        ).fetchall()
        for r in ask_rows:
            preview = _row_text(r, "output_text").strip().split("\n", 1)[0][:120]
            events.append(
                {
                    "kind": "ask",
                    "id": r["id"],
                    "started_at": r["started_at"],
                    "completed_at": r["completed_at"],
                    "status": r["status"],
                    "duration_ms": int(r["duration_ms"] or 0),
                    "cost_usd": float(r["cost_usd"] or 0),
                    "model": r["model"],
                    "num_turns": int(r["num_turns"] or 0),
                    "preview": preview,
                }
            )

    events.sort(key=lambda e: e["started_at"], reverse=True)
    return {
        "user_id": user_id,
        "window": {
            "start": start_iso,
            "end": end_iso_norm,
            "minutes": minutes,
            "days": round(minutes / 1440, 4),
        },
        "count": len(events),
        "events": events,
    }


def query_cycle(db_path: str, user_id: str, cycle_id: str) -> dict[str, Any] | None:
    """Return full detail for a single cycle: memex content + diff base + memories + trace."""
    with _open_ro(db_path) as conn:
        cycle_row = conn.execute(
            "SELECT * FROM cycle_records WHERE user_id = ? AND id = ?",
            (user_id, cycle_id),
        ).fetchone()
        if not cycle_row:
            return None
        cycle = dict(cycle_row)

        completed_at = cycle.get("completed_at") or cycle["started_at"]

        memex_row = conn.execute(
            """SELECT id, content, created_at FROM memories
               WHERE user_id = ? AND source_event_ids = '["__memex__"]'
                 AND created_at <= ?
               ORDER BY created_at DESC, id DESC
               LIMIT 1""",
            (user_id, completed_at),
        ).fetchone()
        memex_content = _row_text(memex_row, "content") if memex_row else ""
        memex_created_at = memex_row["created_at"] if memex_row else None

        prev_memex_row = None
        if memex_row:
            prev_memex_row = conn.execute(
                """SELECT id, content, created_at FROM memories
                   WHERE user_id = ? AND source_event_ids = '["__memex__"]'
                     AND (
                       created_at < ?
                       OR (created_at = ? AND id < ?)
                     )
                   ORDER BY created_at DESC, id DESC
                   LIMIT 1""",
                (user_id, memex_row["created_at"], memex_row["created_at"], memex_row["id"]),
            ).fetchone()
        prev_memex_content = _row_text(prev_memex_row, "content") if prev_memex_row else ""

        # Memory and link snapshots at cycle boundary
        mem_rows = conn.execute(
            """SELECT id, content, source_event_ids, created_at, updated_at
               FROM memories
               WHERE user_id = ? AND active = 1
                 AND created_at <= ?
                 AND source_event_ids != '["__memex__"]'
               ORDER BY created_at DESC LIMIT 1000""",
            (user_id, completed_at),
        ).fetchall()
        memories = [_coerce_dict_text(dict(r), "content", "source_event_ids") for r in mem_rows]

        link_rows = conn.execute(
            """SELECT id, source_id, target_id, reason, created_at
               FROM links
               WHERE user_id = ? AND created_at <= ?
               ORDER BY created_at DESC LIMIT 2000""",
            (user_id, completed_at),
        ).fetchall()
        links = [_coerce_dict_text(dict(r), "reason") for r in link_rows]

        # Match the synthesis trace by completed_at proximity (microsecond drift)
        trace = None
        if cycle.get("completed_at"):
            # Align cycle detail with timeline mapping:
            # 1) find the cycle's rank among cycles finishing in this second,
            # 2) pick trace at the same rank in that second-bucket.
            cycle_second = _iso_second(cycle["completed_at"])
            sort_anchor = cycle.get("started_at") or cycle["completed_at"]
            trace_row = None
            if cycle_second:
                rank_row = conn.execute(
                    """SELECT COUNT(*) AS n
                       FROM cycle_records
                       WHERE user_id = ?
                         AND completed_at IS NOT NULL
                         AND substr(completed_at, 1, 19) = ?
                         AND (
                           COALESCE(started_at, completed_at) > ?
                           OR (
                             COALESCE(started_at, completed_at) = ?
                             AND id > ?
                           )
                         )""",
                    (user_id, cycle_second, sort_anchor, sort_anchor, cycle["id"]),
                ).fetchone()
                rank = int(rank_row["n"] or 0) if rank_row else 0
                trace_row = conn.execute(
                    """SELECT transcript, thinking, tool_calls, output_text, error,
                              tool_calls_count, tool_name_counts, num_turns,
                              input_tokens, output_tokens, cache_read_tokens,
                              duration_ms, cost_usd, model, status
                       FROM rollout_traces
                       WHERE user_id = ? AND kind = 'synthesis'
                         AND completed_at IS NOT NULL
                         AND substr(completed_at, 1, 19) = ?
                       ORDER BY completed_at DESC, id DESC
                       LIMIT 1 OFFSET ?""",
                    (user_id, cycle_second, rank),
                ).fetchone()
            if trace_row is None:
                # Legacy fallback when second-bucket matching is unavailable.
                trace_row = conn.execute(
                    """SELECT transcript, thinking, tool_calls, output_text, error,
                              tool_calls_count, tool_name_counts, num_turns,
                              input_tokens, output_tokens, cache_read_tokens,
                              duration_ms, cost_usd, model, status
                       FROM rollout_traces
                       WHERE user_id = ? AND kind = 'synthesis'
                         AND ABS(strftime('%s', completed_at) - strftime('%s', ?)) < 5
                       ORDER BY ABS(strftime('%s', completed_at) - strftime('%s', ?)) ASC
                       LIMIT 1""",
                    (user_id, cycle["completed_at"], cycle["completed_at"]),
                ).fetchone()
            if trace_row:
                transcript_str = _full_text(_row_text(trace_row, "transcript"))
                trace = {
                    "transcript": _parse_json(transcript_str, []),
                    "thinking": _parse_json(_row_text(trace_row, "thinking"), []),
                    "tool_calls": _parse_json(_row_text(trace_row, "tool_calls"), []),
                    "tool_name_counts": _parse_json(_row_text(trace_row, "tool_name_counts"), {}),
                    "tool_calls_count": int(trace_row["tool_calls_count"] or 0),
                    "num_turns": int(trace_row["num_turns"] or 0),
                    "output_text": _row_text(trace_row, "output_text"),
                    "error": _row_text(trace_row, "error"),
                    "input_tokens": int(trace_row["input_tokens"] or 0),
                    "output_tokens": int(trace_row["output_tokens"] or 0),
                    "cache_read_tokens": int(trace_row["cache_read_tokens"] or 0),
                    "duration_ms": int(trace_row["duration_ms"] or 0),
                    "cost_usd": float(trace_row["cost_usd"] or 0),
                    "model": trace_row["model"],
                    "status": trace_row["status"],
                }

    return {
        "kind": "cycle",
        "cycle": cycle,
        "memex": {"content": memex_content, "created_at": memex_created_at},
        "prev_memex": {"content": prev_memex_content},
        "memories": memories,
        "links": links,
        "trace": trace,
    }


def query_ask(db_path: str, user_id: str, ask_id: str) -> dict[str, Any] | None:
    """Return full detail for a single ask trace.

    Includes the memory + link snapshot active at the ask's moment so the
    Memory tab can show a stable grid across event types — no flicker when
    the user scrubs from a cycle to an ask.
    """
    with _open_ro(db_path) as conn:
        row = conn.execute(
            """SELECT * FROM rollout_traces
               WHERE user_id = ? AND kind = 'ask' AND id = ?""",
            (user_id, ask_id),
        ).fetchone()
        if not row:
            return None
        ask = dict(row)
        boundary = ask.get("started_at") or ask.get("completed_at")
        mem_rows = conn.execute(
            """SELECT id, content, source_event_ids, created_at, updated_at
               FROM memories
               WHERE user_id = ? AND active = 1
                 AND created_at <= ?
                 AND source_event_ids != '["__memex__"]'
               ORDER BY created_at DESC LIMIT 1000""",
            (user_id, boundary),
        ).fetchall()
        memories = [_coerce_dict_text(dict(r), "content", "source_event_ids") for r in mem_rows]
        link_rows = conn.execute(
            """SELECT id, source_id, target_id, reason, created_at
               FROM links
               WHERE user_id = ? AND created_at <= ?
               ORDER BY created_at DESC LIMIT 2000""",
            (user_id, boundary),
        ).fetchall()
        links = [_coerce_dict_text(dict(r), "reason") for r in link_rows]

        transcript_str = _full_text(_to_text(ask.get("transcript")))
        return {
            "kind": "ask",
            "memories": memories,
            "links": links,
            "ask": {
                "id": ask["id"],
                "started_at": ask["started_at"],
                "completed_at": ask["completed_at"],
                "status": ask["status"],
                "input_text": _to_text(ask.get("input_text")),
                "output_text": _to_text(ask.get("output_text")),
                "model": ask.get("model"),
                "num_turns": int(ask.get("num_turns") or 0),
                "duration_ms": int(ask.get("duration_ms") or 0),
                "cost_usd": float(ask.get("cost_usd") or 0),
                "input_tokens": int(ask.get("input_tokens") or 0),
                "output_tokens": int(ask.get("output_tokens") or 0),
            },
            "transcript": _parse_json(transcript_str, []),
            "thinking": _parse_json(_to_text(ask.get("thinking")), []),
            "tool_calls": _parse_json(_to_text(ask.get("tool_calls")), []),
        }


def query_log_tail(lines: int) -> dict[str, Any]:
    """Tail the daemon log. Bounded, no full-file load."""
    n = min(max(lines, 1), LOG_LINES_MAX)
    if not DAEMON_LOG_PATH.exists():
        return {"path": str(DAEMON_LOG_PATH), "lines": [], "exists": False}
    try:
        with DAEMON_LOG_PATH.open("rb") as fh:
            buf: deque[bytes] = deque(maxlen=n)
            for raw in fh:
                buf.append(raw.rstrip(b"\n"))
        return {
            "path": str(DAEMON_LOG_PATH),
            "lines": [b.decode("utf-8", errors="replace") for b in buf],
            "exists": True,
        }
    except OSError as exc:
        return {"path": str(DAEMON_LOG_PATH), "lines": [], "exists": True, "error": str(exc)}


def query_health(db_path: str, user_id: str) -> dict[str, Any]:
    from syke.onboarding import read_onboarding_state

    setup_blocker: dict[str, Any] | None = None
    try:
        from syke.llm.pi_client import resolve_pi_model

        resolve_pi_model(None)
    except RuntimeError as exc:
        setup_blocker = {
            "kind": "provider",
            "reason": str(exc),
            "next_steps": [
                "syke auth status",
                "syke auth set <provider> --api-key <KEY> --model <model> --use",
                "syke auth login <provider> --use",
                "syke setup --agent",
                "syke sync",
            ],
        }

    info: dict[str, Any] = {
        "user_id": user_id,
        "db_path": db_path,
        "db_present": Path(db_path).exists(),
        "log_path": str(DAEMON_LOG_PATH),
        "now": datetime.now(UTC).isoformat(),
        "last_cycle": None,
        "last_completed_cycle": None,
        "memex_updated_at": None,
        "onboarding": read_onboarding_state(user_id),
        "setup_blocker": setup_blocker,
    }
    if not info["db_present"]:
        return info
    try:
        with _open_ro(db_path) as conn:
            r = conn.execute(
                "SELECT id, started_at, completed_at, status FROM cycle_records "
                "WHERE user_id = ? ORDER BY started_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if r:
                info["last_cycle"] = dict(r)
            r2 = conn.execute(
                "SELECT id, started_at, completed_at FROM cycle_records "
                "WHERE user_id = ? AND status = 'completed' "
                "ORDER BY completed_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if r2:
                info["last_completed_cycle"] = dict(r2)
            r3 = conn.execute(
                "SELECT created_at FROM memories WHERE user_id = ? "
                "AND source_event_ids = '[\"__memex__\"]' AND active = 1 "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if r3:
                info["memex_updated_at"] = r3["created_at"]
    except sqlite3.Error as exc:
        info["error"] = str(exc)
    return info


# ─── HTTP handler ────────────────────────────────────────────────────────────


_HOST_RE = re.compile(r"^([^:]+|\[[^\]]+\])(:\d+)?$")


def _extract_host(host_header: str | None) -> str:
    if not host_header:
        return ""
    m = _HOST_RE.match(host_header.strip())
    if not m:
        return ""
    return m.group(1).lower()


def _security_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    )


def make_handler(user_id: str, html_path: Path) -> type[BaseHTTPRequestHandler]:
    db_path_factory = lambda: str(user_syke_db_path(user_id))  # noqa: E731

    class WebHandler(BaseHTTPRequestHandler):
        # Suppress default access logging (daemon log already captures lifecycle).
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            _security_headers(self)
            self.end_headers()
            self.wfile.write(body)

        def _send_text(
            self, status: int, body: str, ctype: str = "text/plain; charset=utf-8"
        ) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            _security_headers(self)
            self.end_headers()
            self.wfile.write(data)

        def _send_empty(self, status: int, ctype: str = "text/plain") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", "0")
            _security_headers(self)
            self.end_headers()

        def _check_host(self) -> bool:
            host = _extract_host(self.headers.get("Host"))
            if host in ALLOWED_HOSTS:
                return True
            self._send_json(403, {"error": "host not allowed"})
            return False

        def do_GET(self) -> None:  # noqa: N802
            if not self._check_host():
                return
            try:
                self._route()
            except Exception as exc:
                logger.error("web: handler error: %s", exc, extra={"tag": "WEB"})
                try:
                    self._send_json(500, {"error": "internal error"})
                except Exception:
                    pass

        def _route(self) -> None:
            from urllib.parse import parse_qs, urlsplit

            parts = urlsplit(self.path)
            path = parts.path
            qs = parse_qs(parts.query)

            if path == "/" or path == "/index.html":
                if not html_path.exists():
                    self._send_text(500, "UI bundle missing")
                    return
                self._send_text(
                    200, html_path.read_text(encoding="utf-8"), ctype="text/html; charset=utf-8"
                )
                return

            if path == "/favicon.ico":
                self._send_empty(204, "image/x-icon")
                return

            if path == "/api/health":
                self._send_json(200, query_health(db_path_factory(), user_id))
                return

            if path == "/api/timeline":
                end_iso = (qs.get("end") or [datetime.now(UTC).isoformat()])[0]
                # `minutes` is the canonical window parameter; `days` stays as a
                # convenience alias so old links keep working.
                minutes_param = qs.get("minutes")
                if minutes_param:
                    try:
                        minutes = int(minutes_param[0])
                    except ValueError:
                        minutes = 60 * 24 * 7
                else:
                    try:
                        minutes = int(float((qs.get("days") or ["7"])[0]) * 1440)
                    except ValueError:
                        minutes = 60 * 24 * 7
                # Clamp: 5 minutes up to 2 years for long-horizon recovery timelines.
                minutes = max(5, min(60 * 24 * 730, minutes))
                self._send_json(
                    200,
                    query_timeline(db_path_factory(), user_id, end_iso, minutes=minutes),
                )
                return

            m = re.match(r"^/api/cycle/([0-9a-fA-F\-]{8,})$", path)
            if m:
                detail = query_cycle(db_path_factory(), user_id, m.group(1))
                if detail is None:
                    self._send_json(404, {"error": "cycle not found"})
                else:
                    self._send_json(200, detail)
                return

            m = re.match(r"^/api/ask/([0-9a-fA-F\-]{8,})$", path)
            if m:
                detail = query_ask(db_path_factory(), user_id, m.group(1))
                if detail is None:
                    self._send_json(404, {"error": "ask not found"})
                else:
                    self._send_json(200, detail)
                return

            if path == "/api/log/tail":
                try:
                    lines = max(1, min(LOG_LINES_MAX, int((qs.get("lines") or ["200"])[0])))
                except ValueError:
                    lines = 200
                self._send_json(200, query_log_tail(lines))
                return

            self._send_json(404, {"error": "not found"})

    return WebHandler


# ─── Server lifecycle ────────────────────────────────────────────────────────


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class SykeWebServer:
    """Local read-only timeline server. Runs in a daemon thread."""

    def __init__(self, user_id: str, port: int, html_path: Path):
        self.user_id = user_id
        self.port = port
        self.html_path = html_path
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> bool:
        try:
            handler = make_handler(self.user_id, self.html_path)
            # Explicit loopback bind. Refuse to bind to anything else even if env is wrong.
            self._server = _Server(("127.0.0.1", self.port), handler)
        except OSError as exc:
            logger.info("Web server disabled: bind failed on 127.0.0.1:%s (%s)", self.port, exc)
            self._server = None
            return False

        def _serve() -> None:
            try:
                assert self._server is not None
                self._server.serve_forever(poll_interval=0.5)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("web server crashed: %s", exc, extra={"tag": "WEB"})

        self._thread = threading.Thread(target=_serve, name="syke-web", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception as exc:
                logger.debug("web server shutdown: %s", exc, exc_info=True)
            self._server = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None


def web_server_status(port: int, *, timeout: float = 0.25) -> dict[str, Any]:
    """Probe whether the local web server is reachable on 127.0.0.1:port."""
    info: dict[str, Any] = {
        "ok": False,
        "url": f"http://127.0.0.1:{port}/",
        "reachable": False,
        "detail": None,
    }
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(("127.0.0.1", port))
        info["reachable"] = True
        info["ok"] = True
        info["detail"] = f"web server reachable at {info['url']}"
    except OSError as exc:
        info["detail"] = str(exc)
    return info
