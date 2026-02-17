"""Meta-learning perceiver — ALMA-inspired self-improving exploration.

The agent accumulates exploration knowledge across runs. Each pass leaves a trace;
deterministic reflection labels what worked; the next run starts with accumulated
wisdom. Over time the agent develops a personalized exploration strategy — like a
spider building a web that gets denser with each pass.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    tool,
    create_sdk_mcp_server,
)

from syke.db import SykeDB
from syke.models import UserProfile
from syke.perception.tools import (
    TOOL_NAMES,
    CoverageTracker,
    create_perception_tools,
)
from syke.perception.agentic_perceiver import (
    DiscoveryCallback,
    ToolCallTrace,
    PerceptionMetrics,
    build_profile_from_submission,
    summarize_args,
    _make_coverage_hook,
    _make_search_validator,
    _chain_hooks,
)

from experiments.perception.exploration_archive import (
    CrossReferenceRecord,
    ExplorationArchive,
    ExplorationStrategy,
    ExplorationTrace,
    SearchRecord,
    ToolCallRecord,
)
from experiments.perception.meta_prompts import (
    ADAPTIVE_SYSTEM_PROMPT,
    ADAPTIVE_TASK_PROMPT,
    build_focus_areas,
    build_score_history,
    build_strategy_context,
)
from experiments.perception.reflection import evolve_strategy, reflect_on_run


# ---------------------------------------------------------------------------
# The 7th tool: read_exploration_history
# ---------------------------------------------------------------------------

def _build_exploration_history_tool(archive: ExplorationArchive):
    """Build the read_exploration_history MCP tool bound to an archive."""

    @tool(
        "read_exploration_history",
        "Read your own exploration history — what searches worked, what failed, what connections exist. "
        "Use this to learn from past runs before exploring. "
        "Aspects: 'strategy' (full evolved strategy), 'productive_searches' (what worked), "
        "'dead_ends' (what to avoid), 'cross_platform' (known connections), 'recent_traces' (last 3-5 runs).",
        {
            "type": "object",
            "properties": {
                "aspect": {
                    "type": "string",
                    "enum": ["strategy", "productive_searches", "dead_ends", "cross_platform", "recent_traces"],
                    "description": "What aspect of exploration history to read",
                },
            },
            "required": ["aspect"],
        },
    )
    async def read_exploration_history(args: dict[str, Any]) -> dict[str, Any]:
        aspect = args["aspect"]
        strategy = archive.get_latest_strategy()

        if aspect == "strategy":
            if strategy:
                result = strategy.to_dict()
            else:
                result = {"message": "No strategy yet — this is the first run. Explore broadly."}

        elif aspect == "productive_searches":
            if strategy and strategy.productive_searches:
                result = {
                    "productive_searches": [
                        {"query": ps.query, "hit_rate": f"{ps.hit_rate:.0%}",
                         "relevance": f"{ps.relevance_score:.0%}"}
                        for ps in strategy.productive_searches
                    ]
                }
            else:
                result = {"message": "No productive searches recorded yet."}

        elif aspect == "dead_ends":
            if strategy and strategy.dead_end_searches:
                result = {"dead_end_searches": strategy.dead_end_searches,
                          "note": "These queries consistently return 0 results. Avoid them."}
            else:
                result = {"message": "No dead ends recorded yet."}

        elif aspect == "cross_platform":
            if strategy and strategy.cross_platform_topics:
                result = {
                    "cross_platform_topics": [
                        {"topic": ct.topic, "sources": ct.sources,
                         "strength": f"{ct.strength:.0%}"}
                        for ct in strategy.cross_platform_topics
                    ]
                }
            else:
                result = {"message": "No cross-platform connections discovered yet."}

        elif aspect == "recent_traces":
            recent = archive.get_traces(limit=5)
            if recent:
                result = {
                    "recent_runs": [
                        {
                            "run_id": t.run_id,
                            "score": f"{t.profile_score:.0%}",
                            "cost": f"${t.cost_usd:.4f}",
                            "useful_searches": t.useful_searches[:5],
                            "wasted_searches": t.wasted_searches[:5],
                            "connections": len(t.discovered_connections),
                            "tool_sequence": [tc.name for tc in t.tool_calls[:8]],
                        }
                        for t in recent
                    ]
                }
            else:
                result = {"message": "No previous runs recorded."}
        else:
            result = {"error": f"Unknown aspect: {aspect}"}

        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    return read_exploration_history


# ---------------------------------------------------------------------------
# Meta-learning perceiver
# ---------------------------------------------------------------------------

@dataclass
class MetaRunResult:
    """Result from a single meta-learning run."""

    profile: UserProfile
    trace: ExplorationTrace
    metrics: PerceptionMetrics
    strategy_version: int


class MetaLearningPerceiver:
    """Self-improving perception using ALMA-style meta-learning.

    Each run:
    1. Loads archive + latest strategy
    2. Builds adaptive prompt with strategy context
    3. Runs Agent SDK loop with 7 tools (6 standard + read_exploration_history)
    4. Captures ExplorationTrace
    5. Deterministic reflection labels searches as useful/wasted
    6. Archives the trace
    7. Every 3 runs: evolves strategy
    """

    TOOL_PREFIX = "mcp__perception__"

    def __init__(self, db: SykeDB, user_id: str):
        self.db = db
        self.user_id = user_id
        self.archive = ExplorationArchive(user_id)
        self.metrics = PerceptionMetrics()

    def perceive(
        self,
        full: bool = True,
        on_discovery: DiscoveryCallback | None = None,
        save: bool = True,
    ) -> UserProfile:
        """Run a single meta-learning perception pass. Synchronous wrapper."""
        result = asyncio.run(self._perceive_async(
            full=full, on_discovery=on_discovery, save=save,
        ))
        return result.profile

    def run_cycle(
        self,
        n_runs: int = 5,
        on_discovery: DiscoveryCallback | None = None,
        save: bool = True,
        max_budget_usd: float = 15.0,
    ) -> list[MetaRunResult]:
        """Run N meta-learning cycles, evolving the strategy every 3 runs."""
        results = []
        cumulative_cost = 0.0
        for i in range(n_runs):
            if on_discovery:
                on_discovery("meta_cycle",
                    f"=== Meta-learning run {i + 1}/{n_runs} "
                    f"(spent ${cumulative_cost:.2f}/${max_budget_usd:.2f}) ===")

            result = asyncio.run(self._perceive_async(
                full=True, on_discovery=on_discovery, save=save,
            ))
            results.append(result)
            cumulative_cost += result.metrics.cost_usd

            if on_discovery:
                on_discovery("meta_cycle",
                    f"Run {i + 1}: score={result.trace.profile_score:.0%} "
                    f"cost=${result.trace.cost_usd:.4f} "
                    f"cumulative=${cumulative_cost:.2f}")

            if cumulative_cost >= max_budget_usd:
                if on_discovery:
                    on_discovery("budget_stop",
                        f"Budget cap reached: ${cumulative_cost:.2f} >= ${max_budget_usd:.2f}")
                break

        return results

    async def _perceive_async(
        self,
        full: bool = True,
        on_discovery: DiscoveryCallback | None = None,
        save: bool = True,
    ) -> MetaRunResult:
        """Core async meta-learning perception loop."""
        start_time = time.monotonic()
        self.metrics = PerceptionMetrics()

        events_count = self.db.count_events(self.user_id)
        sources = self.db.get_sources(self.user_id)
        sources_list = ", ".join(sources)

        # 1. Load strategy
        strategy = self.archive.get_latest_strategy()
        strategy_version = strategy.version if strategy else 0
        run_number = self.archive.run_count + 1

        # 2. Build adaptive prompts
        strategy_context = build_strategy_context(strategy)
        system_prompt = ADAPTIVE_SYSTEM_PROMPT.format(strategy_context=strategy_context)

        score_history = [t.profile_score for t in self.archive.traces]
        task_prompt = ADAPTIVE_TASK_PROMPT.format(
            user_id=self.user_id,
            run_number=run_number,
            events_count=events_count,
            sources_list=sources_list,
            score_history=build_score_history(score_history),
            focus_areas=build_focus_areas(strategy),
        )

        # 3. Build MCP server with 7 tools
        standard_tools = create_perception_tools(self.db, self.user_id)
        history_tool = _build_exploration_history_tool(self.archive)
        all_tools = standard_tools + [history_tool]
        perception_server = create_sdk_mcp_server(
            name="perception", version="1.0.0", tools=all_tools,
        )

        # 4. Set up coverage tracker and hooks
        coverage_tracker = CoverageTracker(known_sources=sources)
        coverage_hook = _make_coverage_hook(coverage_tracker, self.TOOL_PREFIX, on_discovery)
        search_hook = _make_search_validator(on_discovery)
        can_use_tool = _chain_hooks(search_hook, coverage_hook)

        allowed = [f"{self.TOOL_PREFIX}{name}" for name in TOOL_NAMES]
        allowed.append(f"{self.TOOL_PREFIX}read_exploration_history")

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={"perception": perception_server},
            allowed_tools=allowed,
            permission_mode="bypassPermissions",
            max_turns=20,
            max_thinking_tokens=30000,
            max_budget_usd=3.0,
            can_use_tool=can_use_tool,
        )

        # 5. Run the agent
        submitted_profile: dict[str, Any] | None = None
        thinking_char_count = 0
        current_tool_trace: ToolCallTrace | None = None

        # Trace capture
        tool_call_records: list[ToolCallRecord] = []
        search_records: list[SearchRecord] = []
        cross_ref_records: list[CrossReferenceRecord] = []
        _current_tool_name: str = ""
        _current_tool_args: dict[str, Any] = {}
        _current_tool_start: float = 0.0

        async with ClaudeSDKClient(options=options) as client:
            await client.query(task_prompt)

            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
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
                            _current_tool_name = tool_short
                            _current_tool_args = dict(block.input)
                            _current_tool_start = time.monotonic()

                            current_tool_trace = ToolCallTrace(
                                name=tool_short,
                                args_summary=args_summary,
                                started_at=time.monotonic(),
                            )

                            if on_discovery:
                                on_discovery("tool_call", f"{tool_short} {args_summary}")

                            if tool_short == "submit_profile":
                                submitted_profile = block.input

                            # Track search queries
                            if tool_short in ("search_footprint", "cross_reference"):
                                query = block.input.get("query") or block.input.get("topic", "")
                                if query:
                                    search_records.append(SearchRecord(
                                        query=query, tool=tool_short,
                                    ))

                        elif isinstance(block, ToolResultBlock):
                            content = block.content
                            if isinstance(content, list):
                                parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                                content = " ".join(parts)
                            content_str = str(content or "")
                            was_empty = (
                                '"count": 0' in content_str
                                or '"total_matches": 0' in content_str
                            )

                            elapsed = (time.monotonic() - _current_tool_start) * 1000 if _current_tool_start else 0

                            # Record tool call
                            tool_call_records.append(ToolCallRecord(
                                name=_current_tool_name,
                                args=_current_tool_args,
                                result_size=len(content_str),
                                was_empty=was_empty,
                                elapsed_ms=elapsed,
                            ))

                            # Update search records with result info
                            if search_records and search_records[-1].tool == _current_tool_name:
                                search_records[-1].was_empty = was_empty
                                # Parse result count
                                try:
                                    result_data = json.loads(content_str)
                                    if "count" in result_data:
                                        search_records[-1].result_count = result_data["count"]
                                    elif "total_matches" in result_data:
                                        search_records[-1].result_count = result_data["total_matches"]
                                except (json.JSONDecodeError, TypeError):
                                    pass

                            # Track cross-references
                            if _current_tool_name == "cross_reference":
                                try:
                                    result_data = json.loads(content_str)
                                    cross_ref_records.append(CrossReferenceRecord(
                                        topic=_current_tool_args.get("topic", ""),
                                        sources_matched=result_data.get("sources_with_matches", []),
                                        total_matches=result_data.get("total_matches", 0),
                                    ))
                                except (json.JSONDecodeError, TypeError):
                                    pass

                            # Update PerceptionMetrics traces
                            if current_tool_trace:
                                current_tool_trace.completed_at = time.monotonic()
                                current_tool_trace.result_size = len(content_str)
                                current_tool_trace.was_empty = was_empty
                                self.metrics.tool_traces.append(current_tool_trace)
                                current_tool_trace = None

                            if on_discovery:
                                on_discovery("tool_result", content_str[:300])
                                # Structured metadata for recording (no raw content)
                                try:
                                    result_data = json.loads(content_str)
                                    meta = {
                                        "tool": _current_tool_name,
                                        "result_size": len(content_str),
                                        "was_empty": was_empty,
                                        "count": result_data.get("count", result_data.get("total_matches")),
                                        "sources_with_matches": result_data.get("sources_with_matches"),
                                    }
                                except (json.JSONDecodeError, TypeError):
                                    meta = {"tool": _current_tool_name, "result_size": len(content_str), "was_empty": was_empty}
                                on_discovery("tool_result_meta", json.dumps(meta))

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
                        stream_est = thinking_char_count // 4
                        self.metrics.thinking_tokens = sdk_thinking if sdk_thinking > 0 else stream_est
                    if on_discovery:
                        on_discovery("result", f"turns={self.metrics.num_turns} cost=${self.metrics.cost_usd:.4f}")

        if submitted_profile is None:
            raise RuntimeError(
                "Meta-learning perception completed without calling submit_profile."
            )

        # 6. Build profile
        profile = build_profile_from_submission(
            submitted_profile, self.user_id, events_count, sources, self.metrics.cost_usd,
        )
        if save:
            self.db.save_profile(profile)

        # 7. Evaluate profile score
        eval_result = self._evaluate_profile(profile, sources)
        profile_score = eval_result.total_score if hasattr(eval_result, 'total_score') else eval_result
        if on_discovery and hasattr(eval_result, 'dimensions'):
            on_discovery("eval_result", json.dumps({
                "total_score": eval_result.total_score,
                "total_pct": eval_result.total_pct,
                "dimensions": [
                    {"name": d.name, "score": d.score, "max_score": d.max_score, "detail": d.detail}
                    for d in eval_result.dimensions
                ],
            }))

        # 8. Build exploration trace
        duration = time.monotonic() - start_time
        run_id = f"meta_{run_number}_{int(time.time())}"
        trace = ExplorationTrace(
            run_id=run_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            tool_calls=tool_call_records,
            searches=search_records,
            cross_references=cross_ref_records,
            profile_score=profile_score,
            cost_usd=self.metrics.cost_usd,
            duration_seconds=round(duration, 1),
            strategy_version=strategy_version,
        )

        # 9. Deterministic reflection
        profile_text = self._profile_to_text(profile)
        trace = reflect_on_run(trace, profile_text)

        # 10. Archive the trace
        self.archive.add_trace(trace)

        if on_discovery:
            on_discovery("reflection",
                f"Reflection: {len(trace.useful_searches)} useful, "
                f"{len(trace.wasted_searches)} wasted, "
                f"{len(trace.discovered_connections)} connections")

        # 11. Evolve strategy every 3 runs
        if self.archive.run_count % 3 == 0:
            new_strategy = evolve_strategy(self.archive)
            self.archive.save_strategy(new_strategy)
            if on_discovery:
                on_discovery("evolution",
                    f"Strategy evolved to v{new_strategy.version} "
                    f"(from {new_strategy.derived_from_runs} runs)")

        return MetaRunResult(
            profile=profile,
            trace=trace,
            metrics=self.metrics,
            strategy_version=strategy_version,
        )

    def _evaluate_profile(self, profile: UserProfile, sources: list[str]):
        """Evaluate profile quality. Returns EvalResult (or float fallback)."""
        try:
            from experiments.perception.eval import evaluate_profile
            return evaluate_profile(profile, all_sources=sources, use_llm_judge=True)
        except Exception:
            # Fallback: simple heuristic — return float for compatibility
            score = 0.0
            if profile.identity_anchor:
                score += 0.2
            if profile.active_threads:
                score += min(len(profile.active_threads) * 0.05, 0.3)
            if profile.recent_detail:
                score += 0.2
            if profile.background_context:
                score += 0.15
            if profile.voice_patterns:
                score += 0.15
            return min(score, 1.0)

    def _profile_to_text(self, profile: UserProfile) -> str:
        """Flatten a profile to text for reflection matching."""
        parts = [
            profile.identity_anchor or "",
            profile.recent_detail or "",
            profile.background_context or "",
        ]
        for thread in profile.active_threads:
            parts.append(thread.name if hasattr(thread, "name") else str(thread))
            desc = thread.description if hasattr(thread, "description") else ""
            if desc:
                parts.append(desc)
        if profile.voice_patterns:
            if isinstance(profile.voice_patterns, dict):
                parts.append(profile.voice_patterns.get("tone", ""))
                parts.extend(profile.voice_patterns.get("vocabulary_notes", []))
        return " ".join(parts)
