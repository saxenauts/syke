"""SQLite schema + queries."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from uuid_extensions import uuid7

from syke.models import Link, Memory

# Migrations applied after initial schema creation.
# CONTRIBUTOR INVARIANT: all migrations must be additive-only (ALTER TABLE ADD COLUMN,
# CREATE INDEX IF NOT EXISTS), idempotent, and never destructive. Never DROP, RENAME,
# or modify existing columns or rows. OperationalError "already exists" / "duplicate column"
# is caught and treated as a no-op — this is expected and correct behavior.
_MEMORY_MIGRATIONS = [
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
    # --- Canonical rollout traces (ask / synthesis / daemon_cycle) ---
    (
        """CREATE TABLE IF NOT EXISTS rollout_traces (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            input_text TEXT,
            output_text TEXT NOT NULL DEFAULT '',
            thinking TEXT DEFAULT '[]',
            transcript TEXT DEFAULT '[]',
            tool_calls TEXT DEFAULT '[]',
            duration_ms INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,
            num_turns INTEGER DEFAULT 0,
            tool_calls_count INTEGER DEFAULT 0,
            tool_name_counts TEXT DEFAULT '{}',
            provider TEXT,
            model TEXT,
            response_id TEXT,
            stop_reason TEXT,
            transport TEXT,
            runtime_reused INTEGER,
            runtime TEXT DEFAULT '{}',
            extras TEXT DEFAULT '{}'
        )""",
        "rollout_traces_table",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_rollout_traces_user_time "
        "ON rollout_traces(user_id, completed_at DESC)",
        "rollout_traces_user_time_idx",
    ),
    (
        "CREATE INDEX IF NOT EXISTS idx_rollout_traces_user_kind_time "
        "ON rollout_traces(user_id, kind, completed_at DESC)",
        "rollout_traces_user_kind_time_idx",
    ),
    # --- cycle_records schema drift fix (columns added after initial CREATE TABLE) ---
    ("ALTER TABLE cycle_records ADD COLUMN cost_usd REAL DEFAULT 0", "cycle_records_cost_usd_col"),
    (
        "ALTER TABLE cycle_records ADD COLUMN input_tokens INTEGER DEFAULT 0",
        "cycle_records_input_tokens_col",
    ),
    (
        "ALTER TABLE cycle_records ADD COLUMN output_tokens INTEGER DEFAULT 0",
        "cycle_records_output_tokens_col",
    ),
    (
        "ALTER TABLE cycle_records ADD COLUMN cache_read_tokens INTEGER DEFAULT 0",
        "cycle_records_cache_read_col",
    ),
    (
        "ALTER TABLE cycle_records ADD COLUMN duration_ms INTEGER DEFAULT 0",
        "cycle_records_duration_ms_col",
    ),
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


class SykeDB:
    """SQLite wrapper for the Syke timeline database."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        auto_initialize: bool = True,
    ):
        if not isinstance(db_path, (str, os.PathLike)):
            raise TypeError(f"SykeDB(db_path) expects a path-like value, got {type(db_path)!r}")
        path_str = os.fspath(db_path)
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
                f"Use user_syke_db_path(user_id) to get the correct path."
            )
        self.db_path = path_str
        self._conn = self._connect_db(self.db_path)
        self._in_transaction = False
        if auto_initialize:
            self.initialize()

    @staticmethod
    def _connect_db(db_path: str) -> sqlite3.Connection:
        if db_path != ":memory:":
            Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

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
        """Atomic write: all inserts succeed or all roll back.

        Re-entrant: if already inside a transaction, inner calls pass
        through and the outermost transaction controls commit/rollback.
        """
        if self._in_transaction:
            yield  # nested — outermost transaction owns the commit
            return

        connections = self._unique_connections()
        for conn in connections:
            if conn.in_transaction:
                conn.commit()
        for conn in connections:
            conn.execute("BEGIN IMMEDIATE")
        self._in_transaction = True
        try:
            yield
            for conn in connections:
                conn.commit()
        except BaseException:
            for conn in reversed(connections):
                conn.rollback()
            raise
        finally:
            self._in_transaction = False

    def initialize(self) -> None:
        """Create tables and indexes, then apply migrations."""
        self._migrate(self._conn, _MEMORY_MIGRATIONS)

    def _migrate(
        self,
        conn: sqlite3.Connection,
        migrations: list[tuple[str, str]],
    ) -> None:
        """Apply schema migrations safely (idempotent)."""
        for sql, _label in migrations:
            try:
                conn.execute(sql)
                conn.commit()
            except sqlite3.OperationalError as e:
                if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                    pass  # Expected: column/index already present
                else:
                    raise

    def _unique_connections(self) -> list[sqlite3.Connection]:
        return [self._conn]

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
        if not self._in_transaction:
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
        if not self._in_transaction:
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

    # ===================================================================
    # Rollout traces — canonical self-observation records
    # ===================================================================

    def get_last_synthesis_timestamp(self, user_id: str) -> str | None:
        """Return timestamp of most recent synthesis op, or None."""
        row = self._conn.execute(
            "SELECT created_at FROM memory_ops "
            "WHERE user_id = ? AND operation IN ('synthesize', 'consolidate') "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return row[0] if row else None

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
        memories_created: int = 0,
        memories_updated: int = 0,
        links_created: int = 0,
        memex_updated: int = 0,
        cost_usd: float = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        duration_ms: int = 0,
        completed_at_override: str | None = None,
    ) -> None:
        completed_at = completed_at_override or datetime.now(UTC).isoformat()
        self._conn.execute(
            """UPDATE cycle_records SET
               completed_at = ?, cursor_end = ?, status = ?,
               memories_created = ?,
               memories_updated = ?, links_created = ?, memex_updated = ?,
               cost_usd = ?, input_tokens = ?, output_tokens = ?,
               cache_read_tokens = ?, duration_ms = ?
               WHERE id = ?""",
            (
                completed_at,
                cursor_end,
                status,
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

    def insert_rollout_trace(
        self,
        *,
        trace_id: str,
        user_id: str,
        kind: str,
        started_at: str,
        completed_at: str,
        status: str,
        error: str | None = None,
        input_text: str | None = None,
        output_text: str = "",
        thinking: list[dict] | list[str] | None = None,
        transcript: list[dict] | None = None,
        tool_calls: list[dict] | None = None,
        duration_ms: int = 0,
        cost_usd: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        num_turns: int = 0,
        tool_calls_count: int = 0,
        tool_name_counts: dict[str, int] | None = None,
        provider: str | None = None,
        model: str | None = None,
        response_id: str | None = None,
        stop_reason: str | None = None,
        transport: str | None = None,
        runtime_reused: bool | None = None,
        runtime: dict | None = None,
        extras: dict | None = None,
    ) -> str:
        self._conn.execute(
            """INSERT OR REPLACE INTO rollout_traces (
                id, user_id, kind, started_at, completed_at, status, error,
                input_text, output_text, thinking, transcript, tool_calls,
                duration_ms, cost_usd, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, num_turns, tool_calls_count,
                tool_name_counts, provider, model, response_id, stop_reason,
                transport, runtime_reused, runtime, extras
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trace_id,
                user_id,
                kind,
                started_at,
                completed_at,
                status,
                error,
                input_text,
                output_text,
                json.dumps(thinking or []),
                json.dumps(transcript or []),
                json.dumps(tool_calls or []),
                int(duration_ms or 0),
                float(cost_usd or 0.0),
                int(input_tokens or 0),
                int(output_tokens or 0),
                int(cache_read_tokens or 0),
                int(cache_write_tokens or 0),
                int(num_turns or 0),
                int(tool_calls_count or 0),
                json.dumps(tool_name_counts or {}),
                provider,
                model,
                response_id,
                stop_reason,
                transport,
                1 if runtime_reused is True else 0 if runtime_reused is False else None,
                json.dumps(runtime or {}),
                json.dumps(extras or {}),
            ),
        )
        if not self._in_transaction:
            self._conn.commit()
        return trace_id

    def get_rollout_traces(
        self,
        user_id: str,
        *,
        kind: str | None = None,
        limit: int | None = 100,
    ) -> list[dict]:
        query = "SELECT * FROM rollout_traces WHERE user_id = ?"
        params: list[object] = [user_id]
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY completed_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            item["version"] = 1
            for key in (
                "thinking",
                "transcript",
                "tool_calls",
                "tool_name_counts",
                "runtime",
                "extras",
            ):
                raw = item.get(key)
                if isinstance(raw, str):
                    try:
                        item[key] = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        item[key] = [] if key in {"thinking", "transcript", "tool_calls"} else {}
            item["metrics"] = {
                "duration_ms": int(item.get("duration_ms") or 0),
                "cost_usd": float(item.get("cost_usd") or 0.0),
                "input_tokens": int(item.get("input_tokens") or 0),
                "output_tokens": int(item.get("output_tokens") or 0),
                "cache_read_tokens": int(item.get("cache_read_tokens") or 0),
                "cache_write_tokens": int(item.get("cache_write_tokens") or 0),
            }
            item["run_id"] = item.get("id")
            result.append(item)
        return result

    # ===================================================================
    # Lifecycle
    # ===================================================================

    def close(self) -> None:
        self._conn.close()
