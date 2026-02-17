"""Exploration archive — traces, strategies, and ALMA sampling for meta-learning perception.

Persists to disk in data/{user}/exploration_archive/. Each perception run leaves
a trace; deterministic reflection labels what worked; strategies evolve over time.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from syke.config import user_data_dir


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ToolCallRecord:
    """Record of a single tool call during a perception run."""

    name: str
    args: dict[str, Any]
    result_size: int = 0
    was_empty: bool = False
    elapsed_ms: float = 0.0


@dataclass
class SearchRecord:
    """Record of a search or cross-reference query."""

    query: str
    tool: str  # search_footprint or cross_reference
    was_empty: bool = False
    result_count: int = 0


@dataclass
class CrossReferenceRecord:
    """Record of a cross-platform discovery."""

    topic: str
    sources_matched: list[str] = field(default_factory=list)
    total_matches: int = 0


@dataclass
class ExplorationTrace:
    """Full record of one perception run — what the agent did, what it found."""

    run_id: str
    timestamp: str  # ISO
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    searches: list[SearchRecord] = field(default_factory=list)
    cross_references: list[CrossReferenceRecord] = field(default_factory=list)

    # Post-reflection labels (filled by reflection.py)
    useful_searches: list[str] = field(default_factory=list)
    wasted_searches: list[str] = field(default_factory=list)
    discovered_connections: list[dict[str, Any]] = field(default_factory=list)

    profile_score: float = 0.0
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    strategy_version: int = 0  # which strategy was active

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "tool_calls": [
                {"name": tc.name, "args": tc.args, "result_size": tc.result_size,
                 "was_empty": tc.was_empty, "elapsed_ms": tc.elapsed_ms}
                for tc in self.tool_calls
            ],
            "searches": [
                {"query": s.query, "tool": s.tool, "was_empty": s.was_empty,
                 "result_count": s.result_count}
                for s in self.searches
            ],
            "cross_references": [
                {"topic": cr.topic, "sources_matched": cr.sources_matched,
                 "total_matches": cr.total_matches}
                for cr in self.cross_references
            ],
            "useful_searches": self.useful_searches,
            "wasted_searches": self.wasted_searches,
            "discovered_connections": self.discovered_connections,
            "profile_score": self.profile_score,
            "cost_usd": self.cost_usd,
            "duration_seconds": self.duration_seconds,
            "strategy_version": self.strategy_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExplorationTrace:
        trace = cls(
            run_id=data["run_id"],
            timestamp=data["timestamp"],
            profile_score=data.get("profile_score", 0.0),
            cost_usd=data.get("cost_usd", 0.0),
            duration_seconds=data.get("duration_seconds", 0.0),
            strategy_version=data.get("strategy_version", 0),
            useful_searches=data.get("useful_searches", []),
            wasted_searches=data.get("wasted_searches", []),
            discovered_connections=data.get("discovered_connections", []),
        )
        for tc in data.get("tool_calls", []):
            trace.tool_calls.append(ToolCallRecord(
                name=tc["name"], args=tc.get("args", {}),
                result_size=tc.get("result_size", 0),
                was_empty=tc.get("was_empty", False),
                elapsed_ms=tc.get("elapsed_ms", 0.0),
            ))
        for s in data.get("searches", []):
            trace.searches.append(SearchRecord(
                query=s["query"], tool=s["tool"],
                was_empty=s.get("was_empty", False),
                result_count=s.get("result_count", 0),
            ))
        for cr in data.get("cross_references", []):
            trace.cross_references.append(CrossReferenceRecord(
                topic=cr["topic"],
                sources_matched=cr.get("sources_matched", []),
                total_matches=cr.get("total_matches", 0),
            ))
        return trace


@dataclass
class ProductiveSearch:
    """A search query that has proven useful across runs."""

    query: str
    hit_rate: float = 0.0  # fraction of runs where it returned results
    relevance_score: float = 0.0  # fraction of runs where it appeared in final profile


@dataclass
class CrossPlatformTopic:
    """A topic that spans multiple platforms."""

    topic: str
    sources: list[str] = field(default_factory=list)
    strength: float = 0.0  # normalized match count


@dataclass
class ExplorationStrategy:
    """Evolved exploration knowledge — what works for this user."""

    version: int = 0
    productive_searches: list[ProductiveSearch] = field(default_factory=list)
    dead_end_searches: list[str] = field(default_factory=list)
    source_priorities: dict[str, float] = field(default_factory=dict)
    cross_platform_topics: list[CrossPlatformTopic] = field(default_factory=list)
    recommended_tool_sequence: list[str] = field(default_factory=list)
    derived_from_runs: int = 0
    total_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "productive_searches": [
                {"query": ps.query, "hit_rate": ps.hit_rate,
                 "relevance_score": ps.relevance_score}
                for ps in self.productive_searches
            ],
            "dead_end_searches": self.dead_end_searches,
            "source_priorities": self.source_priorities,
            "cross_platform_topics": [
                {"topic": ct.topic, "sources": ct.sources, "strength": ct.strength}
                for ct in self.cross_platform_topics
            ],
            "recommended_tool_sequence": self.recommended_tool_sequence,
            "derived_from_runs": self.derived_from_runs,
            "total_cost_usd": self.total_cost_usd,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExplorationStrategy:
        strat = cls(
            version=data.get("version", 0),
            dead_end_searches=data.get("dead_end_searches", []),
            source_priorities=data.get("source_priorities", {}),
            recommended_tool_sequence=data.get("recommended_tool_sequence", []),
            derived_from_runs=data.get("derived_from_runs", 0),
            total_cost_usd=data.get("total_cost_usd", 0.0),
        )
        for ps in data.get("productive_searches", []):
            strat.productive_searches.append(ProductiveSearch(
                query=ps["query"],
                hit_rate=ps.get("hit_rate", 0.0),
                relevance_score=ps.get("relevance_score", 0.0),
            ))
        for ct in data.get("cross_platform_topics", []):
            strat.cross_platform_topics.append(CrossPlatformTopic(
                topic=ct["topic"],
                sources=ct.get("sources", []),
                strength=ct.get("strength", 0.0),
            ))
        return strat

    def summary(self, max_items: int = 5) -> str:
        """Human-readable summary for prompt injection."""
        parts = [f"Strategy v{self.version} (derived from {self.derived_from_runs} runs)"]

        if self.productive_searches:
            top = sorted(self.productive_searches, key=lambda p: p.relevance_score, reverse=True)[:max_items]
            queries = [f"'{p.query}' (hit={p.hit_rate:.0%}, rel={p.relevance_score:.0%})" for p in top]
            parts.append(f"Productive searches: {', '.join(queries)}")

        if self.dead_end_searches:
            parts.append(f"Dead ends (avoid): {', '.join(self.dead_end_searches[:max_items])}")

        if self.source_priorities:
            ranked = sorted(self.source_priorities.items(), key=lambda x: x[1], reverse=True)
            parts.append(f"Source priority: {' > '.join(f'{s} ({v:.2f})' for s, v in ranked)}")

        if self.cross_platform_topics:
            topics = [f"'{ct.topic}' ({', '.join(ct.sources)})" for ct in self.cross_platform_topics[:max_items]]
            parts.append(f"Cross-platform connections: {', '.join(topics)}")

        if self.recommended_tool_sequence:
            parts.append(f"Recommended opening: {' -> '.join(self.recommended_tool_sequence[:6])}")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Archive — disk persistence + ALMA sampling
# ---------------------------------------------------------------------------

class ExplorationArchive:
    """Persistent archive of exploration traces and strategies.

    Stores in data/{user}/exploration_archive/:
    - traces/trace_{run_id}.json
    - strategies/strategy_v{N}.json
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._dir = user_data_dir(user_id) / "exploration_archive"
        self._traces_dir = self._dir / "traces"
        self._strats_dir = self._dir / "strategies"
        self._traces_dir.mkdir(parents=True, exist_ok=True)
        self._strats_dir.mkdir(parents=True, exist_ok=True)

        self.traces: list[ExplorationTrace] = []
        self.strategies: list[ExplorationStrategy] = []
        self._load()

    def _load(self) -> None:
        """Load existing traces and strategies from disk."""
        import logging
        logger = logging.getLogger("syke")

        for f in sorted(self._traces_dir.glob("trace_*.json")):
            try:
                data = json.loads(f.read_text())
                self.traces.append(ExplorationTrace.from_dict(data))
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Skipping corrupt trace %s: %s", f.name, e)

        for f in sorted(self._strats_dir.glob("strategy_v*.json")):
            try:
                data = json.loads(f.read_text())
                self.strategies.append(ExplorationStrategy.from_dict(data))
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Skipping corrupt strategy %s: %s", f.name, e)

    def add_trace(self, trace: ExplorationTrace) -> None:
        """Store a trace to disk and memory."""
        self.traces.append(trace)
        path = self._traces_dir / f"trace_{trace.run_id}.json"
        path.write_text(json.dumps(trace.to_dict(), indent=2))

    def save_strategy(self, strategy: ExplorationStrategy) -> None:
        """Store a strategy to disk and memory."""
        self.strategies.append(strategy)
        path = self._strats_dir / f"strategy_v{strategy.version}.json"
        path.write_text(json.dumps(strategy.to_dict(), indent=2))

    def get_latest_strategy(self) -> ExplorationStrategy | None:
        """Return the most recent strategy, or None if no strategies exist."""
        if not self.strategies:
            return None
        return max(self.strategies, key=lambda s: s.version)

    def get_traces(self, limit: int = 5) -> list[ExplorationTrace]:
        """Return the most recent traces, newest first."""
        return sorted(self.traces, key=lambda t: t.timestamp, reverse=True)[:limit]

    @property
    def run_count(self) -> int:
        return len(self.traces)

    def sample_traces(self, k: int = 3) -> list[ExplorationTrace]:
        """ALMA-style sampling: bias toward high-scoring but recent traces.

        Uses softmax over (profile_score - recency_penalty) similar to
        ArchiveEntry.final_score in schema_free_perceiver.py.
        """
        if not self.traces:
            return []
        if len(self.traces) <= k:
            return list(self.traces)

        # Score = profile_score normalized via sigmoid - age penalty
        now = time.time()
        scores = []
        for t in self.traces:
            normalized = 1 / (1 + math.exp(-(t.profile_score - 0.5)))
            # Age penalty: older traces get penalized
            try:
                from datetime import datetime
                ts = datetime.fromisoformat(t.timestamp).timestamp()
                age_days = (now - ts) / 86400
            except (ValueError, OSError):
                age_days = 0
            penalty = 0.1 * math.log1p(age_days)
            scores.append(normalized - penalty)

        # Softmax selection (deterministic top-k)
        max_score = max(scores)
        exp_scores = [math.exp(s - max_score) for s in scores]
        total = sum(exp_scores)
        probs = [s / total for s in exp_scores]

        indexed = sorted(enumerate(probs), key=lambda x: x[1], reverse=True)
        return [self.traces[idx] for idx, _ in indexed[:k]]
