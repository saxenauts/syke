"""SQLite schema + queries."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from uuid_extensions import uuid7

from syke.models import Event, UserProfile

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, user_id, timestamp, title)
);

CREATE INDEX IF NOT EXISTS idx_events_user_time ON events(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_user_source ON events(user_id, source);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    events_count INTEGER DEFAULT 0,
    error TEXT,
    metadata TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    profile_json TEXT NOT NULL,
    events_count INTEGER NOT NULL,
    sources TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT 'claude-opus-4-6',
    cost_usd REAL,
    thinking_tokens INTEGER
);

"""

# Migrations applied after initial schema creation.
# CONTRIBUTOR INVARIANT: all migrations must be additive-only (ALTER TABLE ADD COLUMN,
# CREATE INDEX IF NOT EXISTS), idempotent, and never destructive. Never DROP, RENAME,
# or modify existing columns or rows. OperationalError "already exists" / "duplicate column"
# is caught and treated as a no-op — this is expected and correct behavior.
_MIGRATIONS = [
    # Add external_id column for push-based dedup
    ("ALTER TABLE events ADD COLUMN external_id TEXT", "events_external_id_col"),
    # Partial unique index: dedup on (source, user_id, external_id) when external_id is set
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_external_id "
        "ON events(source, user_id, external_id) WHERE external_id IS NOT NULL",
        "events_external_id_idx",
    ),
]


class SykeDB:
    """SQLite wrapper for the Syke timeline database."""

    def __init__(self, db_path: str | Path, *, auto_initialize: bool = True):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        if auto_initialize:
            self.initialize()

    # Keep .conn as a read-only property for backward compatibility
    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def __enter__(self) -> SykeDB:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def initialize(self) -> None:
        """Create tables and indexes, then apply migrations."""
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Apply schema migrations safely (idempotent)."""
        for sql, label in _MIGRATIONS:
            try:
                self._conn.execute(sql)
                self._conn.commit()
            except sqlite3.OperationalError as e:
                if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                    pass  # Expected: column/index already present
                else:
                    raise

    def insert_event(self, event: Event) -> bool:
        """Insert an event, returning True if inserted (not duplicate)."""
        if event.id is None:
            event.id = str(uuid7())
        ingested_at = datetime.now(UTC).isoformat()
        try:
            self._conn.execute(
                """INSERT INTO events (id, user_id, source, timestamp, event_type, title, content, metadata, external_id, ingested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.id,
                    event.user_id,
                    event.source,
                    event.timestamp.isoformat(),
                    event.event_type,
                    event.title,
                    event.content,
                    json.dumps(event.metadata),
                    event.external_id,
                    ingested_at,
                ),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def insert_events(self, events: list[Event]) -> int:
        """Bulk insert events, returning count of newly inserted."""
        count = 0
        for event in events:
            if self.insert_event(event):
                count += 1
        return count

    def event_exists_by_external_id(self, source: str, user_id: str, external_id: str) -> bool:
        """Check whether an event with this external_id already exists for the source+user."""
        row = self._conn.execute(
            "SELECT 1 FROM events WHERE source = ? AND user_id = ? AND external_id = ? LIMIT 1",
            (source, user_id, external_id),
        ).fetchone()
        return row is not None

    def get_events(
        self,
        user_id: str,
        source: str | None = None,
        since: str | None = None,
        before: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Query events for a user."""
        query = "SELECT * FROM events WHERE user_id = ?"
        params: list = [user_id]

        if source:
            query += " AND source = ?"
            params.append(source)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        if before:
            query += " AND timestamp < ?"
            params.append(before)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_events_since_ingestion(
        self,
        user_id: str,
        since_ingested: str,
        limit: int = 500,
    ) -> list[dict]:
        """Get events ingested after a given timestamp.

        Filters on ingested_at — when Syke actually received the event.
        Critical for pushed events whose timestamp may be days/weeks in the past.
        """
        rows = self._conn.execute(
            "SELECT * FROM events WHERE user_id = ? AND ingested_at > ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (user_id, since_ingested, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_event_by_id(self, user_id: str, event_id: str) -> dict | None:
        """Fetch a single event by ID for a user."""
        row = self._conn.execute(
            "SELECT * FROM events WHERE user_id = ? AND id = ?",
            (user_id, event_id),
        ).fetchone()
        return dict(row) if row else None

    def search_events(self, user_id: str, query: str, limit: int = 20) -> list[dict]:
        """Keyword search across event content and titles.

        Splits query into individual keywords and matches with OR logic.
        """
        keywords = query.strip().split()
        if not keywords:
            return []

        conditions = []
        params: list = [user_id]
        for kw in keywords[:8]:
            conditions.append("(content LIKE ? OR title LIKE ?)")
            params.extend([f"%{kw}%", f"%{kw}%"])

        where = " OR ".join(conditions)
        params.append(limit)
        rows = self._conn.execute(
            f"""SELECT * FROM events
                WHERE user_id = ? AND ({where})
                ORDER BY timestamp DESC LIMIT ?""",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def count_events(self, user_id: str, source: str | None = None) -> int:
        """Count events for a user, optionally filtered by source."""
        query = "SELECT COUNT(*) FROM events WHERE user_id = ?"
        params: list = [user_id]
        if source:
            query += " AND source = ?"
            params.append(source)
        return self._conn.execute(query, params).fetchone()[0]

    def count_events_since(self, user_id: str, since: str) -> int:
        """Count events ingested after a given timestamp."""
        return self._conn.execute(
            "SELECT COUNT(*) FROM events WHERE user_id = ? AND ingested_at > ?",
            (user_id, since),
        ).fetchone()[0]

    def get_source_date_range(self, user_id: str, source: str) -> tuple[str | None, str | None]:
        """Return (oldest, newest) event timestamps for a source."""
        row = self._conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM events WHERE user_id = ? AND source = ?",
            (user_id, source),
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def get_sources(self, user_id: str) -> list[str]:
        """Get distinct sources for a user."""
        rows = self._conn.execute(
            "SELECT DISTINCT source FROM events WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [row[0] for row in rows]

    def start_ingestion_run(self, user_id: str, source: str) -> str:
        """Start an ingestion run, return run ID."""
        run_id = str(uuid7())
        self._conn.execute(
            "INSERT INTO ingestion_runs (id, user_id, source) VALUES (?, ?, ?)",
            (run_id, user_id, source),
        )
        self._conn.commit()
        return run_id

    def complete_ingestion_run(
        self, run_id: str, events_count: int, error: str | None = None
    ) -> None:
        """Mark an ingestion run as completed or failed."""
        status = "failed" if error else "completed"
        self._conn.execute(
            """UPDATE ingestion_runs
               SET completed_at = datetime('now'), status = ?, events_count = ?, error = ?
               WHERE id = ?""",
            (status, events_count, error, run_id),
        )
        self._conn.commit()

    def get_last_sync_timestamp(self, user_id: str, source: str) -> str | None:
        """Return ISO timestamp of most recent successful ingestion for a source."""
        row = self._conn.execute(
            """SELECT completed_at FROM ingestion_runs
               WHERE user_id = ? AND source = ? AND status = 'completed'
               ORDER BY completed_at DESC LIMIT 1""",
            (user_id, source),
        ).fetchone()
        return row[0] if row else None

    def get_last_profile_timestamp(self, user_id: str) -> str | None:
        """Return created_at of most recent profile, or None."""
        row = self._conn.execute(
            "SELECT created_at FROM profiles WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return row[0] if row else None

    def save_profile(self, profile: UserProfile) -> str:
        """Save a perception profile."""
        profile_id = str(uuid7())
        created_at = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT INTO profiles (id, user_id, created_at, profile_json, events_count, sources, model, cost_usd, thinking_tokens)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id,
                profile.user_id,
                created_at,
                profile.model_dump_json(),
                profile.events_count,
                json.dumps(profile.sources),
                profile.model,
                profile.cost_usd,
                profile.thinking_tokens,
            ),
        )
        self._conn.commit()
        return profile_id

    def get_latest_profile(self, user_id: str) -> UserProfile | None:
        """Get the most recent profile for a user."""
        row = self._conn.execute(
            "SELECT profile_json FROM profiles WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if row:
            return UserProfile.model_validate_json(row[0])
        return None

    def get_status(self, user_id: str) -> dict:
        """Get a summary of data for a user."""
        total = self.count_events(user_id)
        sources = self.get_sources(user_id)
        source_counts = {s: self.count_events(user_id, s) for s in sources}

        runs = self._conn.execute(
            """SELECT source, status, events_count, started_at, completed_at
               FROM ingestion_runs WHERE user_id = ?
               ORDER BY started_at DESC LIMIT 10""",
            (user_id,),
        ).fetchall()

        profile_row = self._conn.execute(
            "SELECT created_at, events_count, sources, model FROM profiles WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        latest_event_row = self._conn.execute(
            "SELECT MAX(ingested_at) FROM events WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        return {
            "user_id": user_id,
            "total_events": total,
            "sources": source_counts,
            "recent_runs": [dict(r) for r in runs],
            "latest_profile": dict(profile_row) if profile_row else None,
            "latest_event_at": latest_event_row[0] if latest_event_row else None,
        }

    def get_perception_cost_stats(self, user_id: str) -> dict | None:
        """Get perception cost statistics from the profiles table.

        Returns run count, total cost, avg cost, last run cost, and
        token breakdown from the latest run. Returns None if no profiles exist.
        """
        stats_row = self._conn.execute(
            """SELECT COUNT(*) as run_count,
                      COALESCE(SUM(cost_usd), 0) as total_cost,
                      COALESCE(AVG(cost_usd), 0) as avg_cost
               FROM profiles WHERE user_id = ? AND cost_usd IS NOT NULL""",
            (user_id,),
        ).fetchone()
        if not stats_row or stats_row[0] == 0:
            return None

        latest_row = self._conn.execute(
            """SELECT cost_usd, thinking_tokens, model
               FROM profiles WHERE user_id = ? AND cost_usd IS NOT NULL
               ORDER BY created_at DESC LIMIT 1""",
            (user_id,),
        ).fetchone()

        result = {
            "run_count": stats_row[0],
            "total_cost_usd": round(stats_row[1], 4),
            "avg_cost_usd": round(stats_row[2], 4),
        }
        if latest_row:
            result["last_run_cost_usd"] = round(latest_row[0] or 0, 4)
            result["last_run_thinking_tokens"] = latest_row[1] or 0
            result["last_run_model"] = latest_row[2] or ""

        return result

    def close(self) -> None:
        self._conn.close()
