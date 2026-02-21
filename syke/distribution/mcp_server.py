"""FastMCP server — makes Syke consumable by Claude Code and other MCP clients.

v0.3.5: Three-verb interface (get_live_context, ask, record) + data tools.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from syke.config import user_data_dir, user_db_path
from syke.db import SykeDB
from syke.distribution.formatters import format_profile

logger = logging.getLogger(__name__)


def _summarize_events(events: list[dict]) -> list[dict]:
    """Keep only core fields, strip content + metadata."""
    return [
        {
            "id": e["id"],
            "source": e["source"],
            "timestamp": e["timestamp"],
            "event_type": e["event_type"],
            "title": e["title"],
            "content_length": len(e.get("content") or ""),
        }
        for e in events
    ]


def create_server(user_id: str) -> FastMCP:
    """Create an MCP server for a specific user."""
    mcp = FastMCP(
        "syke",
        instructions=(
            f"Syke \u2014 personal memory for {user_id}. "
            f"Knows who they are, what they're working on, and what happened across platforms.\n\n"
            f"## Start Here\n\n"
            f"get_live_context() \u2014 Instant identity snapshot. Current projects, preferences, "
            f"communication style, recent activity. Always call this first. Zero cost.\n\n"
            f"## Go Deeper\n\n"
            f"ask(question) \u2014 When the live context doesn't have the answer. Syke explores "
            f"the timeline across platforms and returns a precise answer. Takes 5-25s.\n"
            f"Examples:\n"
            f'- "What did they work on last week?" \u2192 ask\n'
            f'- "Who are they collaborating with?" \u2192 ask\n'
            f'- "Find the decision about database choice" \u2192 ask\n\n'
            f"## Contribute Back\n\n"
            f"record(observation) \u2014 When something meaningful happens. Decisions, completions, "
            f"preferences, frustrations. Natural language, Syke handles the rest.\n"
            f"The daemon already captures sessions and commits. Use record() for signal it can't see.\n"
            f"Examples:\n"
            f'- record("User decided to use Rust for the CLI rewrite")\n'
            f'- record("Finished migrating to PostgreSQL 16")\n'
            f'- record("Prefers explicit error messages over silent failures")\n\n'
            f"## Pattern\n\n"
            f"get_live_context() first (free, instant) \u2192 ask() if needed (costs, slow) \u2192 "
            f"record() when something happens (free, instant)\n\n"
            f"## Data Tools (Advanced)\n\n"
            f"Also available: query_timeline(since, source), search_events(query), "
            f"get_event(id), get_manifest(). Use for debugging or when you need raw data."
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

    def _log_mcp_call(
        tool_name: str,
        args: dict,
        result: dict | str,
        duration_ms: float = 0,
        caller: str = "external",
    ) -> None:
        """Log every tool call to mcp_calls.jsonl. Silent on failure."""
        try:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "tool": tool_name,
                "caller": caller,
                "args_summary": {k: str(v)[:100] for k, v in args.items()},
                "duration_ms": round(duration_ms, 1),
                "result_size": len(str(result)),
                "status": "ok",
            }
            log_path = user_data_dir(user_id) / "mcp_calls.jsonl"
            with open(log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # logging must never break tool calls

    # ── Primary Tools (The Three Verbs) ──────────────────────────────

    @mcp.tool()
    def get_live_context(format: str = "json") -> str:
        """Get the user's live identity context \u2014 who they are, what they're working on, how they communicate.

        Pre-synthesized from cross-platform signals (code, conversations, commits, emails).
        Updated every sync cycle. Instant, zero-cost. START HERE before ask().

        This is not a database lookup \u2014 it's a living model of the user, synthesized by
        agentic perception across all their platforms.

        Args:
            format: Output format \u2014 json, markdown, claude-md, or user-md

        Examples of what you'll find:
        - Current active projects and priorities
        - Communication style and preferences
        - Recent context (what happened this week)
        - Cross-platform threads (GitHub + ChatGPT + Claude Code connections)
        - Technical skills, tools, and patterns
        - World state (deadlines, collaborations, mood signals)

        When to use this vs ask():
        - "What is the user working on?" \u2192 get_live_context() (it's right there)
        - "What did they think about X three weeks ago?" \u2192 ask() (needs timeline exploration)
        - "How do they prefer error messages?" \u2192 get_live_context() (communication style)
        - "Find the commit where they refactored auth" \u2192 ask() (specific search needed)
        """
        t0 = time.monotonic()
        db = _get_db()
        profile = db.get_latest_profile(user_id)
        if not profile:
            result = json.dumps({"error": "No profile found. Run: syke setup"})
            _log_mcp_call(
                "get_live_context",
                {"format": format},
                result,
                (time.monotonic() - t0) * 1000,
            )
            return result
        try:
            result = format_profile(profile, format)
        except ValueError as e:
            result = json.dumps({"error": str(e)})
        _log_mcp_call(
            "get_live_context",
            {"format": format},
            result,
            (time.monotonic() - t0) * 1000,
        )
        return result

    @mcp.tool()
    async def ask(question: str) -> str:
        """Ask any question about the user \u2014 Syke explores their timeline and returns a precise answer.

        Syke's internal agent reads the synthesized identity, searches across platforms,
        and reasons about the user's history. More thorough than manual search, but takes
        5-25 seconds.

        Use this when get_live_context() doesn't have the answer. Don't use it for
        questions the live context already covers.

        Args:
            question: Natural language question about the user

        Examples:
        - "What did they work on last week?"
        - "What do they think about RAG architectures?"
        - "Who are they collaborating with on the auth refactor?"
        - "What's their experience with Rust?"
        - "Find the decision about JWT vs session tokens"

        Costs: Uses agentic exploration (5-25s, ~$0.10-0.20 per call).
        Prefer get_live_context() for questions about current state.
        """
        t0 = time.monotonic()
        from syke.distribution.ask_agent import _run_ask

        result = await _run_ask(_get_db(), user_id, question)
        _log_mcp_call(
            "ask", {"question": question}, result, (time.monotonic() - t0) * 1000
        )
        return result

    @mcp.tool()
    def record(observation: str) -> str:
        """Record an observation about the user. Deliberate signal capture \u2014 Syke handles the rest.

        When something meaningful happens \u2014 a decision, a preference, a project shift,
        a completed task \u2014 record it. Natural language, no structure needed.

        The daemon already captures sessions, commits, and conversations automatically.
        Use record() for signal the daemon can't see: decisions made in conversation,
        preferences expressed, frustrations, insights.

        Syke will:
        - Detect the calling source automatically
        - Deduplicate against recent records (won't store the same thing twice)
        - Timestamp and index for the timeline
        - Reflect it in the next profile update

        Args:
            observation: What you observed, in natural language.

        Examples:
        - record("User decided to use PostgreSQL over MongoDB for the new project")
        - record("They finished the auth refactor and merged PR #42")
        - record("User expressed frustration with the deployment pipeline")
        - record("Prefers dark mode, mentioned it twice today")
        - record("Started collaborating with Sarah on the ML pipeline")

        When to call this:
        - User makes a decision (architecture, tool choice, direction)
        - A task is completed (feature shipped, bug fixed, PR merged)
        - User expresses a strong preference or frustration
        - A new project or collaboration starts
        - Something happens that future sessions should know about

        When NOT to call this:
        - Every message in the conversation (too noisy \u2014 daemon handles session capture)
        - Trivial observations ("user said hello")
        - Information already in the live context
        """
        from syke.ingestion.gateway import IngestGateway

        t0 = time.monotonic()
        db = _get_db()

        # Content hash dedup \u2014 don't store the same observation twice
        content_hash = hashlib.sha256(observation.encode()).hexdigest()[:16]
        if db.event_exists_by_external_id("mcp-record", user_id, content_hash):
            result = json.dumps(
                {"status": "already_known", "message": "Already recorded."}
            )
            _log_mcp_call(
                "record",
                {"observation_length": len(observation)},
                result,
                (time.monotonic() - t0) * 1000,
            )
            return result

        # Write via IngestGateway (content filter, validation, storage)
        gateway = IngestGateway(db, user_id)
        gw_result = gateway.push(
            source="mcp-record",
            event_type="observation",
            title=observation[:120],
            content=observation,
            timestamp=datetime.now().isoformat(),
            external_id=content_hash,
        )

        duration_ms = (time.monotonic() - t0) * 1000

        if gw_result.get("status") == "error":
            result = json.dumps({"status": "error", "message": gw_result["error"]})
            _log_mcp_call(
                "record", {"observation_length": len(observation)}, result, duration_ms
            )
            return result

        result = json.dumps(
            {
                "status": "recorded",
                "message": "Recorded. Will be reflected in the next profile update.",
            }
        )
        _log_mcp_call(
            "record", {"observation_length": len(observation)}, result, duration_ms
        )
        return result

    # ── Alias (Temporary, 1 Release) ─────────────────────────────────

    @mcp.tool()
    def get_profile(format: str = "json") -> str:
        """Alias for get_live_context(). Will be removed in next release.

        Args:
            format: Output format \u2014 json, markdown, claude-md, or user-md
        """
        return get_live_context(format)

    # ── Secondary Tools (Data, not promoted in instructions) ─────────

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
        t0 = time.monotonic()
        db = _get_db()
        events = db.get_events(user_id, source=source, since=since, limit=limit)
        if summary:
            events = _summarize_events(events)
        result = json.dumps(events, indent=2, default=str)
        _log_mcp_call(
            "query_timeline",
            {"since": since, "source": source, "limit": limit},
            result,
            (time.monotonic() - t0) * 1000,
        )
        return result

    @mcp.tool()
    def get_manifest() -> str:
        """Get a summary of all ingested data \u2014 sources, event counts, and profile status."""
        t0 = time.monotonic()
        db = _get_db()
        status = db.get_status(user_id)

        profile_ts = db.get_last_profile_timestamp(user_id)
        if profile_ts:
            from datetime import UTC

            try:
                profile_dt = datetime.fromisoformat(profile_ts.replace("Z", "+00:00"))
                age_hours = (datetime.now(UTC) - profile_dt).total_seconds() / 3600
                status["profile_age_hours"] = round(age_hours, 1)
                status["profile_fresh"] = age_hours < 24
            except (ValueError, TypeError):
                pass
            status["events_since_profile"] = db.count_events_since(user_id, profile_ts)

        cost_stats = db.get_perception_cost_stats(user_id)
        if cost_stats:
            status["profile_costs"] = cost_stats

        result = json.dumps(status, indent=2, default=str)
        _log_mcp_call("get_manifest", {}, result, (time.monotonic() - t0) * 1000)
        return result

    @mcp.tool()
    def get_event(event_id: str) -> str:
        """Fetch full content for a single event by ID.

        Use this after search_events or query_timeline to read the complete
        content of a specific event without the cost of an ask() call.

        Args:
            event_id: The event ID to fetch
        """
        t0 = time.monotonic()
        db = _get_db()
        event = db.get_event_by_id(user_id, event_id)
        if event is None:
            result = json.dumps({"error": f"Event not found: {event_id}"})
        else:
            result = json.dumps(event, indent=2, default=str)
        _log_mcp_call(
            "get_event", {"event_id": event_id}, result, (time.monotonic() - t0) * 1000
        )
        return result

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
        t0 = time.monotonic()
        db = _get_db()
        events = db.search_events(user_id, query, limit)
        if summary:
            events = _summarize_events(events)
        result = json.dumps(events, indent=2, default=str)
        _log_mcp_call(
            "search_events",
            {"query": query, "limit": limit},
            result,
            (time.monotonic() - t0) * 1000,
        )
        return result

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

        t0 = time.monotonic()
        db = _get_db()
        try:
            meta = json.loads(metadata) if metadata else None
        except (json.JSONDecodeError, TypeError) as e:
            result = json.dumps(
                {"status": "error", "error": f"Invalid metadata JSON: {e}"}
            )
            _log_mcp_call(
                "push_event",
                {"source": source, "title": title},
                result,
                (time.monotonic() - t0) * 1000,
            )
            return result
        if meta is not None and not isinstance(meta, dict):
            result = json.dumps(
                {
                    "status": "error",
                    "error": f"metadata must be a JSON object, got {type(meta).__name__}",
                }
            )
            _log_mcp_call(
                "push_event",
                {"source": source, "title": title},
                result,
                (time.monotonic() - t0) * 1000,
            )
            return result
        gateway = IngestGateway(db, user_id)
        gw_result = gateway.push(
            source=source,
            event_type=event_type,
            title=title,
            content=content,
            timestamp=timestamp,
            metadata=meta,
            external_id=external_id,
        )
        result = json.dumps(gw_result)
        _log_mcp_call(
            "push_event",
            {"source": source, "title": title},
            result,
            (time.monotonic() - t0) * 1000,
        )
        return result

    @mcp.tool()
    def push_events(events_json: str) -> str:
        """Push multiple events to Syke's timeline in a single call.

        Args:
            events_json: JSON array of event objects, each with: source, event_type, title, content, and optional timestamp, metadata, external_id
        """
        from syke.ingestion.gateway import IngestGateway

        t0 = time.monotonic()
        db = _get_db()
        try:
            events = json.loads(events_json)
        except (json.JSONDecodeError, TypeError) as e:
            result = json.dumps({"status": "error", "error": f"Invalid JSON: {e}"})
            _log_mcp_call(
                "push_events",
                {"count": "parse_error"},
                result,
                (time.monotonic() - t0) * 1000,
            )
            return result
        if not isinstance(events, list):
            result = json.dumps(
                {"status": "error", "error": "events_json must be a JSON array"}
            )
            _log_mcp_call(
                "push_events",
                {"count": "not_array"},
                result,
                (time.monotonic() - t0) * 1000,
            )
            return result
        gateway = IngestGateway(db, user_id)
        gw_result = gateway.push_batch(events)
        result = json.dumps(gw_result)
        _log_mcp_call(
            "push_events",
            {"count": len(events)},
            result,
            (time.monotonic() - t0) * 1000,
        )
        return result

    return mcp
