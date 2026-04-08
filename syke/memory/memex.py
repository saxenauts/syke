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


def get_memex(db: SykeDB, user_id: str) -> dict[str, object] | None:
    return db.get_memex(user_id)


def update_memex(db: SykeDB, user_id: str, new_content: str) -> str:
    """Update the memex with new content via supersede.

    Old memex is deactivated, new one created. Returns new memex ID.
    """
    existing = db.get_memex(user_id)
    new_memory = Memory(
        id=str(uuid7()),
        user_id=user_id,
        content=new_content,
        source_event_ids=MEMEX_MARKER,
    )

    if existing:
        new_id = db.supersede_memory(user_id, existing["id"], new_memory)
    else:
        new_id = db.insert_memory(new_memory)

    db.log_memory_op(
        user_id,
        "synthesize",
        input_summary="memex update",
        output_summary=f"new memex {new_id}",
        memory_ids=[new_id],
    )
    return new_id


def get_memex_for_injection(db: SykeDB, user_id: str) -> str:
    """Get memex content formatted for system prompt injection.

    Returns the memex content if it exists, or a minimal fallback
    with memory stats so the agent knows what's available.
    """
    memex = db.get_memex(user_id)
    content = ""

    if memex:
        content = memex["content"]
    else:
        mem_count = db.count_memories(user_id)
        event_count = db.count_events(user_id)
        if mem_count > 0:
            return (
                f"[No memex yet. {mem_count} memories and {event_count} events are available "
                "in Syke's canonical database.]"
            )
        if event_count > 0:
            return (
                f"[No memories yet. {event_count} raw events are available in Syke's "
                "canonical database.]"
            )
        return (
            "[First run — no memories yet.]\n\n"
            "Synthesis hasn't completed its first cycle. You can still help:\n"
            "- Read adapter markdowns in `adapters/` to discover what harness data exists.\n"
            "- Explore harness directories directly — the data is there, the memex just hasn't mapped it yet.\n"
            "- If the user records something (`syke record`), answer from that.\n"
            "- Tell the user synthesis is building their memex and it will be ready within ~15 minutes."
        )

    return content
