"""SQLite schema + queries."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from uuid_extensions import uuid7

from syke.models import Event, Link, Memory

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
    # -----------------------------------------------------------------------
    # Memory layer (storage branch) — memories, links, memory_ops, FTS5
    # -----------------------------------------------------------------------
    # --- memories table ---
    (
        """CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            content TEXT NOT NULL,
            source_event_ids TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT,
            superseded_by TEXT,
            active INTEGER DEFAULT 1
        )""",
        "create_memories_table",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memories_user_active ON memories(user_id, active)",
        "memories_user_active_idx",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memories_user_created "
        "ON memories(user_id, created_at DESC)",
        "memories_user_created_idx",
    ),
    # --- links table ---
    (
        """CREATE TABLE IF NOT EXISTS links (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        "create_links_table",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_id)",
        "links_source_idx",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_id)",
        "links_target_idx",
    ),
    # --- memory_ops table (audit log + synthesis gating) ---
    (
        """CREATE TABLE IF NOT EXISTS memory_ops (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            input_summary TEXT DEFAULT '',
            output_summary TEXT DEFAULT '',
            memory_ids TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            duration_ms INTEGER,
            metadata TEXT DEFAULT '{}'
        )""",
        "create_memory_ops_table",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_memory_ops_user_time "
        "ON memory_ops(user_id, created_at DESC)",
        "memory_ops_user_time_idx",
    ),
    # --- FTS5 on memories (BM25 search) ---
    (
        "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
        "memory_id UNINDEXED, content, tokenize='porter unicode61')",
        "memories_fts5_table",
    ),
    # --- FTS5 on events (BM25 search, replaces LIKE) ---
    (
        "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5("
        "event_id UNINDEXED, title, content, tokenize='porter unicode61')",
        "events_fts5_table",
    ),
    (
        """CREATE TABLE IF NOT EXISTS synthesis_cursor (
            user_id TEXT PRIMARY KEY,
            last_event_id TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        "create_synthesis_cursor_table",
    ),
    # --- Observe Phase 2: session columns on events ---
    ("ALTER TABLE events ADD COLUMN session_id TEXT", "events_session_id_col"),
    ("ALTER TABLE events ADD COLUMN parent_session_id TEXT", "events_parent_session_id_col"),
    (
        "CREATE INDEX IF NOT EXISTS idx_events_session_id "
        "ON events(session_id) WHERE session_id IS NOT NULL",
        "events_session_id_idx",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_events_parent_session "
        "ON events(parent_session_id) WHERE parent_session_id IS NOT NULL",
        "events_parent_session_idx",
    ),
    # --- Observe canonical schema: typed columns ---
    ("ALTER TABLE events ADD COLUMN sequence_index INTEGER", "events_sequence_index_col"),
    ("ALTER TABLE events ADD COLUMN role TEXT", "events_role_col"),
    ("ALTER TABLE events ADD COLUMN model TEXT", "events_model_col"),
    ("ALTER TABLE events ADD COLUMN stop_reason TEXT", "events_stop_reason_col"),
    ("ALTER TABLE events ADD COLUMN input_tokens INTEGER", "events_input_tokens_col"),
    ("ALTER TABLE events ADD COLUMN output_tokens INTEGER", "events_output_tokens_col"),
    ("ALTER TABLE events ADD COLUMN cache_read_tokens INTEGER", "events_cache_read_tokens_col"),
    ("ALTER TABLE events ADD COLUMN is_error INTEGER DEFAULT 0", "events_is_error_col"),
    ("ALTER TABLE events ADD COLUMN source_event_type TEXT", "events_source_event_type_col"),
    ("ALTER TABLE events ADD COLUMN source_path TEXT", "events_source_path_col"),
    ("ALTER TABLE events ADD COLUMN source_line_index INTEGER", "events_source_line_index_col"),
    ("ALTER TABLE events ADD COLUMN extras TEXT DEFAULT '{}'", "events_extras_col"),
    (
        "ALTER TABLE events ADD COLUMN cache_creation_tokens INTEGER",
        "events_cache_creation_tokens_col",
    ),
    ("ALTER TABLE events ADD COLUMN tool_name TEXT", "events_tool_name_col"),
    ("ALTER TABLE events ADD COLUMN tool_correlation_id TEXT", "events_tool_correlation_id_col"),
    ("ALTER TABLE events ADD COLUMN duration_ms INTEGER", "events_duration_ms_col"),
    ("ALTER TABLE events ADD COLUMN parent_event_id TEXT", "events_parent_event_id_col"),
    (
        "CREATE INDEX IF NOT EXISTS idx_events_model ON events(model) WHERE model IS NOT NULL",
        "events_model_idx",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, timestamp)",
        "events_type_time_idx",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_events_tool_name "
        "ON events(tool_name) WHERE tool_name IS NOT NULL",
        "events_tool_name_idx",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_events_parent_event "
        "ON events(parent_event_id) WHERE parent_event_id IS NOT NULL",
        "events_parent_event_idx",
    ),
    # Tag legacy claude-code and codex events for re-ingestion safety
    (
        "UPDATE events SET extras = json_set(COALESCE(extras, '{}'), '$.legacy_format', json('true')) "
        "WHERE (source = 'claude-code' OR source = 'codex') AND session_id IS NULL",
        "tag_legacy_events",
    ),
    # --- Observe Phase 3: source instance tracking ---
    ("ALTER TABLE events ADD COLUMN source_instance_id TEXT", "events_source_instance_id_col"),
    (
        "CREATE INDEX IF NOT EXISTS idx_events_source_instance "
        "ON events(source_instance_id) WHERE source_instance_id IS NOT NULL",
        "events_source_instance_idx",
    ),
    # --- Synthesis cycle provenance ---
    (
        "CREATE TABLE IF NOT EXISTS cycle_records ("
        "  id TEXT PRIMARY KEY,"
        "  user_id TEXT NOT NULL,"
        "  started_at TEXT NOT NULL,"
        "  completed_at TEXT,"
        "  cursor_start TEXT,"
        "  cursor_end TEXT,"
        "  skill_hash TEXT,"
        "  prompt_hash TEXT,"
        "  model TEXT,"
        "  status TEXT NOT NULL DEFAULT 'running',"
        "  events_processed INTEGER DEFAULT 0,"
        "  memories_created INTEGER DEFAULT 0,"
        "  memories_updated INTEGER DEFAULT 0,"
        "  links_created INTEGER DEFAULT 0,"
        "  memex_updated INTEGER DEFAULT 0,"
        "  cost_usd REAL DEFAULT 0,"
        "  input_tokens INTEGER DEFAULT 0,"
        "  output_tokens INTEGER DEFAULT 0,"
        "  cache_read_tokens INTEGER DEFAULT 0,"
        "  duration_ms INTEGER DEFAULT 0"
        ")",
        "cycle_records_table",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_cycle_records_user "
        "ON cycle_records(user_id, started_at DESC)",
        "cycle_records_user_idx",
    ),
    (
        "CREATE TABLE IF NOT EXISTS cycle_annotations ("
        "  id TEXT PRIMARY KEY,"
        "  cycle_id TEXT NOT NULL,"
        "  annotator TEXT NOT NULL,"
        "  annotation_type TEXT NOT NULL,"
        "  content TEXT NOT NULL,"
        "  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
        ")",
        "cycle_annotations_table",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_cycle_annotations_cycle ON cycle_annotations(cycle_id)",
        "cycle_annotations_cycle_idx",
    ),
    # --- cycle_records schema drift fix (columns added after initial CREATE TABLE) ---
    ("ALTER TABLE cycle_records ADD COLUMN cost_usd REAL DEFAULT 0", "cycle_records_cost_usd_col"),
    ("ALTER TABLE cycle_records ADD COLUMN input_tokens INTEGER DEFAULT 0", "cycle_records_input_tokens_col"),
    ("ALTER TABLE cycle_records ADD COLUMN output_tokens INTEGER DEFAULT 0", "cycle_records_output_tokens_col"),
    ("ALTER TABLE cycle_records ADD COLUMN cache_read_tokens INTEGER DEFAULT 0", "cycle_records_cache_read_col"),
    ("ALTER TABLE cycle_records ADD COLUMN duration_ms INTEGER DEFAULT 0", "cycle_records_duration_ms_col"),
    # --- FTS5 sync triggers on memories ---
    (
        "CREATE TRIGGER IF NOT EXISTS memories_fts_insert "
        "AFTER INSERT ON memories BEGIN "
        "INSERT INTO memories_fts(memory_id, content) VALUES (NEW.id, NEW.content); "
        "END",
        "memories_fts_insert_trigger",
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS memories_fts_update "
        "AFTER UPDATE ON memories BEGIN "
        "DELETE FROM memories_fts WHERE memory_id = OLD.id; "
        "INSERT INTO memories_fts(memory_id, content) "
        "SELECT NEW.id, NEW.content WHERE NEW.active = 1; "
        "END",
        "memories_fts_update_trigger",
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS memories_fts_delete "
        "AFTER DELETE ON memories BEGIN "
        "DELETE FROM memories_fts WHERE memory_id = OLD.id; "
        "END",
        "memories_fts_delete_trigger",
    ),
]

# Separate from _MIGRATIONS because it's a DML backfill, not a DDL migration.
# Runs once when events_fts is empty.
_BACKFILL_EVENTS_FTS = (
    "INSERT INTO events_fts(event_id, title, content) "
    "SELECT id, COALESCE(title, ''), content FROM events "
    "WHERE NOT EXISTS (SELECT 1 FROM events_fts LIMIT 1)"
)


class SykeDB:
    """SQLite wrapper for the Syke timeline database."""

    def __init__(self, db_path: str | Path, *, auto_initialize: bool = True):
        path_str = str(db_path)
        # Guard against passing a bare username instead of a file path.
        # Allow :memory: for tests and paths with a directory or .db extension.
        if (
            path_str != ":memory:"
            and "/" not in path_str
            and "\\" not in path_str
            and not path_str.endswith(".db")
        ):
            raise ValueError(
                f"SykeDB(db_path) looks like a username, not a file path: {path_str!r}. "
                f"Use user_db_path(user_id) to get the correct path."
            )
        self.db_path = path_str
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._in_transaction = False
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

    @contextmanager
    def transaction(self):
        """Atomic write: all inserts succeed or all roll back."""
        if self._conn.in_transaction:
            self._conn.commit()
        self._conn.execute("BEGIN IMMEDIATE")
        self._in_transaction = True
        try:
            yield
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        finally:
            self._in_transaction = False

    def initialize(self) -> None:
        """Create tables and indexes, then apply migrations."""
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Apply schema migrations safely (idempotent)."""
        for sql, _label in _MIGRATIONS:
            try:
                self._conn.execute(sql)
                self._conn.commit()
            except sqlite3.OperationalError as e:
                if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                    pass  # Expected: column/index already present
                else:
                    raise
        # Backfill events_fts from existing events (one-time, idempotent)
        try:
            self._conn.execute(_BACKFILL_EVENTS_FTS)
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # events_fts table might not exist (shouldn't happen, but safe)

    # ===================================================================
    # Events
    # ===================================================================

    def insert_event(self, event: Event) -> bool:
        """Insert an event, returning True if inserted (not duplicate)."""
        if event.id is None:
            event.id = str(uuid7())
        ingested_at = datetime.now(UTC).isoformat()
        # Legacy `metadata` column and new `extras` column both receive the merged dict.
        # New Observe events set metadata={}, so merged == extras. Redundant but harmless —
        # changing this requires migrating all metadata column readers (synthesis, tests).
        merged_extras = {**event.metadata, **event.extras} if event.metadata else event.extras
        try:
            self._conn.execute(
                """INSERT INTO events (
                   id, user_id, source, timestamp, event_type, title, content,
                   metadata, external_id, session_id, parent_session_id, ingested_at,
                   sequence_index, role, model, stop_reason,
                   input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                   tool_name, tool_correlation_id, is_error, duration_ms,
                   parent_event_id,
                   source_event_type, source_path, source_line_index, extras,
                   source_instance_id
                   ) VALUES (
                   ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?, ?, ?,
                   ?, ?, ?, ?,
                   ?, ?, ?, ?,
                   ?, ?, ?, ?,
                   ?,
                   ?, ?, ?, ?,
                   ?
                   )""",
                (
                    event.id,
                    event.user_id,
                    event.source,
                    event.timestamp.isoformat(),
                    event.event_type,
                    event.title,
                    event.content,
                    json.dumps(merged_extras),
                    event.external_id,
                    event.session_id,
                    event.parent_session_id,
                    ingested_at,
                    event.sequence_index,
                    event.role,
                    event.model,
                    event.stop_reason,
                    event.input_tokens,
                    event.output_tokens,
                    event.cache_read_tokens,
                    event.cache_creation_tokens,
                    event.tool_name,
                    event.tool_correlation_id,
                    event.is_error,
                    event.duration_ms,
                    event.parent_event_id,
                    event.source_event_type,
                    event.source_path,
                    event.source_line_index,
                    json.dumps(merged_extras),
                    event.source_instance_id,
                ),
            )
            try:
                self._conn.execute(
                    "INSERT INTO events_fts(event_id, title, content) VALUES (?, ?, ?)",
                    (event.id, event.title or "", event.content),
                )
            except sqlite3.OperationalError:
                pass
            if not self._in_transaction:
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

    def search_events_fts(self, user_id: str, query: str, limit: int = 20) -> list[dict]:
        """FTS5/BM25 search over events. Falls back to LIKE if FTS not populated."""
        if not query.strip():
            return []
        try:
            rows = self._conn.execute(
                """SELECT e.*, bm25(events_fts) as rank
                   FROM events_fts fts
                   JOIN events e ON e.id = fts.event_id
                   WHERE events_fts MATCH ? AND e.user_id = ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, user_id, limit),
            ).fetchall()
            if rows:
                return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            pass  # FTS table might not exist yet, fall back
        # Fallback to existing LIKE search
        return self.search_events(user_id, query, limit)

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

    def count_events_after_id(
        self, user_id: str, event_id: str, *, exclude_source: str | None = None
    ) -> int:
        query = "SELECT COUNT(*) FROM events WHERE user_id = ? AND id > ?"
        params: list[str] = [user_id, event_id]
        if exclude_source:
            query += " AND source != ?"
            params.append(exclude_source)
        return self._conn.execute(query, params).fetchone()[0]

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

    # ===================================================================
    # Ingestion runs
    # ===================================================================

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

    # ===================================================================
    # Observe — graph health, synthesis stats, evolution metrics
    # ===================================================================

    def get_graph_stats(self, user_id: str) -> dict:
        """Memory graph statistics: counts, density, hub nodes, orphans."""
        active = self.count_memories(user_id, active_only=True)
        retired = self.count_memories(user_id, active_only=False) - active

        link_count = self._conn.execute(
            "SELECT COUNT(*) FROM links WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

        # Hub nodes: memories with the most links (either direction)
        hub_rows = self._conn.execute(
            """SELECT m.id, SUBSTR(m.content, 1, 60) as preview,
                      COUNT(DISTINCT l.id) as link_count
               FROM memories m
               JOIN links l ON (l.source_id = m.id OR l.target_id = m.id)
                            AND l.user_id = m.user_id
               WHERE m.user_id = ? AND m.active = 1
               GROUP BY m.id
               ORDER BY link_count DESC LIMIT 5""",
            (user_id,),
        ).fetchall()

        # Orphan count: active memories with zero links
        orphan_count = self._conn.execute(
            """SELECT COUNT(*) FROM memories m
               WHERE m.user_id = ? AND m.active = 1
               AND NOT EXISTS (
                   SELECT 1 FROM links l
                   WHERE l.user_id = m.user_id
                   AND (l.source_id = m.id OR l.target_id = m.id)
               )""",
            (user_id,),
        ).fetchone()[0]

        # Supersession chain stats
        chain_rows = self._conn.execute(
            """WITH RECURSIVE chain(id, depth) AS (
                   SELECT id, 0 FROM memories
                   WHERE user_id = ? AND superseded_by IS NULL AND active = 1
                 UNION ALL
                   SELECT m.id, c.depth + 1
                   FROM memories m JOIN chain c ON m.superseded_by = c.id
                   WHERE m.user_id = ?
               )
               SELECT MAX(depth) as max_depth,
                      AVG(depth) as avg_depth,
                      COUNT(CASE WHEN depth > 0 THEN 1 END) as chains_with_history
               FROM chain""",
            (user_id, user_id),
        ).fetchone()

        return {
            "active": active,
            "retired": retired,
            "links": link_count,
            "density": round(link_count / active, 2) if active else 0,
            "hubs": [
                {"preview": r["preview"].strip().split("\n")[0], "links": r["link_count"]}
                for r in hub_rows
            ],
            "orphan_count": orphan_count,
            "orphan_rate": round(orphan_count / active, 2) if active else 0,
            "supersession_max_depth": chain_rows["max_depth"] or 0,
            "supersession_avg_depth": round(chain_rows["avg_depth"] or 0, 1),
            "chains_with_history": chain_rows["chains_with_history"] or 0,
        }

    def get_synthesis_stats(self, user_id: str, limit: int = 10) -> list[dict]:
        """Recent synthesis operations with outcome metadata."""
        rows = self._conn.execute(
            """SELECT created_at, duration_ms, metadata
               FROM memory_ops
               WHERE user_id = ? AND operation IN ('synthesize', 'consolidate')
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        results = []
        for r in rows:
            entry = {"created_at": r["created_at"], "duration_ms": r["duration_ms"]}
            try:
                meta = json.loads(r["metadata"]) if r["metadata"] else {}
                entry.update(meta)
            except (json.JSONDecodeError, TypeError):
                pass
            results.append(entry)
        return results

    def get_orphan_memories(self, user_id: str, limit: int = 5) -> list[dict]:
        """Active memories with zero links, oldest first (decay candidates)."""
        rows = self._conn.execute(
            """SELECT m.id, SUBSTR(m.content, 1, 80) as preview, m.created_at
               FROM memories m
               WHERE m.user_id = ? AND m.active = 1
               AND NOT EXISTS (
                   SELECT 1 FROM links l
                   WHERE l.user_id = m.user_id
                   AND (l.source_id = m.id OR l.target_id = m.id)
               )
               AND m.source_event_ids != '["__memex__"]'
               ORDER BY m.created_at ASC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_memory_trends(self, user_id: str, days: int = 7) -> dict:
        """Memory creation, supersession, deactivation trends over N days."""
        created = self._conn.execute(
            """SELECT COUNT(*) FROM memories
               WHERE user_id = ? AND created_at >= datetime('now', ?)""",
            (user_id, f"-{days} days"),
        ).fetchone()[0]

        superseded = self._conn.execute(
            """SELECT COUNT(*) FROM memories
               WHERE user_id = ? AND active = 0 AND superseded_by IS NOT NULL
               AND created_at >= datetime('now', ?)""",
            (user_id, f"-{days} days"),
        ).fetchone()[0]

        deactivated = self._conn.execute(
            """SELECT COUNT(*) FROM memories
               WHERE user_id = ? AND active = 0 AND superseded_by IS NULL
               AND created_at >= datetime('now', ?)""",
            (user_id, f"-{days} days"),
        ).fetchone()[0]

        links_created = self._conn.execute(
            """SELECT COUNT(*) FROM links
               WHERE user_id = ? AND created_at >= datetime('now', ?)""",
            (user_id, f"-{days} days"),
        ).fetchone()[0]

        return {
            "days": days,
            "created": created,
            "superseded": superseded,
            "deactivated": deactivated,
            "net": created - superseded - deactivated,
            "links_created": links_created,
            "links_per_day": round(links_created / days, 1) if days else 0,
        }

    def get_ingestion_staleness(self, user_id: str) -> list[dict]:
        """Per-source: event count, last sync time, staleness."""
        sources = self.get_sources(user_id)
        result = []
        for source in sources:
            count = self.count_events(user_id, source)
            last_sync = self.get_last_sync_timestamp(user_id, source)
            result.append(
                {
                    "source": source,
                    "count": count,
                    "last_sync": last_sync,
                }
            )
        # Sort by count descending
        result.sort(key=lambda x: x["count"], reverse=True)
        return result

    # ===================================================================
    # Status
    # ===================================================================

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

        latest_event_row = self._conn.execute(
            "SELECT MAX(ingested_at) FROM events WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        return {
            "user_id": user_id,
            "total_events": total,
            "sources": source_counts,
            "recent_runs": [dict(r) for r in runs],
            "latest_event_at": latest_event_row[0] if latest_event_row else None,
        }

    # ===================================================================
    # Memories — Layer 2 of the memory architecture
    # ===================================================================

    def insert_memory(self, memory: Memory) -> str:
        """Insert a memory, returning its ID. Syncs to FTS5."""
        now = datetime.now(UTC).isoformat()
        created = memory.created_at.isoformat() if isinstance(memory.created_at, datetime) else now
        self._conn.execute(
            """INSERT INTO memories
               (id, user_id, content, source_event_ids, created_at, updated_at, superseded_by, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory.id,
                memory.user_id,
                memory.content,
                json.dumps(memory.source_event_ids),
                created,
                None,
                memory.superseded_by,
                1 if memory.active else 0,
            ),
        )

        if not self._in_transaction:
            self._conn.commit()
        return memory.id

    def get_memory(self, user_id: str, memory_id: str) -> dict | None:
        """Fetch a single memory by ID, supporting prefix match for short IDs."""
        row = self._conn.execute(
            "SELECT * FROM memories WHERE user_id = ? AND id = ?",
            (user_id, memory_id),
        ).fetchone()
        if row:
            return dict(row)
        if len(memory_id) >= 8 and len(memory_id) < 36:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE user_id = ? AND id LIKE ?",
                (user_id, f"{memory_id}%"),
            ).fetchone()
            return dict(row) if row else None
        return None

    def update_memory(self, user_id: str, memory_id: str, new_content: str) -> bool:
        """Update a memory's content in-place. Returns True if found and updated."""
        now = datetime.now(UTC).isoformat()
        cursor = self._conn.execute(
            "UPDATE memories SET content = ?, updated_at = ? "
            "WHERE user_id = ? AND id = ? AND active = 1",
            (new_content, now, user_id, memory_id),
        )
        if cursor.rowcount == 0:
            return False

        if not self._in_transaction:
            self._conn.commit()
        return True

    def supersede_memory(self, user_id: str, old_id: str, new_memory: Memory) -> str:
        """Replace a memory with a newer version (old version deactivated, pointer set).

        Old memory gets superseded_by pointer and is deactivated.
        New memory is inserted and indexed. Returns new memory ID.
        """
        with self.transaction():
            new_id = self.insert_memory(new_memory)
            self._conn.execute(
                "UPDATE memories SET superseded_by = ?, active = 0 WHERE user_id = ? AND id = ?",
                (new_id, user_id, old_id),
            )
        return new_id

    def deactivate_memory(self, user_id: str, memory_id: str) -> bool:
        """Deactivate (decay) a memory. Returns True if found and deactivated."""
        cursor = self._conn.execute(
            "UPDATE memories SET active = 0 WHERE user_id = ? AND id = ? AND active = 1",
            (user_id, memory_id),
        )
        if cursor.rowcount == 0:
            return False
        if not self._in_transaction:
            self._conn.commit()
        return True

    def get_memory_chain(self, user_id: str, memory_id: str) -> list[dict]:
        """Get the full supersession chain for a memory (oldest first).

        Walks backward from the given ID to find the root, then forward
        to the latest version. Returns the complete evolution history.
        """
        # Walk backward to find the root
        current = memory_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            row = self._conn.execute(
                "SELECT id FROM memories WHERE user_id = ? AND superseded_by = ?",
                (user_id, current),
            ).fetchone()
            if row:
                current = row[0]
            else:
                break

        # Walk forward from root
        chain: list[dict] = []
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            row = self._conn.execute(
                "SELECT * FROM memories WHERE user_id = ? AND id = ?",
                (user_id, current),
            ).fetchone()
            if row:
                d = dict(row)
                chain.append(d)
                current = d.get("superseded_by")
            else:
                break

        return chain

    def search_memories(self, user_id: str, query: str, limit: int = 20) -> list[dict]:
        """FTS5/BM25 search over active memories, with ID prefix fallback.

        Returns memories ranked by relevance. Lower rank = better match.
        Falls back to ID prefix match if query looks like a UUID fragment and FTS returns nothing.
        """
        if not query.strip():
            return []

        _uuid_like = len(query) >= 8 and all(c in "0123456789abcdef-" for c in query.lower())

        if not _uuid_like:
            rows = self._conn.execute(
                """SELECT m.*, bm25(memories_fts) as rank
                   FROM memories_fts fts
                   JOIN memories m ON m.id = fts.memory_id
                   WHERE memories_fts MATCH ? AND m.user_id = ? AND m.active = 1
                   ORDER BY rank
                   LIMIT ?""",
                (query, user_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

        mem = self.get_memory(user_id, query)
        if mem:
            return [mem]

        rows = self._conn.execute(
            """SELECT m.*, bm25(memories_fts) as rank
               FROM memories_fts fts
               JOIN memories m ON m.id = fts.memory_id
               WHERE memories_fts MATCH ? AND m.user_id = ? AND m.active = 1
               ORDER BY rank
               LIMIT ?""",
            (query, user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_recent_memories(self, user_id: str, limit: int = 20) -> list[dict]:
        """Get most recent active memories, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE user_id = ? AND active = 1 "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def count_memories(self, user_id: str, active_only: bool = True) -> int:
        """Count memories for a user."""
        if active_only:
            return self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id = ? AND active = 1",
                (user_id,),
            ).fetchone()[0]
        return self._conn.execute(
            "SELECT COUNT(*) FROM memories WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]

    def get_memex(self, user_id: str) -> dict | None:
        """Get the memex memory for a user.

        Convention: memex memory has source_event_ids = '["__memex__"]'.
        Returns the most recent active memex, or None.
        """
        row = self._conn.execute(
            "SELECT * FROM memories "
            "WHERE user_id = ? AND active = 1 AND source_event_ids = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id, json.dumps(["__memex__"])),
        ).fetchone()
        return dict(row) if row else None

    # ===================================================================
    # Links — Layer 2b (sparse connections)
    # ===================================================================

    def insert_link(self, link: Link) -> str:
        """Insert a link between two memories, returning its ID."""
        created = (
            link.created_at.isoformat()
            if isinstance(link.created_at, datetime)
            else datetime.now(UTC).isoformat()
        )
        self._conn.execute(
            """INSERT INTO links (id, user_id, source_id, target_id, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                link.id,
                link.user_id,
                link.source_id,
                link.target_id,
                link.reason,
                created,
            ),
        )
        self._conn.commit()
        return link.id

    def get_links_for(self, user_id: str, memory_id: str) -> list[dict]:
        """Get all links connected to a memory (both directions)."""
        rows = self._conn.execute(
            """SELECT * FROM links
               WHERE user_id = ? AND (source_id = ? OR target_id = ?)
               ORDER BY created_at DESC""",
            (user_id, memory_id, memory_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_linked_memories(self, user_id: str, memory_id: str) -> list[dict]:
        """Get all active memories linked to a given memory, with link reasons.

        Follows links in both directions. Returns the linked memory plus
        the link reason and link ID.
        """
        rows = self._conn.execute(
            """SELECT m.*, l.reason as link_reason, l.id as link_id
               FROM links l
               JOIN memories m ON (
                   (l.source_id = ? AND m.id = l.target_id) OR
                   (l.target_id = ? AND m.id = l.source_id)
               )
               WHERE l.user_id = ? AND m.active = 1
               ORDER BY l.created_at DESC""",
            (memory_id, memory_id, user_id),
        ).fetchall()
        return [dict(row) for row in rows]

    # ===================================================================
    # Memory operations log (audit trail + synthesis gating)
    # ===================================================================

    def log_memory_op(
        self,
        user_id: str,
        operation: str,
        *,
        input_summary: str = "",
        output_summary: str = "",
        memory_ids: list[str] | None = None,
        duration_ms: int | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Log a memory operation (audit trail, used for synthesis gating).

        Every operation is recorded: add, link, update, retrieve, compact,
        consolidate. These logs track memory operations for debugging and synthesis gating.
        """
        op_id = str(uuid7())
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT INTO memory_ops
               (id, user_id, operation, input_summary, output_summary,
                memory_ids, created_at, duration_ms, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                op_id,
                user_id,
                operation,
                input_summary,
                output_summary,
                json.dumps(memory_ids or []),
                now,
                duration_ms,
                json.dumps(metadata or {}),
            ),
        )
        self._conn.commit()
        return op_id

    def get_memory_ops(
        self, user_id: str, limit: int = 100, operation: str | None = None
    ) -> list[dict]:
        """Get memory operations log. Useful for debugging memory operations."""
        if operation:
            rows = self._conn.execute(
                "SELECT * FROM memory_ops WHERE user_id = ? AND operation = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, operation, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM memory_ops WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_last_synthesis_timestamp(self, user_id: str) -> str | None:
        """Return timestamp of most recent synthesis op, or None."""
        row = self._conn.execute(
            "SELECT created_at FROM memory_ops "
            "WHERE user_id = ? AND operation IN ('synthesize', 'consolidate') "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return row[0] if row else None

    def get_synthesis_cursor(self, user_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT last_event_id FROM synthesis_cursor WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row[0] if row else None

    def set_synthesis_cursor(self, user_id: str, last_event_id: str) -> None:
        self._conn.execute(
            """INSERT INTO synthesis_cursor (user_id, last_event_id, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                   last_event_id = excluded.last_event_id,
                   updated_at = datetime('now')""",
            (user_id, last_event_id),
        )
        self._conn.commit()

    # ===================================================================
    # Cycle Records
    # ===================================================================

    def insert_cycle_record(
        self,
        user_id: str,
        *,
        cursor_start: str | None = None,
        skill_hash: str | None = None,
        prompt_hash: str | None = None,
        model: str | None = None,
    ) -> str:
        cycle_id = str(uuid7())
        started_at = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT INTO cycle_records
               (id, user_id, started_at, cursor_start, skill_hash, prompt_hash, model, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'running')""",
            (cycle_id, user_id, started_at, cursor_start, skill_hash, prompt_hash, model),
        )
        if not self._in_transaction:
            self._conn.commit()
        return cycle_id

    def complete_cycle_record(
        self,
        cycle_id: str,
        *,
        status: str = "completed",
        cursor_end: str | None = None,
        events_processed: int = 0,
        memories_created: int = 0,
        memories_updated: int = 0,
        links_created: int = 0,
        memex_updated: int = 0,
        cost_usd: float = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        duration_ms: int = 0,
    ) -> None:
        completed_at = datetime.now(UTC).isoformat()
        self._conn.execute(
            """UPDATE cycle_records SET
               completed_at = ?, cursor_end = ?, status = ?,
               events_processed = ?, memories_created = ?,
               memories_updated = ?, links_created = ?, memex_updated = ?,
               cost_usd = ?, input_tokens = ?, output_tokens = ?,
               cache_read_tokens = ?, duration_ms = ?
               WHERE id = ?""",
            (
                completed_at,
                cursor_end,
                status,
                events_processed,
                memories_created,
                memories_updated,
                links_created,
                memex_updated,
                cost_usd,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                duration_ms,
                cycle_id,
            ),
        )
        if not self._in_transaction:
            self._conn.commit()

    def insert_cycle_annotation(
        self,
        cycle_id: str,
        annotator: str,
        annotation_type: str,
        content: str,
    ) -> str:
        ann_id = str(uuid7())
        self._conn.execute(
            """INSERT INTO cycle_annotations (id, cycle_id, annotator, annotation_type, content)
               VALUES (?, ?, ?, ?, ?)""",
            (ann_id, cycle_id, annotator, annotation_type, content),
        )
        if not self._in_transaction:
            self._conn.commit()
        return ann_id

    def get_cycle_records(self, user_id: str, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM cycle_records WHERE user_id = ? ORDER BY started_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    # ===================================================================
    # Lifecycle
    # ===================================================================

    def close(self) -> None:
        self._conn.close()
