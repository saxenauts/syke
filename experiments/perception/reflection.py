"""Deterministic reflection — zero LLM cost trace analysis and strategy evolution.

Labels each search as useful/wasted by checking if query terms appear in the final
profile. Evolves strategies by aggregating traces weighted by profile score.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from experiments.perception.exploration_archive import (
    CrossPlatformTopic,
    ExplorationArchive,
    ExplorationStrategy,
    ExplorationTrace,
    ProductiveSearch,
)


def reflect_on_run(trace: ExplorationTrace, profile_text: str) -> ExplorationTrace:
    """Label searches as useful or wasted based on the final profile.

    A search is "useful" if any word in the query (len >= 3) appears in the
    profile text. Otherwise it's "wasted". Also extracts cross-platform
    connections from cross_reference results.

    Mutates and returns the trace (fills useful_searches, wasted_searches,
    discovered_connections).
    """
    profile_lower = profile_text.lower()

    useful = []
    wasted = []

    for search in trace.searches:
        query_words = [w.lower() for w in search.query.split() if len(w) >= 3]
        if search.was_empty:
            wasted.append(search.query)
            continue

        # Check if any query term appears in the final profile
        found = any(word in profile_lower for word in query_words)
        if found:
            useful.append(search.query)
        else:
            wasted.append(search.query)

    trace.useful_searches = useful
    trace.wasted_searches = wasted

    # Extract cross-platform connections
    connections = []
    for cr in trace.cross_references:
        if len(cr.sources_matched) >= 2:
            connections.append({
                "topic": cr.topic,
                "sources": cr.sources_matched,
                "matches": cr.total_matches,
            })
    trace.discovered_connections = connections

    return trace


def evolve_strategy(archive: ExplorationArchive) -> ExplorationStrategy:
    """Evolve a new strategy from all traces in the archive.

    Deterministic — no LLM calls. Aggregates:
    - Productive searches: queries that were labeled useful, weighted by profile score
    - Dead ends: queries that were empty 2+ consecutive times
    - Source priorities: sources that appear in high-scoring traces
    - Cross-platform topics: connections found across traces
    - Recommended tool sequence: from the highest-scoring trace

    Returns a new ExplorationStrategy with incremented version.
    """
    traces = archive.traces
    if not traces:
        return ExplorationStrategy(version=1, derived_from_runs=0)

    current = archive.get_latest_strategy()
    new_version = (current.version + 1) if current else 1

    # --- Productive searches ---
    # Accumulate hits/relevance across traces, weighted by profile score
    search_hits: dict[str, list[bool]] = defaultdict(list)  # query -> [was_useful]
    search_relevance: dict[str, float] = defaultdict(float)  # query -> sum of scores

    for trace in traces:
        weight = max(trace.profile_score, 0.1)  # minimum weight
        for q in trace.useful_searches:
            search_hits[q].append(True)
            search_relevance[q] += weight
        for q in trace.wasted_searches:
            search_hits[q].append(False)

    productive = []
    for query, hits in search_hits.items():
        hit_rate = sum(hits) / len(hits) if hits else 0.0
        # Only include searches with > 50% hit rate
        if hit_rate > 0.5:
            max_possible = len(traces) * 1.0  # max score per trace is 1.0
            relevance = search_relevance[query] / max_possible if max_possible > 0 else 0.0
            productive.append(ProductiveSearch(
                query=query, hit_rate=hit_rate, relevance_score=min(relevance, 1.0),
            ))

    productive.sort(key=lambda p: p.relevance_score, reverse=True)

    # --- Dead ends ---
    # Queries that returned empty in 2+ consecutive traces
    empty_counter: Counter[str] = Counter()
    for trace in traces:
        for search in trace.searches:
            if search.was_empty:
                empty_counter[search.query] += 1

    dead_ends = [q for q, count in empty_counter.items() if count >= 2]

    # --- Source priorities ---
    # Weight each source by the profile scores of traces that explored it
    source_scores: dict[str, list[float]] = defaultdict(list)
    for trace in traces:
        explored_sources = set()
        for tc in trace.tool_calls:
            if tc.name == "browse_timeline":
                src = tc.args.get("source")
                if src:
                    explored_sources.add(src)
            elif tc.name in ("search_footprint", "cross_reference"):
                # These explore all sources implicitly
                pass
        for cr in trace.cross_references:
            explored_sources.update(cr.sources_matched)
        for src in explored_sources:
            source_scores[src].append(trace.profile_score)

    source_priorities = {}
    for src, scores in source_scores.items():
        avg = sum(scores) / len(scores) if scores else 0.0
        source_priorities[src] = round(avg, 3)

    # --- Cross-platform topics ---
    # Aggregate connections across traces
    topic_sources: dict[str, set[str]] = defaultdict(set)
    topic_strength: dict[str, float] = defaultdict(float)
    for trace in traces:
        for conn in trace.discovered_connections:
            topic = conn["topic"]
            topic_sources[topic].update(conn.get("sources", []))
            topic_strength[topic] += conn.get("matches", 0)

    cross_platform = []
    for topic, sources in topic_sources.items():
        if len(sources) >= 2:
            # Normalize strength
            max_strength = max(topic_strength.values()) if topic_strength else 1.0
            strength = topic_strength[topic] / max_strength if max_strength > 0 else 0.0
            cross_platform.append(CrossPlatformTopic(
                topic=topic, sources=sorted(sources), strength=round(strength, 3),
            ))
    cross_platform.sort(key=lambda ct: ct.strength, reverse=True)

    # --- Recommended tool sequence ---
    # Use the tool sequence from the highest-scoring trace
    best_trace = max(traces, key=lambda t: t.profile_score)
    recommended = [tc.name for tc in best_trace.tool_calls]

    # --- Totals ---
    total_cost = sum(t.cost_usd for t in traces)

    return ExplorationStrategy(
        version=new_version,
        productive_searches=productive[:20],  # cap at 20
        dead_end_searches=dead_ends[:20],
        source_priorities=source_priorities,
        cross_platform_topics=cross_platform[:10],
        recommended_tool_sequence=recommended,
        derived_from_runs=len(traces),
        total_cost_usd=round(total_cost, 4),
    )
