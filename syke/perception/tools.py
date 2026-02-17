"""Agent SDK tools for agentic perception — wraps SykeDB queries, served via in-process MCP server."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server

from syke.db import SykeDB

# ---------------------------------------------------------------------------
# Tool definitions (called by the agent during perception)
# ---------------------------------------------------------------------------

CONTENT_PREVIEW_LEN = 800

TOOL_NAMES = [
    "get_source_overview",
    "browse_timeline",
    "search_footprint",
    "cross_reference",
    "read_previous_profile",
    "submit_profile",
]


# ---------------------------------------------------------------------------
# Coverage tracker — used by PostToolUse / PreToolUse hooks
# ---------------------------------------------------------------------------

@dataclass
class CoverageTracker:
    """Tracks what the agent has explored so far.

    Used by hooks to inject mid-loop feedback (PostToolUse) and to gate
    submission (PreToolUse on submit_profile).  Zero extra API cost —
    hooks piggyback on existing turns.
    """

    known_sources: list[str] = field(default_factory=list)
    sources_browsed: set[str] = field(default_factory=set)
    sources_searched: set[str] = field(default_factory=set)
    topics_searched: list[str] = field(default_factory=list)
    tool_count: int = 0
    cross_platform_count: int = 0
    thread_count: int = 0

    def update_from_tool_call(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """Update coverage state when a tool is called."""
        self.tool_count += 1

        if tool_name == "browse_timeline":
            src = tool_input.get("source")
            if src:
                self.sources_browsed.add(src)
            else:
                # Browsing without source filter — doesn't prove any specific source was explored.
                # The agent should use source-filtered browses or search results to prove coverage.
                pass

        elif tool_name == "search_footprint":
            query = tool_input.get("query", "")
            if query:
                self.topics_searched.append(query)

        elif tool_name == "cross_reference":
            topic = tool_input.get("topic", "")
            if topic:
                self.topics_searched.append(topic)
                self.cross_platform_count += 1

        elif tool_name == "get_source_overview":
            # Overview is awareness, not exploration — don't grant full coverage
            pass

    def update_from_tool_result(self, tool_name: str, result_text: str) -> None:
        """Update coverage state from a tool result (PostToolUse)."""
        # Detect which sources appear in results by checking for JSON-formatted source fields
        # (e.g. "source": "github") to avoid false positives from URLs or content text
        for src in self.known_sources:
            if f'"source": "{src}"' in result_text:
                self.sources_searched.add(src)

    @property
    def explored_sources(self) -> set[str]:
        """Sources the agent has actively explored (browsed or found in search results)."""
        return self.sources_browsed | self.sources_searched

    @property
    def missing_sources(self) -> list[str]:
        """Sources the agent hasn't explored yet."""
        return [s for s in self.known_sources if s not in self.explored_sources]

    @property
    def source_coverage(self) -> float:
        """Fraction of known sources explored (0.0 to 1.0)."""
        if not self.known_sources:
            return 1.0
        return len(self.explored_sources) / len(self.known_sources)

    def coverage_feedback(self) -> str | None:
        """Generate feedback string if there are coverage gaps.

        Returns None if coverage is sufficient.
        """
        missing = self.missing_sources
        if not missing or self.tool_count < 3:
            return None

        parts = [
            f"COVERAGE GAP: You haven't explored {', '.join(missing)} yet.",
            f"Sources covered: {len(self.explored_sources)}/{len(self.known_sources)}.",
            f"Cross-platform queries so far: {self.cross_platform_count}.",
        ]
        if self.cross_platform_count < 2:
            parts.append("Consider using cross_reference to find patterns across platforms.")
        parts.append("Explore the missing sources before submitting.")
        return " ".join(parts)

    def submission_gaps(self, profile_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Analyze whether the profile is ready for submission.

        Returns a dict with gap details.  If the dict is empty, the profile
        is ready to submit.
        """
        gaps: dict[str, Any] = {}

        # Source coverage check
        if self.source_coverage < 1.0:
            gaps["sources_missing"] = self.missing_sources
            gaps["source_coverage"] = self.source_coverage

        # Cross-platform ratio check — at least 40% of tool calls should be
        # cross-platform (cross_reference) or we haven't cross-referenced enough
        if self.known_sources and len(self.known_sources) > 1:
            if self.cross_platform_count < 1:
                gaps["cross_platform_deficit"] = True
                gaps["cross_platform_count"] = self.cross_platform_count

        # Minimum exploration check
        if self.tool_count < 4:
            gaps["insufficient_exploration"] = True
            gaps["tool_count"] = self.tool_count

        return gaps


def create_perception_tools(db: SykeDB, user_id: str) -> list:
    """Build the six perception tools bound to a specific DB and user."""

    def _format_event(ev: dict[str, Any]) -> dict[str, Any]:
        """Format an event dict for tool output with content preview."""
        return {
            "timestamp": ev["timestamp"],
            "source": ev["source"],
            "event_type": ev["event_type"],
            "title": ev.get("title") or "",
            "content_preview": (ev.get("content") or "")[:CONTENT_PREVIEW_LEN],
        }

    @tool(
        "get_source_overview",
        "Get an overview of the user's digital footprint: platforms, event counts, date ranges, and latest profile timestamp.",
        {},
    )
    async def get_source_overview(args: dict[str, Any]) -> dict[str, Any]:
        status = db.get_status(user_id)
        # Add date range per source
        source_details = {}
        for source in status.get("sources", {}):
            earliest, latest = db.get_source_date_range(user_id, source)
            source_details[source] = {
                "count": status["sources"][source],
                "earliest": earliest,
                "latest": latest,
            }
        result = {
            "user_id": user_id,
            "total_events": status["total_events"],
            "sources": source_details,
            "latest_profile": status.get("latest_profile"),
        }
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "browse_timeline",
        "Browse events in a time window. Returns content previews (first 800 chars). Use 'source' to filter by platform, 'since'/'before' for date range (ISO format), 'limit' for max events (default 50).",
        {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "ISO date/datetime to filter from (e.g., '2026-02-01')"},
                "before": {"type": "string", "description": "ISO date/datetime to filter until"},
                "source": {"type": "string", "description": "Platform name to filter (e.g., 'github', 'claude-code')"},
                "limit": {"type": "integer", "description": "Max events to return (default 50, max 100)"},
            },
            "required": [],
        },
    )
    async def browse_timeline(args: dict[str, Any]) -> dict[str, Any]:
        limit = min(args.get("limit", 50), 100)
        events = db.get_events(
            user_id,
            source=args.get("source"),
            since=args.get("since"),
            before=args.get("before"),
            limit=limit,
        )
        formatted = [_format_event(ev) for ev in events]
        result = {"count": len(formatted), "events": formatted}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "search_footprint",
        "Full-text search across all events by keyword. Returns matching events with content previews.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term"},
                "limit": {"type": "integer", "description": "Max results (default 20, max 50)"},
            },
            "required": ["query"],
        },
    )
    async def search_footprint(args: dict[str, Any]) -> dict[str, Any]:
        limit = min(args.get("limit", 20), 50)
        events = db.search_events(user_id, args["query"], limit=limit)
        formatted = [_format_event(ev) for ev in events]
        result = {"query": args["query"], "count": len(formatted), "events": formatted}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "cross_reference",
        "Search for a topic across ALL platforms, grouped by source. The key ALMA-inspired tool: discover what patterns exist across the digital footprint.",
        {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic or keyword to search across all platforms"},
                "limit_per_source": {"type": "integer", "description": "Max events per source (default 10)"},
            },
            "required": ["topic"],
        },
    )
    async def cross_reference(args: dict[str, Any]) -> dict[str, Any]:
        topic = args["topic"]
        limit_per = min(args.get("limit_per_source", 10), 25)
        sources = db.get_sources(user_id)
        # Single query, group results by source in Python
        all_matches = db.search_events(user_id, topic, limit=limit_per * len(sources) if sources else limit_per)
        by_source: dict[str, list] = {}
        for ev in all_matches:
            src = ev["source"]
            if src not in by_source:
                by_source[src] = []
            if len(by_source[src]) < limit_per:
                by_source[src].append(_format_event(ev))
        result = {
            "topic": topic,
            "sources_with_matches": list(by_source.keys()),
            "total_matches": sum(len(v) for v in by_source.values()),
            "by_source": by_source,
        }
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "read_previous_profile",
        "Get the most recent perception profile for incremental updates. Returns the full profile JSON or indicates no profile exists.",
        {},
    )
    async def read_previous_profile(args: dict[str, Any]) -> dict[str, Any]:
        profile = db.get_latest_profile(user_id)
        if profile:
            profile_data = json.loads(profile.model_dump_json())
            # Cap profile size to prevent unbounded growth
            if "active_threads" in profile_data and isinstance(profile_data["active_threads"], list):
                profile_data["active_threads"] = profile_data["active_threads"][:5]
            if "recent_detail" in profile_data and isinstance(profile_data["recent_detail"], str):
                profile_data["recent_detail"] = profile_data["recent_detail"][:2000]
            result = {"exists": True, "profile": profile_data}
        else:
            result = {"exists": False}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "submit_profile",
        "Submit the final perception profile. Call this EXACTLY ONCE when done. Required fields: identity_anchor (str), active_threads (list), recent_detail (str), background_context (str). Optional: voice_patterns (object with tone, vocabulary_notes, communication_style, examples).",
        {
            "type": "object",
            "properties": {
                "identity_anchor": {"type": "string", "description": "2-3 sentences: who IS this person?"},
                "active_threads": {
                    "type": "array",
                    "description": "Active threads of interest/work",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "intensity": {"type": "string", "enum": ["high", "medium", "low"]},
                            "platforms": {"type": "array", "items": {"type": "string"}},
                            "recent_signals": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "description"],
                    },
                },
                "recent_detail": {"type": "string", "description": "Precise context from last ~2 weeks"},
                "background_context": {"type": "string", "description": "Longer arcs, career, recurring themes"},
                "world_state": {
                    "type": "string",
                    "description": "Precise map of the user's current world. Projects with their status, recent decisions, open questions, blockers. Factual bedrock alongside the narrative. Write as detailed prose.",
                },
                "voice_patterns": {
                    "type": "object",
                    "description": "Communication style",
                    "properties": {
                        "tone": {"type": "string"},
                        "vocabulary_notes": {"type": "array", "items": {"type": "string"}},
                        "communication_style": {"type": "string"},
                        "examples": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "required": ["identity_anchor", "active_threads", "recent_detail", "background_context"],
        },
    )
    async def submit_profile(args: dict[str, Any]) -> dict[str, Any]:
        # Validate required fields
        missing = []
        for field in ["identity_anchor", "active_threads", "recent_detail", "background_context"]:
            if not args.get(field):
                missing.append(field)
        if missing:
            return {
                "content": [{"type": "text", "text": json.dumps({
                    "status": "error",
                    "message": f"Missing required fields: {', '.join(missing)}",
                })}],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": json.dumps({
            "status": "submitted",
            "profile": args,
        })}]}

    return [get_source_overview, browse_timeline, search_footprint, cross_reference, read_previous_profile, submit_profile]


def build_perception_mcp_server(db: SykeDB, user_id: str):
    """Create an in-process MCP server with perception tools."""
    tools = create_perception_tools(db, user_id)
    return create_sdk_mcp_server(name="perception", version="1.0.0", tools=tools)
