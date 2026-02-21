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





def create_server(user_id: str) -> FastMCP:
    """Create an MCP server for a specific user."""
    mcp = FastMCP(
        "syke",
        instructions=(
            f"Syke — personal memory for {user_id}. "
            f"Knows who they are, what they're working on, and what happened across platforms.\n\n"
            f"## Three verbs:\n\n"
            f"get_live_context() — Instant identity snapshot. Always call this first. Zero cost.\n\n"
            f"ask(question) — Explore the timeline. For anything get_live_context doesn't answer. Takes 15-30s.\n\n"
            f"record(observation) — Push a meaningful signal back. Decisions, completions, preferences. "
            f"Natural language, Syke handles the rest.\n\n"
            f"Pattern: get_live_context first → ask if needed → record when something happens."
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















    return mcp
