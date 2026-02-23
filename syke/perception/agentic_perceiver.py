"""Agentic perception engine — Opus crawls the digital footprint using Agent SDK.

Supports two modes:
- Single agent (default): Opus explores directly with MCP tools
- Multi-agent (use_sub_agents=True): 3 Sonnet explorers + Opus synthesizer

Cost tiers:
- Full perception (Opus agent loop): $0.50–$3.00 — ground-up profile builds
- Incremental perception (Sonnet agent loop): $0.05–$0.15 — routine delta updates
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    PermissionResultAllow,
    PermissionResultDeny,
)

from syke.config import (
    SYNC_MODEL, SYNC_MAX_TURNS, SYNC_BUDGET, SYNC_THINKING,
    REBUILD_MODEL, REBUILD_MAX_TURNS, REBUILD_BUDGET, REBUILD_THINKING,
)
from syke.db import SykeDB
from syke.models import UserProfile
from syke.perception.agent_prompts import (
    AGENT_SYSTEM_PROMPT,
    AGENT_TASK_PROMPT_FULL,
    AGENT_TASK_PROMPT_INCREMENTAL,
    SUB_AGENTS,
)
from syke.perception.tools import TOOL_NAMES, CoverageTracker, build_perception_mcp_server
from syke.memory.memex import get_memex_for_injection


# ---------------------------------------------------------------------------
# Shared types and utilities (inlined from _shared.py)
# ---------------------------------------------------------------------------

DiscoveryCallback = Callable[[str, str], None]


@dataclass
class ToolCallTrace:
    """Per-tool instrumentation for benchmarking."""

    name: str
    args_summary: str
    started_at: float  # time.monotonic()
    completed_at: float | None = None
    result_size: int = 0
    was_empty: bool = False


@dataclass
class PerceptionMetrics:
    """Structured metrics captured from a perception run."""

    cost_usd: float = 0.0
    num_turns: int = 0
    duration_ms: int = 0
    duration_api_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    tool_traces: list[ToolCallTrace] = field(default_factory=list)


_PROFILE_FIELDS = frozenset({
    "identity_anchor", "active_threads", "recent_detail",
    "background_context", "world_state", "voice_patterns",
})

def summarize_args(args: dict[str, Any]) -> str:
    """Create a short summary of tool call arguments for display."""
    parts = []
    for key, val in args.items():
        if key in _PROFILE_FIELDS:
            if isinstance(val, str):
                parts.append(f"{key}='{val[:40]}...'")
            elif isinstance(val, list):
                parts.append(f"{key}=[{len(val)} items]")
            else:
                parts.append(f"{key}=...")
        elif isinstance(val, str) and len(val) > 50:
            parts.append(f"{key}='{val[:50]}...'")
        elif isinstance(val, (list, dict)):
            parts.append(f"{key}=<{type(val).__name__}>")
        else:
            parts.append(f"{key}={val}")
    return " ".join(parts[:8])


def build_profile_from_submission(
    data: dict[str, Any],
    user_id: str,
    events_count: int,
    sources: list[str],
    cost_usd: float,
    model: str | None = None,
) -> UserProfile:
    """Convert submitted profile data to a UserProfile model."""
    return UserProfile(
        user_id=user_id,
        identity_anchor=data.get("identity_anchor", ""),
        active_threads=data.get("active_threads", []),
        recent_detail=data.get("recent_detail", ""),
        background_context=data.get("background_context", ""),
        world_state=data.get("world_state", ""),
        voice_patterns=data.get("voice_patterns") or None,
        sources=sources,
        events_count=events_count,
        model=model or REBUILD_MODEL,
        cost_usd=cost_usd,
    )


def merge_delta_into_profile(
    existing: UserProfile,
    delta: dict[str, Any],
    user_id: str,
    events_count: int,
    sources: list[str],
    cost_usd: float,
    model: str | None = None,
) -> UserProfile:
    """Merge a delta submission into an existing profile.

    Fields present in the delta overwrite the existing profile.
    Fields absent from the delta are preserved from the existing profile.
    """
    # Start with existing profile data
    base = existing.model_dump()

    # Fields the agent can update via delta
    mergeable_fields = {
        "identity_anchor", "active_threads", "recent_detail",
        "background_context", "world_state", "voice_patterns",
    }

    for field_name in mergeable_fields:
        if field_name in delta and delta[field_name]:
            base[field_name] = delta[field_name]

    # Update metadata fields (always fresh)
    base["user_id"] = user_id
    base["sources"] = sources
    base["events_count"] = events_count
    base["model"] = model or REBUILD_MODEL
    base["cost_usd"] = cost_usd

    return UserProfile.model_validate(base)


# ---------------------------------------------------------------------------
# Hook factories
# ---------------------------------------------------------------------------

def _make_coverage_hook(
    tracker: CoverageTracker,
    tool_prefix: str,
    on_discovery: DiscoveryCallback | None = None,
) -> Any:
    """Factory: returns a PreToolUse hook that gates submit_profile on coverage."""

    async def _coverage_gate(
        tool_name: str, tool_input: dict, context: Any
    ) -> PermissionResultAllow | PermissionResultDeny:
        short_name = tool_name.replace(tool_prefix, "")

        tracker.update_from_tool_call(short_name, tool_input)

        if short_name == "submit_profile":
            gaps = tracker.submission_gaps(tool_input)
            if gaps:
                reasons = []
                if "sources_missing" in gaps:
                    reasons.append(
                        f"Sources not explored: {', '.join(gaps['sources_missing'])} "
                        f"({gaps['source_coverage']:.0%} coverage)"
                    )
                if gaps.get("cross_platform_deficit"):
                    reasons.append(
                        f"No cross-platform queries yet (use cross_reference)"
                    )
                if gaps.get("insufficient_exploration"):
                    reasons.append(
                        f"Only {gaps['tool_count']} tool calls — explore more before submitting"
                    )
                reason = (
                    "Profile has coverage gaps: " + "; ".join(reasons) + ". "
                    "Explore the missing sources first, then resubmit."
                )
                if on_discovery:
                    on_discovery("hook_gate", reason)
                return PermissionResultDeny(reason=reason)

        feedback = tracker.coverage_feedback()
        if feedback and on_discovery:
            on_discovery("hook_feedback", feedback)

        return PermissionResultAllow()

    return _coverage_gate


def _make_search_validator(
    on_discovery: DiscoveryCallback | None = None,
) -> Any:
    """Factory: returns a PreToolUse hook that optimizes multi-word search queries."""

    async def _validate_search_query(tool_name: str, tool_input: dict, context: Any) -> PermissionResultAllow:
        if "search" in tool_name or "cross_reference" in tool_name:
            query = tool_input.get("query") or tool_input.get("topic", "")
            words = query.split()
            if len(words) > 3:
                original = query
                best = max(words, key=len)
                if "query" in tool_input:
                    tool_input["query"] = best
                elif "topic" in tool_input:
                    tool_input["topic"] = best
                if on_discovery:
                    on_discovery("hook_correction", f"'{original}' -> '{best}'")
                return PermissionResultAllow(updated_input=tool_input)
        return PermissionResultAllow()

    return _validate_search_query


def _chain_hooks(*hooks):
    """Chain multiple PreToolUse hooks — runs in order, first deny wins."""

    async def _chained(tool_name: str, tool_input: dict, context: Any):
        for hook in hooks:
            result = await hook(tool_name, tool_input, context)
            if isinstance(result, PermissionResultDeny):
                return result
            if hasattr(result, "updated_input") and result.updated_input:
                tool_input = result.updated_input
        return PermissionResultAllow()

    return _chained


# ---------------------------------------------------------------------------
# Main perceiver class
# ---------------------------------------------------------------------------

class AgenticPerceiver:
    """Agentic perception using Claude Agent SDK with custom MCP tools.

    Modes:
    - use_sub_agents=False (default): Single Opus agent explores directly
    - use_sub_agents=True: 3 Sonnet sub-agents explore, Opus synthesizes
    """

    TOOL_PREFIX = "mcp__perception__"

    def __init__(self, db: SykeDB, user_id: str, *, use_sub_agents: bool = False):
        self.db = db
        self.user_id = user_id
        self.use_sub_agents = use_sub_agents
        self.metrics = PerceptionMetrics()
        self.current_model: str = ""
        self.coverage_tracker: CoverageTracker | None = None

    def perceive(
        self,
        full: bool = True,
        on_discovery: DiscoveryCallback | None = None,
        save: bool = True,
    ) -> UserProfile:
        """Run agentic perception. Synchronous wrapper around async internals."""
        return asyncio.run(self._perceive_async(full=full, on_discovery=on_discovery, save=save))

    async def _perceive_async(
        self,
        full: bool = True,
        on_discovery: DiscoveryCallback | None = None,
        save: bool = True,
    ) -> UserProfile:
        """Core async perception loop using ClaudeSDKClient."""
        events_count = self.db.count_events(self.user_id)
        sources = self.db.get_sources(self.user_id)
        sources_list = ", ".join(sources)

        # Load existing profile for incremental merge
        existing_profile: UserProfile | None = None

        memex_context = get_memex_for_injection(self.db, self.user_id)
        memex_section = (
            f"\n\n## Existing Memory (Memex)\n{memex_context}"
            if memex_context and not memex_context.startswith("[No data")
            else ""
        )

        if full:
            task_prompt = AGENT_TASK_PROMPT_FULL.format(
                user_id=self.user_id,
                events_count=events_count,
                sources_list=sources_list,
            ) + memex_section
        else:
            existing_profile = self.db.get_latest_profile(self.user_id)
            last_ts = self.db.get_last_profile_timestamp(self.user_id)
            new_count = 0
            if last_ts:
                new_events = self.db.get_events_since_ingestion(
                    self.user_id, since_ingested=last_ts, limit=500
                )
                new_count = len(new_events)
            task_prompt = AGENT_TASK_PROMPT_INCREMENTAL.format(
                user_id=self.user_id,
                new_events_count=new_count,
                events_count=events_count,
                sources_list=sources_list,
            ) + memex_section

        perception_server = build_perception_mcp_server(self.db, self.user_id)
        self.coverage_tracker = CoverageTracker(known_sources=sources)

        # Build hooks — multi-agent mode chains search validator + coverage gate
        coverage_hook = _make_coverage_hook(
            self.coverage_tracker, self.TOOL_PREFIX, on_discovery
        )
        if self.use_sub_agents:
            search_hook = _make_search_validator(on_discovery)
            can_use_tool = _chain_hooks(search_hook, coverage_hook)
        else:
            can_use_tool = coverage_hook

        # Model selection: Sonnet for incremental (cheap), Opus for full (deep)
        if full:
            model = REBUILD_MODEL
            max_turns = 25 if self.use_sub_agents else REBUILD_MAX_TURNS  # 25 fixed for sub-agent orchestrator; SYKE_REBUILD_MAX_TURNS controls single-agent mode
            max_thinking = REBUILD_THINKING
            max_budget = REBUILD_BUDGET
        else:
            model = SYNC_MODEL
            max_turns = SYNC_MAX_TURNS
            max_thinking = SYNC_THINKING
            max_budget = SYNC_BUDGET

        allowed = [f"{self.TOOL_PREFIX}{name}" for name in TOOL_NAMES]
        options = ClaudeAgentOptions(
            system_prompt=AGENT_SYSTEM_PROMPT,
            model=model,
            mcp_servers={"perception": perception_server},
            allowed_tools=allowed,
            permission_mode="bypassPermissions",
            max_turns=max_turns,
            max_thinking_tokens=max_thinking,
            max_budget_usd=max_budget,
            can_use_tool=can_use_tool,
        )

        # Multi-agent mode: pass sub-agent definitions
        if self.use_sub_agents:
            options.agents = SUB_AGENTS

        # Run the agent
        submitted_profile: dict[str, Any] | None = None
        thinking_char_count = 0
        current_tool_trace: ToolCallTrace | None = None

        async with ClaudeSDKClient(options=options) as client:
            await client.query(task_prompt)

            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    self.current_model = getattr(message, "model", "") or ""
                    for block in message.content:
                        if isinstance(block, ThinkingBlock):
                            thinking_char_count += len(block.thinking)
                            if on_discovery:
                                on_discovery("thinking", block.thinking)
                        elif isinstance(block, TextBlock) and block.text.strip():
                            if on_discovery:
                                on_discovery("reasoning", block.text)
                        elif isinstance(block, ToolUseBlock):
                            tool_short = block.name.replace(self.TOOL_PREFIX, "")
                            args_summary = summarize_args(block.input)
                            current_tool_trace = ToolCallTrace(
                                name=tool_short,
                                args_summary=args_summary,
                                started_at=time.monotonic(),
                            )
                            if on_discovery:
                                on_discovery("tool_call", f"{tool_short} {args_summary}")
                            if tool_short == "submit_profile":
                                submitted_profile = block.input
                        elif isinstance(block, ToolResultBlock):
                            if current_tool_trace:
                                current_tool_trace.completed_at = time.monotonic()
                                content = block.content
                                if isinstance(content, list):
                                    parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                                    content = " ".join(parts)
                                content_str = str(content or "")
                                current_tool_trace.result_size = len(content_str)
                                current_tool_trace.was_empty = (
                                    '"count": 0' in content_str
                                    or '"total_matches": 0' in content_str
                                )
                                self.metrics.tool_traces.append(current_tool_trace)
                                if self.coverage_tracker:
                                    self.coverage_tracker.update_from_tool_result(
                                        current_tool_trace.name, content_str
                                    )
                                current_tool_trace = None
                            if on_discovery:
                                content = block.content
                                if isinstance(content, list):
                                    parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                                    content = " ".join(parts)
                                on_discovery("tool_result", str(content or "")[:300])

                elif isinstance(message, ResultMessage):
                    self.metrics.cost_usd = message.total_cost_usd or 0.0
                    self.metrics.num_turns = message.num_turns or 0
                    self.metrics.duration_ms = message.duration_ms or 0
                    self.metrics.duration_api_ms = message.duration_api_ms or 0
                    usage = getattr(message, "usage", None) or {}
                    if isinstance(usage, dict):
                        self.metrics.input_tokens = usage.get("input_tokens", 0)
                        self.metrics.output_tokens = usage.get("output_tokens", 0)
                        sdk_thinking = usage.get("thinking_tokens", 0)
                        stream_estimate = thinking_char_count // 4
                        self.metrics.thinking_tokens = sdk_thinking if sdk_thinking > 0 else stream_estimate
                    if on_discovery:
                        on_discovery(
                            "result",
                            f"turns={self.metrics.num_turns} cost=${self.metrics.cost_usd:.4f}",
                        )

        if submitted_profile is None:
            raise RuntimeError(
                "Agentic perception completed without calling submit_profile. "
                "Check agent logs — the agent may have encountered an auth or API error."
            )

        # Delta merge: for incremental runs with an existing profile,
        # merge the submitted delta into the existing profile
        actual_model = self.current_model or model
        if not full and existing_profile is not None:
            profile = merge_delta_into_profile(
                existing_profile,
                submitted_profile,
                self.user_id,
                events_count,
                sources,
                self.metrics.cost_usd,
                model=actual_model,
            )
        else:
            profile = build_profile_from_submission(
                submitted_profile, self.user_id, events_count, sources, self.metrics.cost_usd,
                model=actual_model,
            )

        if save:
            self.db.save_profile(profile)
        return profile
