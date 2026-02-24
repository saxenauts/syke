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
from syke.models import Memory, UserProfile

log = logging.getLogger(__name__)

MEMEX_MARKER = ["__memex__"]


def get_memex(db: SykeDB, user_id: str) -> dict | None:
    return db.get_memex(user_id)


def bootstrap_memex_from_profile(db: SykeDB, user_id: str) -> str | None:
    """Convert the existing UserProfile into the initial memex memory.

    One-time bootstrap: reads the latest profile and creates a memex
    memory from it. Returns the memex memory ID, or None if no profile exists.

    Skips if a memex already exists.
    """
    existing = db.get_memex(user_id)
    if existing:
        log.info("Memex already exists for %s, skipping bootstrap", user_id)
        return existing["id"]

    profile = db.get_latest_profile(user_id)
    if not profile:
        log.info("No profile found for %s, cannot bootstrap memex", user_id)
        return None

    content = _profile_to_memex_content(profile)
    memory = Memory(
        id=str(uuid7()),
        user_id=user_id,
        content=content,
        source_event_ids=MEMEX_MARKER,
    )
    db.insert_memory(memory)
    db.log_memory_op(
        user_id,
        "synthesize",
        input_summary="bootstrap from profile",
        output_summary=f"created memex {memory.id}",
        memory_ids=[memory.id],
    )
    log.info(
        "Bootstrapped memex %s for %s (%d chars)", memory.id, user_id, len(content)
    )
    return memory.id


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
    if memex:
        return memex["content"]

    # Auto-bootstrap: if profile exists but no memex, create it now
    profile = db.get_latest_profile(user_id)
    if profile:
        bootstrap_memex_from_profile(db, user_id)
        memex = db.get_memex(user_id)
        if memex:
            return memex["content"]

    mem_count = db.count_memories(user_id)
    event_count = db.count_events(user_id)
    if mem_count > 0:
        return (
            f"[No memex yet. {mem_count} memories and {event_count} events available. "
            f"Use search_memories and search_evidence to explore.]"
        )
    if event_count > 0:
        return (
            f"[No memories yet. {event_count} raw events available. "
            f"Use search_evidence to explore raw events.]"
        )
    return "[No data yet.]"


def _profile_to_memex_content(profile: UserProfile) -> str:
    """Convert a UserProfile into memex content text."""
    sections = []

    sections.append(f"# Memex — {profile.user_id}")
    sections.append("")
    sections.append(f"## Identity")
    sections.append(profile.identity_anchor)

    if profile.active_threads:
        sections.append("")
        sections.append("## What's Active")
        for thread in profile.active_threads:
            intensity = f" [{thread.intensity}]" if thread.intensity else ""
            platforms = f" ({', '.join(thread.platforms)})" if thread.platforms else ""
            sections.append(
                f"- **{thread.name}**{intensity}{platforms}: {thread.description}"
            )
            if thread.recent_signals:
                for signal in thread.recent_signals[:3]:
                    sections.append(f"  - {signal}")

    if profile.world_state:
        sections.append("")
        sections.append("## Context")
        sections.append(profile.world_state)

    if profile.recent_detail:
        sections.append("")
        sections.append("## Recent Context")
        sections.append(profile.recent_detail)

    if profile.background_context:
        sections.append("")
        sections.append("## Background")
        sections.append(profile.background_context)

    if profile.voice_patterns:
        sections.append("")
        sections.append("## Voice")
        sections.append(f"Tone: {profile.voice_patterns.tone}")
        if profile.voice_patterns.communication_style:
            sections.append(f"Style: {profile.voice_patterns.communication_style}")

    sections.append("")
    sections.append(f"---")
    sections.append(
        f"Sources: {', '.join(profile.sources)}. Events: {profile.events_count}."
    )

    return "\n".join(sections)
