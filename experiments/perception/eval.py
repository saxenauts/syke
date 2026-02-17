"""Perception evaluation — scores a profile on quality dimensions.

This is the missing eval function for ALMA-style iteration. It answers:
"What makes a good perception profile?" with a concrete numeric score.

Dimensions:
- Thread quality: cross-platform signal, specificity, evidence richness
- Identity anchor: length, specificity, avoids generic platitudes
- Voice patterns: richness of tone, vocabulary, examples
- Source coverage: fraction of available sources represented
- Structural completeness: all fields populated with substance
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from syke.models import UserProfile


@dataclass
class DimensionScore:
    """Score for a single evaluation dimension."""

    name: str
    score: float  # 0.0 to 1.0
    max_score: float = 1.0
    detail: str = ""


@dataclass
class EvalResult:
    """Complete evaluation of a perception profile."""

    profile_user_id: str
    dimensions: list[DimensionScore] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        """Weighted composite score (0.0 to 1.0)."""
        if not self.dimensions:
            return 0.0
        return sum(d.score * d.max_score for d in self.dimensions) / sum(d.max_score for d in self.dimensions)

    @property
    def total_pct(self) -> float:
        """Score as a percentage."""
        return self.total_score * 100

    def by_name(self, name: str) -> DimensionScore | None:
        for d in self.dimensions:
            if d.name == name:
                return d
        return None


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def _score_thread_quality(profile: UserProfile, all_sources: list[str] | None = None) -> DimensionScore:
    """Score active threads on cross-platform signal, specificity, evidence."""
    threads = profile.active_threads
    if not threads:
        return DimensionScore(name="thread_quality", score=0.0, max_score=1.5, detail="No threads")

    scores = []

    # 1. Cross-platform threads (threads spanning 2+ platforms)
    cross_platform = sum(1 for t in threads if len(getattr(t, "platforms", []) or []) >= 2)
    cross_ratio = cross_platform / len(threads)
    scores.append(cross_ratio)

    # 2. Thread count — sweet spot is 4-8
    count = len(threads)
    if count < 2:
        count_score = 0.3
    elif count <= 8:
        count_score = min(1.0, count / 5)
    else:
        count_score = max(0.5, 1.0 - (count - 8) * 0.1)
    scores.append(count_score)

    # 3. Specificity — threads with longer descriptions + recent signals are more specific
    specificity_scores = []
    for t in threads:
        desc_len = len(getattr(t, "description", "") or "")
        signals = getattr(t, "recent_signals", []) or []
        # Description should be 30+ chars, each signal 10+ chars
        desc_score = min(1.0, desc_len / 80)
        signal_score = min(1.0, len(signals) / 3)
        specificity_scores.append((desc_score + signal_score) / 2)
    avg_specificity = sum(specificity_scores) / len(specificity_scores) if specificity_scores else 0.0
    scores.append(avg_specificity)

    # 4. Intensity distribution — good profiles have a mix of high/medium/low
    intensities = [getattr(t, "intensity", "medium") for t in threads]
    unique_intensities = len(set(intensities))
    intensity_score = min(1.0, unique_intensities / 3)
    scores.append(intensity_score)

    total = sum(scores) / len(scores)
    detail = (
        f"{len(threads)} threads, {cross_platform} cross-platform, "
        f"specificity={avg_specificity:.2f}, intensity_diversity={unique_intensities}"
    )
    return DimensionScore(name="thread_quality", score=total, max_score=1.5, detail=detail)


def _score_identity_anchor(profile: UserProfile) -> DimensionScore:
    """Score the identity anchor on length, specificity, and depth."""
    anchor = profile.identity_anchor or ""
    if not anchor:
        return DimensionScore(name="identity_anchor", score=0.0, max_score=1.0, detail="Empty")

    scores = []

    # Length — sweet spot is 150-500 chars (2-3 meaningful sentences)
    length = len(anchor)
    if length < 50:
        len_score = 0.2
    elif length < 150:
        len_score = 0.5
    elif length <= 500:
        len_score = 1.0
    else:
        len_score = max(0.6, 1.0 - (length - 500) * 0.001)
    scores.append(len_score)

    # Specificity — contains proper nouns, numbers, or technical terms
    specificity_signals = 0
    words = anchor.split()
    capitalized = sum(1 for w in words if w[0:1].isupper() and len(w) > 1)
    specificity_signals += min(5, capitalized)
    # Numbers/dates
    numbers = len(re.findall(r'\d+', anchor))
    specificity_signals += min(3, numbers)
    spec_score = min(1.0, specificity_signals / 5)
    scores.append(spec_score)

    # Depth — avoid generic platitudes; look for words that indicate real insight
    depth_markers = [
        "tension", "contrast", "oscillat", "caught between", "drives",
        "obsess", "struggle", "vision", "grind", "philosophy",
        "paradox", "identity", "essence", "conviction",
    ]
    depth_count = sum(1 for m in depth_markers if m.lower() in anchor.lower())
    depth_score = min(1.0, depth_count / 3)
    scores.append(depth_score)

    total = sum(scores) / len(scores)
    detail = f"{length} chars, specificity={spec_score:.2f}, depth={depth_score:.2f}"
    return DimensionScore(name="identity_anchor", score=total, max_score=1.0, detail=detail)


def _score_voice_patterns(profile: UserProfile) -> DimensionScore:
    """Score voice pattern richness."""
    vp = profile.voice_patterns
    if not vp:
        return DimensionScore(name="voice_patterns", score=0.0, max_score=1.0, detail="Not detected")

    scores = []

    # Tone presence and specificity
    tone = getattr(vp, "tone", "") or ""
    tone_score = min(1.0, len(tone) / 30)
    scores.append(tone_score)

    # Vocabulary notes
    vocab = getattr(vp, "vocabulary_notes", []) or []
    vocab_score = min(1.0, len(vocab) / 4)
    scores.append(vocab_score)

    # Communication style
    style = getattr(vp, "communication_style", "") or ""
    style_score = min(1.0, len(style) / 40)
    scores.append(style_score)

    # Direct quotes/examples
    examples = getattr(vp, "examples", []) or []
    example_score = min(1.0, len(examples) / 3)
    scores.append(example_score)

    total = sum(scores) / len(scores)
    detail = (
        f"tone={len(tone)}ch, vocab={len(vocab)} items, "
        f"style={len(style)}ch, examples={len(examples)}"
    )
    return DimensionScore(name="voice_patterns", score=total, max_score=1.0, detail=detail)


def _score_source_coverage(profile: UserProfile, all_sources: list[str] | None = None) -> DimensionScore:
    """Score how well the profile represents all available data sources."""
    profile_sources = profile.sources or []
    if not profile_sources and not all_sources:
        return DimensionScore(name="source_coverage", score=0.0, max_score=0.75, detail="No sources")

    known_sources = set(all_sources or profile_sources)
    if not known_sources:
        return DimensionScore(name="source_coverage", score=0.0, max_score=0.75, detail="No sources")

    # Check which sources appear in thread platforms
    mentioned = set()
    for t in profile.active_threads:
        platforms = getattr(t, "platforms", []) or []
        mentioned.update(platforms)

    # Also check text fields
    text = f"{profile.identity_anchor} {profile.recent_detail} {profile.background_context}"
    for src in known_sources:
        if src in text:
            mentioned.add(src)

    coverage = len(mentioned & known_sources) / len(known_sources)
    detail = f"{len(mentioned & known_sources)}/{len(known_sources)} sources covered"
    return DimensionScore(name="source_coverage", score=coverage, max_score=0.75, detail=detail)


def _score_completeness(profile: UserProfile) -> DimensionScore:
    """Score structural completeness — are all fields populated with substance?"""
    checks = {
        "identity_anchor": bool(profile.identity_anchor and len(profile.identity_anchor) > 20),
        "active_threads": bool(profile.active_threads and len(profile.active_threads) >= 2),
        "recent_detail": bool(profile.recent_detail and len(profile.recent_detail) > 50),
        "background_context": bool(profile.background_context and len(profile.background_context) > 50),
        "voice_patterns": profile.voice_patterns is not None,
        "sources": bool(profile.sources),
    }
    filled = sum(1 for v in checks.values() if v)
    total = len(checks)
    score = filled / total
    missing = [k for k, v in checks.items() if not v]
    detail = f"{filled}/{total} fields" + (f", missing: {', '.join(missing)}" if missing else "")
    return DimensionScore(name="completeness", score=score, max_score=0.75, detail=detail)


def _score_recent_detail(profile: UserProfile) -> DimensionScore:
    """Score the recent_detail field on usefulness for downstream AI sessions."""
    detail = profile.recent_detail or ""
    if not detail:
        return DimensionScore(name="recent_detail", score=0.0, max_score=1.0, detail="Empty")

    scores = []

    # Length — actionable context needs 200+ chars
    length = len(detail)
    if length < 100:
        len_score = 0.2
    elif length < 300:
        len_score = 0.6
    elif length <= 2000:
        len_score = 1.0
    else:
        len_score = 0.8  # Too long might dilute
    scores.append(len_score)

    # Temporal markers — dates, "today", "this week", "last"
    temporal = len(re.findall(
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d|'
        r'\d{4}-\d{2}|today|yesterday|this week|last week|right now|currently',
        detail, re.IGNORECASE,
    ))
    temporal_score = min(1.0, temporal / 3)
    scores.append(temporal_score)

    # Specificity — proper nouns and technical terms
    words = detail.split()
    capitalized = sum(1 for w in words if w[0:1].isupper() and len(w) > 2)
    spec_score = min(1.0, capitalized / 10)
    scores.append(spec_score)

    total = sum(scores) / len(scores)
    return DimensionScore(
        name="recent_detail", score=total, max_score=1.0,
        detail=f"{length} chars, {temporal} temporal markers",
    )


def _score_llm_judge(profile: UserProfile, all_sources: list[str] | None = None) -> DimensionScore:
    """Score profile quality using Haiku as an LLM judge.

    Sends the profile to Haiku for a 1-10 rating on 4 criteria:
    insight depth, actionability, specificity, and coherence.
    Cost: ~$0.002 per eval call.

    Returns 0.5 score with fallback detail if the API call fails.
    """
    try:
        import anthropic

        # Build a compact profile summary for the judge
        threads_text = ""
        for t in profile.active_threads:
            name = t.name if hasattr(t, "name") else str(t)
            desc = (t.description if hasattr(t, "description") else "") or ""
            platforms = ", ".join(getattr(t, "platforms", []) or [])
            threads_text += f"  - {name}: {desc[:100]}"
            if platforms:
                threads_text += f" [platforms: {platforms}]"
            threads_text += "\n"

        voice = ""
        if profile.voice_patterns:
            vp = profile.voice_patterns
            tone = getattr(vp, "tone", "") or ""
            style = getattr(vp, "communication_style", "") or ""
            if tone or style:
                voice = f"Voice: tone={tone}, style={style}"

        profile_text = f"""Identity Anchor: {profile.identity_anchor or '(empty)'}

Active Threads:
{threads_text or '(none)'}
Recent Detail: {(profile.recent_detail or '(empty)')[:500]}

Background: {(profile.background_context or '(empty)')[:300]}

{voice}

Sources: {', '.join(profile.sources or [])}"""

        sources_context = f"Available data sources: {', '.join(all_sources)}" if all_sources else ""

        prompt = f"""Rate this identity profile on a 1-10 scale for each criterion. This profile was synthesized from a person's digital footprint to help AI assistants understand who they are.

{sources_context}

--- PROFILE ---
{profile_text}
--- END PROFILE ---

Rate 1-10 on each:
1. INSIGHT: Does it reveal who this person really is — their drives, tensions, identity? (not just surface facts)
2. ACTIONABILITY: Could an AI assistant use this immediately to personalize responses?
3. SPECIFICITY: Real names, dates, projects, platforms vs generic platitudes?
4. COHERENCE: Does it hold together as a unified portrait, or feel like disconnected fragments?

Respond in EXACTLY this format (just the numbers, nothing else):
INSIGHT: <number>
ACTIONABILITY: <number>
SPECIFICITY: <number>
COHERENCE: <number>"""

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-20250414",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )

        # Parse the structured response
        text = response.content[0].text
        scores = {}
        for line in text.strip().splitlines():
            line = line.strip()
            for criterion in ("INSIGHT", "ACTIONABILITY", "SPECIFICITY", "COHERENCE"):
                if line.upper().startswith(criterion):
                    try:
                        val = float(line.split(":")[-1].strip())
                        scores[criterion.lower()] = max(1.0, min(10.0, val))
                    except (ValueError, IndexError):
                        pass

        if len(scores) < 4:
            return DimensionScore(
                name="llm_judge", score=0.5, max_score=2.0,
                detail=f"Partial parse: got {len(scores)}/4 criteria",
            )

        avg = sum(scores.values()) / len(scores)
        normalized = (avg - 1) / 9  # Map 1-10 to 0-1
        detail = ", ".join(f"{k}={v:.0f}" for k, v in scores.items())
        return DimensionScore(name="llm_judge", score=normalized, max_score=2.0, detail=detail)

    except Exception as e:
        return DimensionScore(
            name="llm_judge", score=0.5, max_score=2.0,
            detail=f"LLM judge unavailable: {e}",
        )


def evaluate_profile(
    profile: UserProfile,
    all_sources: list[str] | None = None,
    use_llm_judge: bool = False,
) -> EvalResult:
    """Run full evaluation on a perception profile.

    Returns an EvalResult with dimension scores and a composite total.
    Set use_llm_judge=True to include a Haiku-based quality assessment (costs ~$0.002).
    """
    result = EvalResult(profile_user_id=profile.user_id)
    result.dimensions = [
        _score_thread_quality(profile, all_sources),
        _score_identity_anchor(profile),
        _score_voice_patterns(profile),
        _score_source_coverage(profile, all_sources),
        _score_completeness(profile),
        _score_recent_detail(profile),
    ]
    if use_llm_judge:
        result.dimensions.append(_score_llm_judge(profile, all_sources))
    return result


# ---------------------------------------------------------------------------
# Freeform evaluation — schema-agnostic scoring for arbitrary JSON profiles
# ---------------------------------------------------------------------------

# Default UserProfile keys — used to measure schema novelty
_DEFAULT_SCHEMA_KEYS = frozenset({
    "identity_anchor", "active_threads", "recent_detail",
    "background_context", "voice_patterns", "user_id",
    "created_at", "sources", "events_count", "model",
    "thinking_tokens", "cost_usd", "schema_rationale",
})


def _flatten_json(obj: object, prefix: str = "", _depth: int = 0) -> dict[str, str]:
    """Flatten a nested JSON object into dot-separated key-value pairs."""
    if _depth > 50:
        return {prefix: "<max depth exceeded>"} if prefix else {}
    result: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                result.update(_flatten_json(v, full_key, _depth + 1))
            else:
                result[full_key] = str(v)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            result.update(_flatten_json(item, f"{prefix}[{i}]", _depth + 1))
    else:
        if prefix:
            result[prefix] = str(obj)
    return result


def _score_freeform_specificity(schema: dict, flat: dict[str, str]) -> DimensionScore:
    """Score specificity: proper nouns, dates, project names per total text."""
    all_text = " ".join(flat.values())
    words = all_text.split()
    total_words = max(len(words), 1)

    # Proper nouns (capitalized words > 1 char, not at sentence start)
    capitalized = sum(1 for w in words if w[0:1].isupper() and len(w) > 2)
    # Dates and numbers
    numbers = len(re.findall(r'\d{4}[-/]\d{2}|\b\d{4}\b|\b\d{1,2}/\d{1,2}\b', all_text))
    # Technical terms (words with mixed case, hyphens, underscores)
    technical = sum(1 for w in words if any(c in w for c in "-_") and len(w) > 3)

    signals = capitalized + numbers * 2 + technical
    score = min(1.0, signals / max(total_words * 0.15, 1))

    detail = f"{capitalized} proper nouns, {numbers} dates, {technical} technical terms in {total_words} words"
    return DimensionScore(name="specificity", score=score, max_score=1.5, detail=detail)


def _score_freeform_cross_platform(
    schema: dict, flat: dict[str, str], all_sources: list[str]
) -> DimensionScore:
    """Score cross-platform signal: source names mentioned, threads spanning platforms."""
    all_text = " ".join(flat.values()).lower()
    all_keys = " ".join(flat.keys()).lower()
    combined = all_text + " " + all_keys

    sources_mentioned = set()
    for src in all_sources:
        if src.lower() in combined or src.replace("-", " ").lower() in combined:
            sources_mentioned.add(src)

    if not all_sources:
        return DimensionScore(
            name="cross_platform", score=0.5, max_score=1.0,
            detail="No sources to check against"
        )

    coverage = len(sources_mentioned) / len(all_sources)
    detail = f"{len(sources_mentioned)}/{len(all_sources)} sources mentioned: {', '.join(sorted(sources_mentioned))}"
    return DimensionScore(name="cross_platform", score=coverage, max_score=1.0, detail=detail)


def _score_freeform_depth(schema: dict, flat: dict[str, str]) -> DimensionScore:
    """Score depth: content length, nesting depth, evidence density."""
    all_text = " ".join(flat.values())
    total_chars = len(all_text)
    total_fields = len(flat)

    # Nesting depth
    max_depth = max((k.count(".") + k.count("[") for k in flat.keys()), default=0)

    scores = []

    # Content volume — sweet spot is 2000-10000 chars
    if total_chars < 500:
        scores.append(0.2)
    elif total_chars < 2000:
        scores.append(0.5)
    elif total_chars <= 10000:
        scores.append(1.0)
    else:
        scores.append(0.8)

    # Field count — more fields = more dimensions captured
    field_score = min(1.0, total_fields / 30)
    scores.append(field_score)

    # Nesting — deeper structure = richer representation
    nest_score = min(1.0, max_depth / 4)
    scores.append(nest_score)

    total = sum(scores) / len(scores)
    detail = f"{total_chars} chars, {total_fields} fields, max depth {max_depth}"
    return DimensionScore(name="depth", score=total, max_score=1.0, detail=detail)


def _score_freeform_actionability(schema: dict, flat: dict[str, str]) -> DimensionScore:
    """Score actionability: temporal markers, recent context, project status."""
    all_text = " ".join(flat.values())

    # Temporal markers
    temporal = len(re.findall(
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d|'
        r'\d{4}-\d{2}|today|yesterday|this week|last week|right now|currently|'
        r'deadline|next\s+\w+|recent|active|ongoing',
        all_text, re.IGNORECASE,
    ))

    # Project/status words
    status_words = len(re.findall(
        r'building|shipping|deployed|debugging|implementing|testing|'
        r'blocked|waiting|planning|exploring|researching',
        all_text, re.IGNORECASE,
    ))

    temporal_score = min(1.0, temporal / 5)
    status_score = min(1.0, status_words / 4)
    total = (temporal_score + status_score) / 2

    detail = f"{temporal} temporal markers, {status_words} status words"
    return DimensionScore(name="actionability", score=total, max_score=1.0, detail=detail)


def _score_freeform_novelty(schema: dict) -> DimensionScore:
    """Score schema novelty: key names that differ from UserProfile defaults.

    Rewards genuine structural invention over renaming standard fields.
    """
    top_keys = set(schema.keys())
    novel_keys = top_keys - _DEFAULT_SCHEMA_KEYS
    total_keys = max(len(top_keys), 1)

    novelty_ratio = len(novel_keys) / total_keys
    # Bonus for creative key names (multi-word, descriptive)
    creative_bonus = sum(
        1 for k in novel_keys
        if "_" in k or len(k) > 15
    ) / max(len(novel_keys), 1) * 0.2

    score = min(1.0, novelty_ratio + creative_bonus)
    detail = f"{len(novel_keys)}/{total_keys} novel keys: {', '.join(sorted(novel_keys)[:8])}"
    return DimensionScore(name="schema_novelty", score=score, max_score=0.5, detail=detail)


def evaluate_freeform(
    schema: dict,
    all_sources: list[str] | None = None,
) -> EvalResult:
    """Evaluate a freeform (schema-free) perception profile.

    Schema-agnostic scoring on 5 dimensions:
    - Specificity: proper nouns, dates, project names per total text
    - Cross-platform signal: source names mentioned, threads spanning platforms
    - Depth: content length, nesting depth, evidence density
    - Actionability: temporal markers, recent context, project status
    - Schema novelty: key names differ from UserProfile defaults

    Returns an EvalResult with dimension scores and a composite total.
    """
    flat = _flatten_json(schema)
    sources = all_sources or []

    result = EvalResult(profile_user_id=schema.get("user_id", "unknown"))
    result.dimensions = [
        _score_freeform_specificity(schema, flat),
        _score_freeform_cross_platform(schema, flat, sources),
        _score_freeform_depth(schema, flat),
        _score_freeform_actionability(schema, flat),
        _score_freeform_novelty(schema),
    ]
    return result


def format_eval_report(result: EvalResult) -> str:
    """Format an eval result as a markdown report."""
    sections = []
    sections.append(f"# Profile Evaluation — {result.profile_user_id}\n")
    sections.append(f"**Composite Score: {result.total_pct:.1f}%**\n")

    sections.append("| Dimension | Score | Weight | Detail |")
    sections.append("|-----------|------:|-------:|--------|")
    for d in result.dimensions:
        pct = d.score * 100
        sections.append(f"| {d.name} | {pct:.0f}% | x{d.max_score:.2f} | {d.detail} |")
    sections.append("")

    return "\n".join(sections)
