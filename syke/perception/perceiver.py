"""Opus 4.6 perception engine — builds identity profiles from cross-platform event timelines."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from syke.db import SykeDB
from syke.llm.client import LLMClient, LLMResponse
from syke.models import UserProfile
from syke.perception.prompts import PERCEPTION_SYSTEM, INCREMENTAL_SYSTEM

# Character budgets per bucket (~4 chars per token)
RECENT_CHAR_BUDGET = 400_000  # ~100K tokens for recent
MEDIUM_CHAR_BUDGET = 120_000  # ~30K tokens for medium
BACKGROUND_CHAR_BUDGET = 40_000  # ~10K tokens for background


class Perceiver:
    """Reads the timeline, builds recency-weighted context, calls Opus 4.6 with extended thinking."""

    def __init__(self, db: SykeDB, user_id: str, client: LLMClient | None = None):
        self.db = db
        self.user_id = user_id
        self.client = client or LLMClient()
        self.last_response: LLMResponse | None = None

    def perceive(self, full: bool = True, save: bool = True) -> UserProfile:
        """Run perception on the user's timeline.

        Args:
            full: If True, full perception from scratch. If False, incremental update.
            save: If True, save the profile to DB. Set False for benchmarks.
        """
        # Build recency-weighted timeline text
        timeline_text = self._build_timeline_text()
        events_count = self.db.count_events(self.user_id)
        sources = self.db.get_sources(self.user_id)

        if full or not self.db.get_latest_profile(self.user_id):
            return self._full_perception(timeline_text, events_count, sources, save=save)
        else:
            return self._incremental_perception(timeline_text, events_count, sources, save=save)

    def _build_timeline_text(self) -> str:
        """Build recency-weighted timeline text from events.

        - Last 2 weeks: full event detail (budget-aware truncation)
        - 2-8 weeks: summarized events
        - 8+ weeks: source-aware sampled key events
        """
        now = datetime.now(UTC)
        two_weeks_ago = (now - timedelta(weeks=2)).isoformat()
        eight_weeks_ago = (now - timedelta(weeks=8)).isoformat()

        sections = []

        # Recent (last 2 weeks) — full detail with budget-aware truncation
        recent = self.db.get_events(self.user_id, since=two_weeks_ago, limit=500)
        recent = self._dedup_events(recent)
        if recent:
            per_event_limit = max(500, RECENT_CHAR_BUDGET // len(recent))
            sections.append("## RECENT (Last 2 Weeks) — Full Detail\n")
            for ev in recent:
                sections.append(self._format_event_full(ev, max_content=per_event_limit))

        # Medium (2-8 weeks) — summarized, using since+before for clean range
        # Cap at 400 events (~300 chars each ≈ 120K budget)
        medium = self.db.get_events(
            self.user_id, since=eight_weeks_ago, before=two_weeks_ago, limit=2000
        )
        medium = self._dedup_events(medium)
        if len(medium) > 400:
            medium = self._sample_by_source(medium, target_per_source=120)
        if medium:
            per_event_limit = max(200, MEDIUM_CHAR_BUDGET // len(medium))
            sections.append("\n## MEDIUM TERM (2-8 Weeks) — Summarized\n")
            for ev in medium:
                sections.append(self._format_event_summary(ev, max_content=per_event_limit))

        # Background (8+ weeks) — source-aware sampling
        older = self.db.get_events(self.user_id, before=eight_weeks_ago, limit=5000)
        older = self._dedup_events(older)
        if older:
            sampled = self._sample_by_source(older, target_per_source=70)
            sections.append("\n## BACKGROUND (8+ Weeks) — Key Events\n")
            for ev in sampled:
                sections.append(self._format_event_brief(ev))

        return "\n".join(sections)

    def _sample_by_source(self, events: list[dict], target_per_source: int = 70) -> list[dict]:
        """Sample events proportionally from each source for balanced representation."""
        by_source: dict[str, list[dict]] = defaultdict(list)
        for ev in events:
            by_source[ev["source"]].append(ev)

        sampled = []
        for source, source_events in by_source.items():
            step = max(1, len(source_events) // target_per_source)
            sampled.extend(source_events[::step])

        sampled.sort(key=lambda e: e["timestamp"])
        return sampled

    def _dedup_events(self, events: list[dict]) -> list[dict]:
        """Remove events with identical content prefixes (first 500 chars).

        Catches duplicate sessions and continuation boilerplate where the
        same opening text appears in multiple events.
        """
        seen: set[str] = set()
        result = []
        for ev in events:
            key = (ev.get("content") or "")[:500]
            if key not in seen:
                seen.add(key)
                result.append(ev)
        return result

    def _extract_summary(self, ev: dict) -> str:
        """Extract session summary from event metadata if available."""
        meta = ev.get("metadata", {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        return meta.get("summary", "") if isinstance(meta, dict) else ""

    def _format_event_full(self, ev: dict, max_content: int = 3000) -> str:
        """Full event detail with budget-aware truncation."""
        title = ev.get("title") or ""
        content = ev.get("content", "")

        # Front-load session summary if available (survives truncation)
        summary = self._extract_summary(ev)
        if summary:
            content = f"[Session summary] {summary}\n\n{content}"

        if len(content) > max_content:
            content = content[:max_content] + "..."
        return (
            f"### [{ev['source']}] {ev['event_type']} — {ev['timestamp'][:16]}\n"
            f"**{title}**\n{content}\n"
        )

    def _format_event_summary(self, ev: dict, max_content: int = 500) -> str:
        """Summarized event with optional session summary."""
        title = ev.get("title") or ""

        # Prefer session summary over raw content for medium-term events
        summary = self._extract_summary(ev)
        if summary:
            content = summary[:max_content]
        else:
            content = (ev.get("content", "") or "")[:max_content]

        return (
            f"- [{ev['source']}] {ev['event_type']} — {ev['timestamp'][:10]} — "
            f"{title}: {content}\n"
        )

    def _format_event_brief(self, ev: dict) -> str:
        """Brief event reference."""
        title = ev.get("title") or ev.get("content", "")[:80]
        return f"- [{ev['source']}] {ev['timestamp'][:10]}: {title}\n"

    def _full_perception(
        self, timeline_text: str, events_count: int, sources: list[str], save: bool = True
    ) -> UserProfile:
        """Full perception from scratch using extended thinking."""
        response = self.client.chat(
            messages=[
                {
                    "role": "user",
                    "content": f"Here is the complete digital footprint timeline for user '{self.user_id}'.\n\nSources: {', '.join(sources)}\nTotal events: {events_count}\n\n{timeline_text}",
                }
            ],
            system=PERCEPTION_SYSTEM,
            max_tokens=16000,
            thinking=True,
            thinking_budget=16000,
        )
        self.last_response = response

        profile = self._parse_profile(response.content, events_count, sources)
        profile.thinking_tokens = response.thinking_tokens
        profile.cost_usd = response.cost_usd

        if save:
            self.db.save_profile(profile)

        return profile

    def _build_new_events_timeline(self, since: str) -> str:
        """Build timeline text from events ingested after the given timestamp.

        Uses ingested_at (when Syke received the event) rather than the event's
        own timestamp. This is critical for pushed events whose timestamp may be
        days or weeks in the past — we need to show the perceiver everything it
        hasn't seen yet, regardless of when the event originally occurred.
        """
        new_events = self.db.get_events_since_ingestion(
            self.user_id, since_ingested=since, limit=500
        )
        new_events = self._dedup_events(new_events)
        if not new_events:
            return ""
        sections = ["## New Events Since Last Perception\n"]
        per_event_limit = max(500, RECENT_CHAR_BUDGET // len(new_events))
        for ev in new_events:
            sections.append(self._format_event_full(ev, max_content=per_event_limit))
        return "\n".join(sections)

    def _incremental_perception(
        self, timeline_text: str, events_count: int, sources: list[str], save: bool = True
    ) -> UserProfile:
        """Incremental update using previous profile + new events only."""
        prev = self.db.get_latest_profile(self.user_id)
        prev_json = prev.model_dump_json(indent=2) if prev else "{}"

        # Build new-events-only timeline instead of sending full timeline
        last_profile_ts = self.db.get_last_profile_timestamp(self.user_id)
        if last_profile_ts:
            new_timeline = self._build_new_events_timeline(last_profile_ts)
        else:
            new_timeline = timeline_text  # fallback to full

        # Count new events for accurate reporting (by ingestion time, not event time)
        new_event_count = len(self.db.get_events_since_ingestion(
            self.user_id, since_ingested=last_profile_ts, limit=500
        )) if last_profile_ts else events_count

        response = self.client.chat(
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"## Previous Profile\n{prev_json}\n\n"
                        f"## New Events Since Last Perception\n"
                        f"Sources: {', '.join(sources)}\n"
                        f"New events: {new_event_count} (total: {events_count})\n\n"
                        f"{new_timeline}"
                    ),
                }
            ],
            system=INCREMENTAL_SYSTEM,
            max_tokens=16000,
            thinking=True,
            thinking_budget=16000,
        )
        self.last_response = response

        profile = self._parse_profile(response.content, events_count, sources)
        profile.thinking_tokens = response.thinking_tokens
        profile.cost_usd = response.cost_usd

        if save:
            self.db.save_profile(profile)

        return profile

    def _extract_json(self, content: str) -> dict:
        """Extract JSON from LLM response, handling optional markdown fences."""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start:end])
                except json.JSONDecodeError:
                    pass
            raise ValueError(f"Could not parse profile JSON from response: {content[:200]}")

    def _parse_profile(
        self, content: str, events_count: int, sources: list[str]
    ) -> UserProfile:
        """Parse the LLM output into a UserProfile."""
        data = self._extract_json(content)

        return UserProfile(
            user_id=self.user_id,
            identity_anchor=data.get("identity_anchor", ""),
            active_threads=data.get("active_threads", []),
            recent_detail=data.get("recent_detail", ""),
            background_context=data.get("background_context", ""),
            world_state=data.get("world_state", ""),
            voice_patterns=data.get("voice_patterns") or None,
            sources=sources,
            events_count=events_count,
            model=self.client.model,
        )
