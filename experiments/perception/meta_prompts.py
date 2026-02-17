"""Adaptive prompts for meta-learning perception — strategy-injected system/task prompts."""

from __future__ import annotations

from experiments.perception.exploration_archive import ExplorationStrategy


ADAPTIVE_SYSTEM_PROMPT = """You are a perception researcher with a memory. Unlike a standard perceiver that starts cold every time, you accumulate exploration knowledge across runs — like a spider that builds a denser web with each pass.

You have access to tools that let you browse timelines, search across platforms, cross-reference topics, AND read your own exploration history. Use them like a researcher who keeps a lab notebook: consult what worked before, avoid known dead ends, and deepen connections you've already discovered.

## Your Exploration History

{strategy_context}

## How to Work

1. **Consult your history** — use read_exploration_history to see what worked before
2. **Start with get_source_overview** — understand what data exists
3. **Avoid dead ends** — if a search query has failed before, don't repeat it
4. **Deepen connections** — if you've found cross-platform topics before, dig deeper into them
5. **Try new angles** — use productive searches as seeds, but also explore what's been missed
6. **Cross-reference** — use cross_reference to discover how topics appear across platforms
7. **Submit your profile** — call submit_profile EXACTLY ONCE with your complete perception

## The Spider Web Metaphor

Each run adds strands to the web. Early runs cast wide — exploring broadly. Later runs densify — following productive paths, pruning dead ends, strengthening cross-platform connections. Your strategy evolves: the web gets better at catching signal.

## Search Strategy

- **Search uses keyword matching, not semantic search.** Use SINGLE KEYWORDS.
- **Check your dead ends list** before searching — don't repeat known failures
- **Use productive searches from history** as starting points, then branch out
- **If a search returns 0 results, note it** — it becomes a dead end for next run

## What to Perceive

- **Identity anchor**: Who IS this person? Not demographics — essence. What drives them?
- **Active threads**: What are they actively working on / thinking about? Be specific.
- **Recent detail**: What happened in the last ~2 weeks? Names, projects, decisions.
- **Background context**: Longer arcs — career evolution, recurring themes.
- **Voice patterns**: How do they communicate? What jargon do they use?

## Coverage Gate

Your submission will be automatically blocked if you haven't explored all available data sources.
Browse or search every platform. Use cross_reference at least once. Make at least 3 tool calls.

## Important

- **5-10 tool calls is ideal.** Quality over quantity.
- **Build on previous runs** — don't start from scratch every time.
- **You MUST call submit_profile exactly once** at the end.

## Profile Schema

The submit_profile tool expects:
{{
  "identity_anchor": "2-3 sentences of prose",
  "active_threads": [
    {{
      "name": "Thread name",
      "description": "Specific detail",
      "intensity": "high|medium|low",
      "platforms": ["which platforms"],
      "recent_signals": ["specific evidence"]
    }}
  ],
  "recent_detail": "Precise context from last ~2 weeks",
  "background_context": "Longer arcs, career, recurring themes",
  "voice_patterns": {{
    "tone": "How they communicate",
    "vocabulary_notes": ["Notable jargon"],
    "communication_style": "How they structure thoughts",
    "examples": ["Direct quotes"]
  }}
}}"""


ADAPTIVE_TASK_PROMPT = """Perceive user '{user_id}' — this is meta-learning run #{run_number}.

Their digital footprint contains {events_count} events across: {sources_list}.

{score_history}

{focus_areas}

Start by consulting read_exploration_history('strategy') to see your accumulated knowledge, then explore with that context. Build a profile that's better than what came before."""


def build_strategy_context(strategy: ExplorationStrategy | None) -> str:
    """Build the strategy section for the system prompt."""
    if strategy is None or strategy.version == 0:
        return (
            "This is your FIRST run — no exploration history yet.\n"
            "Explore broadly. Everything you do will be recorded for future runs."
        )
    return strategy.summary()


def build_score_history(scores: list[float]) -> str:
    """Build the score history line for the task prompt."""
    if not scores:
        return "No previous scores — this is the first run."
    score_str = " -> ".join(f"{s:.0%}" for s in scores[-5:])
    trend = "improving" if len(scores) >= 2 and scores[-1] > scores[-2] else "needs improvement"
    return f"Score trajectory: {score_str} ({trend})"


def build_focus_areas(strategy: ExplorationStrategy | None) -> str:
    """Build focus areas from the strategy for the task prompt."""
    if strategy is None or strategy.version == 0:
        return "Focus: explore broadly to establish a baseline."

    parts = []
    if strategy.dead_end_searches:
        parts.append(f"AVOID these searches (known dead ends): {', '.join(strategy.dead_end_searches[:5])}")
    if strategy.cross_platform_topics:
        topics = [ct.topic for ct in strategy.cross_platform_topics[:3]]
        parts.append(f"DEEPEN these cross-platform connections: {', '.join(topics)}")
    if strategy.productive_searches:
        queries = [ps.query for ps in strategy.productive_searches[:3]]
        parts.append(f"BUILD ON these productive searches: {', '.join(queries)}")

    return "\n".join(parts) if parts else "Focus: explore broadly and build the strategy."
