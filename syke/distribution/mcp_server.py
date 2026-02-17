"""FastMCP server — makes Syke consumable by Claude Code and other MCP clients."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from syke.config import user_db_path
from syke.db import SykeDB
from syke.distribution.formatters import format_profile


def _summarize_events(events: list[dict]) -> list[dict]:
    """Keep only core fields, strip content + metadata."""
    return [{
        "id": e["id"],
        "source": e["source"],
        "timestamp": e["timestamp"],
        "event_type": e["event_type"],
        "title": e["title"],
        "content_length": len(e.get("content") or ""),
    } for e in events]


def create_server(user_id: str) -> FastMCP:
    """Create an MCP server for a specific user."""
    mcp = FastMCP(
        "syke",
        instructions=(
            f"Syke — personal context for {user_id}. "
            f"Query the user's cross-platform digital footprint and identity profile.\n\n"
            f"## How to Query Syke\n\n"
            f"**RECOMMENDED: Use ask() for natural language queries:**\n"
            f"- ask(\"What is the user working on?\")\n"
            f"- ask(\"What did they do last week?\")\n"
            f"- ask(\"What do they think about AI agents?\")\n\n"
            f"The ask() tool intelligently explores the timeline using agentic search. "
            f"It requires ANTHROPIC_API_KEY to be set. Cost: ~$0.02/call. "
            f"If unavailable, use the data tools below.\n\n"
            f"**Data tools** (zero cost, always available):\n"
            f"- get_profile() — Identity profile with active threads\n"
            f"- query_timeline(since, source) — Events by date/source\n"
            f"- search_events(query) — Keyword search across events\n"
            f"- get_event(event_id) — Full content for a single event\n"
            f"- get_manifest() — Data statistics and freshness\n"
            f"- push_event() / push_events() — Write new events\n\n"
            f"## First Session (No Profile Yet)\n\n"
            f"If get_profile() returns an error, the profile hasn't been built yet. "
            f"Timeline data tools (query_timeline, search_events, get_event) work immediately — "
            f"use them to answer questions about the user.\n\n"
            f"To build the profile, the user (or agent) should run in a standalone terminal:\n"
            f"  syke sync --rebuild\n\n"
            f"This requires ANTHROPIC_API_KEY. It cannot run inside Claude Code (Agent SDK limitation). "
            f"Once built, get_profile() and ask() become available.\n\n"
            f"## Federated Push\n\n"
            f"When something meaningful happens in this session, call push_event to record it:\n"
            f"- User starts working on a new project or topic\n"
            f"- A decision is made (architecture, tool choice, direction change)\n"
            f"- User expresses a preference, frustration, or insight\n"
            f"- A task is completed (feature shipped, bug fixed, PR merged)\n"
            f"- User mentions a person, company, or relationship\n\n"
            f"Use source='claude-code', event_type='observation', and a descriptive title. "
            f"Keep content concise (1-3 sentences). Use external_id to prevent duplicates "
            f"(e.g., 'session-{{session_id}}-topic'). Don't push every message — push the signal, not the noise."
        ),
    )

    # Cache DB connection for the server lifetime (one per MCP session)
    _db: SykeDB | None = None

    def _get_db() -> SykeDB:
        nonlocal _db
        if _db is None:
            _db = SykeDB(user_db_path(user_id))
            _db.initialize()
        return _db

    @mcp.tool()
    def get_profile(format: str = "json") -> str:
        """Get the user's identity profile.

        Args:
            format: Output format — json, markdown, claude-md, or user-md
        """
        db = _get_db()
        profile = db.get_latest_profile(user_id)
        if not profile:
            return json.dumps({"error": "No profile found. Run: syke setup"})
        try:
            return format_profile(profile, format)
        except ValueError as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def query_timeline(
        since: str | None = None,
        source: str | None = None,
        limit: int = 50,
        summary: bool = True,
    ) -> str:
        """Query the user's event timeline.

        Returns events filtered by date and/or source platform. Use summary=False
        to include full content. For natural language questions, prefer ask().

        Args:
            since: ISO date to filter from (e.g., "2025-01-01")
            source: Filter by source platform (chatgpt, github, gmail, claude-code)
            limit: Max events to return (default 50)
            summary: Return summary view (default true, strips content)
        """
        db = _get_db()
        events = db.get_events(user_id, source=source, since=since, limit=limit)
        if summary:
            events = _summarize_events(events)
        return json.dumps(events, indent=2, default=str)

    @mcp.tool()
    def get_manifest() -> str:
        """Get a summary of all ingested data — sources, event counts, and profile status."""
        db = _get_db()
        status = db.get_status(user_id)

        profile_ts = db.get_last_profile_timestamp(user_id)
        if profile_ts:
            from datetime import datetime, UTC
            try:
                profile_dt = datetime.fromisoformat(profile_ts.replace("Z", "+00:00"))
                age_hours = (datetime.now(UTC) - profile_dt).total_seconds() / 3600
                status["profile_age_hours"] = round(age_hours, 1)
                status["profile_fresh"] = age_hours < 24
            except (ValueError, TypeError):
                pass
            status["events_since_profile"] = db.count_events_since(user_id, profile_ts)

        # Profile cost stats from profiles table
        cost_stats = db.get_perception_cost_stats(user_id)
        if cost_stats:
            status["profile_costs"] = cost_stats

        return json.dumps(status, indent=2, default=str)

    @mcp.tool()
    def get_event(event_id: str) -> str:
        """Fetch full content for a single event by ID.

        Use this after search_events or query_timeline to read the complete
        content of a specific event without the cost of an ask() call.

        Args:
            event_id: The event ID to fetch
        """
        db = _get_db()
        event = db.get_event_by_id(user_id, event_id)
        if event is None:
            return json.dumps({"error": f"Event not found: {event_id}"})
        return json.dumps(event, indent=2, default=str)

    @mcp.tool()
    def search_events(query: str, limit: int = 20, summary: bool = True) -> str:
        """Search across all events by keyword.

        Returns events matching the query in titles and content.
        Use get_event(id) to fetch full content for a specific result.
        For natural language questions, prefer ask().

        Args:
            query: Search term to find in event titles and content
            limit: Max results to return (default 20)
            summary: Return summary view (default true, strips content)
        """
        db = _get_db()
        events = db.search_events(user_id, query, limit)
        if summary:
            events = _summarize_events(events)
        return json.dumps(events, indent=2, default=str)

    @mcp.tool()
    def push_event(
        source: str,
        event_type: str,
        title: str,
        content: str,
        timestamp: str | None = None,
        metadata: str | None = None,
        external_id: str | None = None,
    ) -> str:
        """Push a raw event to Syke's timeline.

        Args:
            source: Platform name (e.g., "claude-code", "notes", "slack")
            event_type: Event category (e.g., "conversation", "observation", "task")
            title: Short title for the event
            content: Full event content
            timestamp: ISO timestamp (defaults to now)
            metadata: JSON string of extra metadata
            external_id: Source-provided dedup key (prevents duplicate pushes)
        """
        from syke.ingestion.gateway import IngestGateway

        db = _get_db()
        try:
            meta = json.loads(metadata) if metadata else None
        except (json.JSONDecodeError, TypeError) as e:
            return json.dumps({"status": "error", "error": f"Invalid metadata JSON: {e}"})
        if meta is not None and not isinstance(meta, dict):
            return json.dumps({"status": "error", "error": f"metadata must be a JSON object, got {type(meta).__name__}"})
        gateway = IngestGateway(db, user_id)
        result = gateway.push(
            source=source,
            event_type=event_type,
            title=title,
            content=content,
            timestamp=timestamp,
            metadata=meta,
            external_id=external_id,
        )
        return json.dumps(result)

    @mcp.tool()
    def push_events(events_json: str) -> str:
        """Push multiple events to Syke's timeline in a single call.

        Args:
            events_json: JSON array of event objects, each with: source, event_type, title, content, and optional timestamp, metadata, external_id
        """
        from syke.ingestion.gateway import IngestGateway

        db = _get_db()
        try:
            events = json.loads(events_json)
        except (json.JSONDecodeError, TypeError) as e:
            return json.dumps({"status": "error", "error": f"Invalid JSON: {e}"})
        if not isinstance(events, list):
            return json.dumps({"status": "error", "error": "events_json must be a JSON array"})
        gateway = IngestGateway(db, user_id)
        result = gateway.push_batch(events)
        return json.dumps(result)

    @mcp.tool()
    async def ask(question: str) -> str:
        """PRIMARY TOOL — Ask any question about the user.

        Syke explores its timeline using agentic reasoning and returns a precise answer.
        Use this for ANY question about the user — it's smarter than direct queries.

        Examples:
        - "What is their current main project?"
        - "What did they work on last week?"
        - "What do they think about RAG architectures?"
        - "Who are they collaborating with?"
        - "What's their approach to AI safety?"

        Args:
            question: Natural language question about the user
        """
        from syke.distribution.ask_agent import _run_ask
        return await _run_ask(_get_db(), user_id, question)

    return mcp
