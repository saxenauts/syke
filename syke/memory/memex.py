"""Memex — the agent's map of the user.

A special memory that acts as the first thing any agent reads.
It's a navigable map: stable things anchor it, active things show movement,
context grounds it. Over time it gets smarter as retrieval paths emerge.
Convention: memex memories have source_event_ids = ["__memex__"].
"""

from __future__ import annotations

import logging

from uuid_extensions import uuid7

from syke.db import SykeDB
from syke.models import Memory

log = logging.getLogger(__name__)

MEMEX_MARKER = ["__memex__"]


def _strip_projection_header(content: str) -> str:
    lines = content.split("\n")
    if lines and lines[0].startswith("# MEMEX ["):
        return "\n".join(lines[1:]).lstrip("\n")
    return content


def get_memex(db: SykeDB, user_id: str) -> dict[str, object] | None:
    return db.get_memex(user_id)


def update_memex(db: SykeDB, user_id: str, new_content: str) -> str:
    """Update the memex with new content via supersede.

    Old active memex rows are deactivated, new one created. Returns new memex ID.
    """
    canonical_content = _strip_projection_header(new_content)
    marker = '["__memex__"]'
    active_rows = db.conn.execute(
        """SELECT id, content FROM memories
           WHERE user_id = ? AND active = 1 AND source_event_ids = ?
           ORDER BY datetime(created_at) DESC, id DESC""",
        (user_id, marker),
    ).fetchall()
    existing = dict(active_rows[0]) if active_rows else None
    if existing and existing["content"] == canonical_content:
        stale_ids = [row["id"] for row in active_rows[1:]]
        if stale_ids:
            placeholders = ",".join("?" for _ in stale_ids)
            with db.transaction():
                db.conn.execute(
                    f"""UPDATE memories
                        SET superseded_by = ?, active = 0
                        WHERE user_id = ? AND id IN ({placeholders})""",
                    (existing["id"], user_id, *stale_ids),
                )
            db.log_memory_op(
                user_id,
                "synthesize",
                input_summary="memex duplicate cleanup",
                output_summary=(
                    f"kept memex {existing['id']}; deactivated {len(stale_ids)} stale active row(s)"
                ),
                memory_ids=[str(existing["id"]), *[str(memory_id) for memory_id in stale_ids]],
            )
        return str(existing["id"])

    new_memory = Memory(
        id=str(uuid7()),
        user_id=user_id,
        content=canonical_content,
        source_event_ids=MEMEX_MARKER,
    )

    with db.transaction():
        new_id = db.insert_memory(new_memory)
        if active_rows:
            old_ids = [row["id"] for row in active_rows]
            placeholders = ",".join("?" for _ in old_ids)
            db.conn.execute(
                f"""UPDATE memories
                    SET superseded_by = ?, active = 0
                    WHERE user_id = ? AND id IN ({placeholders})""",
                (new_id, user_id, *old_ids),
            )

    db.log_memory_op(
        user_id,
        "synthesize",
        input_summary="memex update",
        output_summary=f"new memex {new_id}",
        memory_ids=[new_id],
    )
    return new_id


def get_memex_for_injection(
    db: SykeDB,
    user_id: str,
    *,
    context: str = "ask",
) -> str:
    """Get memex content formatted for system prompt injection.

    Returns the memex content if it exists, or a minimal fallback
    with memory stats so the agent knows what's available.

    `context` controls the empty-memex fallback:
      - "ask" (default): user-facing placeholder explaining first-run state
      - "synthesis": returns empty string so the agent builds from scratch
        without echoing the placeholder into its output.
    """
    memex = db.get_memex(user_id)
    content = ""

    if memex:
        content = memex["content"]
    else:
        # Synthesis context never wants user-facing placeholder text.
        # The placeholder is an ask-path UX affordance — in synthesis it
        # leaks into the prompt and the agent literally echoes it instead
        # of doing its work. Callers pass context="synthesis" to opt out.
        if context == "synthesis":
            return ""
        mem_count = db.count_memories(user_id)
        if mem_count > 0:
            return (
                f"[No memex yet. {mem_count} memories are available in Syke's canonical database.]"
            )
        return (
            "[First run — no memories yet.]\n\n"
            "Synthesis hasn't completed its first cycle. You can still help:\n"
            "- Read adapter markdowns in `adapters/` to discover what harness data exists.\n"
            "- Explore harness directories directly — the data is there, "
            "the memex just hasn't mapped it yet.\n"
            "- If the user records something (`syke record`), answer from that.\n"
            "- Do not guess a wait time. Tell the user setup is complete but "
            "MEMEX is not ready yet; they can run `syke sync`, check "
            "`syke status --json`, or keep working while the daemon builds it."
        )

    return content
