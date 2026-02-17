"""Tests for perception evaluation scoring."""

from __future__ import annotations

import pytest

from syke.models import UserProfile, ActiveThread, VoicePattern
from experiments.perception.eval import (
    evaluate_profile,
    format_eval_report,
    _score_thread_quality,
    _score_identity_anchor,
    _score_voice_patterns,
    _score_source_coverage,
    _score_completeness,
    _score_recent_detail,
)


def _make_profile(**overrides) -> UserProfile:
    """Build a test profile with sensible defaults."""
    defaults = {
        "user_id": "test",
        "identity_anchor": (
            "Utkarsh Saxena is a 29-year-old founder-builder caught between grand vision "
            "and immediate survival needs. He builds AI memory systems with philosophical depth, "
            "driven by the conviction that memory is emergent structure, not storage."
        ),
        "active_threads": [
            ActiveThread(
                name="Syke Hackathon",
                description="Building a personal context daemon for the Claude Code Hackathon with agentic perception, MCP tools, and 3-way benchmark system",
                intensity="high",
                platforms=["claude-code", "github"],
                recent_signals=["deployed v1", "running benchmarks", "trace system live"],
            ),
            ActiveThread(
                name="Persona Memory System",
                description="Personal AI memory with Neo4j graph and vector embeddings, 4-pillar model, ALMA-inspired architecture",
                intensity="medium",
                platforms=["claude-code", "chatgpt"],
                recent_signals=["65% on PersonaMem", "ALMA research applied"],
            ),
            ActiveThread(
                name="Pogu Companion",
                description="AI companion with animated dog character",
                intensity="medium",
                platforms=["claude-code", "github"],
                recent_signals=["animation debugging"],
            ),
            ActiveThread(
                name="Ad Economy Thesis",
                description="AI personalization disrupting ad industry",
                intensity="low",
                platforms=["chatgpt"],
                recent_signals=["market sizing"],
            ),
        ],
        "recent_detail": (
            "Feb 13 2026: Working on Syke hackathon for Claude Code competition. "
            "Deployed TraceWriter for full JSONL observability. Running 3-way benchmark "
            "comparing legacy vs agentic v1 vs multi-agent v2. Deadline Feb 16 3PM ET. "
            "Also debugging Pogu dog animation with retro game design philosophy."
        ),
        "background_context": (
            "Former startup founder who raised $2M from Accel, spent 6 years on privacy-preserving AI. "
            "12 years of ML experience from 2014. Now in SF on O-1 visa building personal AI memory systems."
        ),
        "voice_patterns": VoicePattern(
            tone="direct, intense, philosophical with occasional frustration",
            vocabulary_notes=["memeplex", "attractor dynamics", "bitter lesson", "psychonaut"],
            communication_style="Builder who thinks in systems, drops into philosophy mid-debugging",
            examples=["life IS a game at higher complexity", "files = memory", "figure out the truth behind things"],
        ),
        "sources": ["claude-code", "chatgpt", "github"],
        "events_count": 3180,
    }
    defaults.update(overrides)
    return UserProfile(**defaults)


class TestThreadQuality:
    """Test thread quality scoring."""

    def test_good_threads_score_high(self):
        profile = _make_profile()
        score = _score_thread_quality(profile)
        assert score.score > 0.5

    def test_no_threads_scores_zero(self):
        profile = _make_profile(active_threads=[])
        score = _score_thread_quality(profile)
        assert score.score == 0.0

    def test_cross_platform_threads_boost_score(self):
        """Threads spanning multiple platforms score higher."""
        single = _make_profile(active_threads=[
            ActiveThread(name="A", description="desc", platforms=["claude-code"]),
            ActiveThread(name="B", description="desc", platforms=["github"]),
        ])
        cross = _make_profile(active_threads=[
            ActiveThread(name="A", description="desc", platforms=["claude-code", "github"]),
            ActiveThread(name="B", description="desc", platforms=["chatgpt", "github"]),
        ])
        assert _score_thread_quality(cross).score > _score_thread_quality(single).score

    def test_threads_with_signals_score_higher(self):
        """Threads with recent_signals and descriptions are more specific."""
        sparse = _make_profile(active_threads=[
            ActiveThread(name="A", description="x"),
        ])
        rich = _make_profile(active_threads=[
            ActiveThread(
                name="A",
                description="Building a comprehensive AI memory system with graph and vector stores",
                recent_signals=["deployed v1", "benchmarked at 65%", "ALMA research applied"],
            ),
        ])
        assert _score_thread_quality(rich).score > _score_thread_quality(sparse).score


class TestIdentityAnchor:
    """Test identity anchor scoring."""

    def test_good_anchor_scores_high(self):
        profile = _make_profile(
            identity_anchor="Utkarsh is a 29-year-old Indian builder caught between grand vision and immediate SF survival needs. He builds AI memory systems with philosophical depth, driven by a conviction that memory is emergent structure, not storage."
        )
        score = _score_identity_anchor(profile)
        assert score.score > 0.5

    def test_empty_anchor_scores_zero(self):
        profile = _make_profile(identity_anchor="")
        score = _score_identity_anchor(profile)
        assert score.score == 0.0

    def test_short_generic_anchor_scores_low(self):
        profile = _make_profile(identity_anchor="A person who uses computers.")
        score = _score_identity_anchor(profile)
        assert score.score < 0.5

    def test_specific_anchor_scores_higher(self):
        """Anchors with proper nouns and numbers score higher on specificity."""
        generic = _make_profile(identity_anchor="A builder working on projects.")
        specific = _make_profile(
            identity_anchor="Utkarsh Saxena is a 29-year-old founder building Persona at InnerNets AI Inc in San Francisco since October 2025."
        )
        assert _score_identity_anchor(specific).score > _score_identity_anchor(generic).score


class TestVoicePatterns:
    """Test voice pattern scoring."""

    def test_rich_voice_scores_high(self):
        profile = _make_profile()
        score = _score_voice_patterns(profile)
        assert score.score > 0.5

    def test_no_voice_scores_zero(self):
        profile = _make_profile(voice_patterns=None)
        score = _score_voice_patterns(profile)
        assert score.score == 0.0

    def test_minimal_voice_scores_low(self):
        profile = _make_profile(voice_patterns=VoicePattern(tone="ok"))
        score = _score_voice_patterns(profile)
        assert score.score < 0.5


class TestSourceCoverage:
    """Test source coverage scoring."""

    def test_full_coverage(self):
        profile = _make_profile()
        score = _score_source_coverage(profile, all_sources=["claude-code", "chatgpt", "github"])
        assert score.score == 1.0

    def test_partial_coverage(self):
        profile = _make_profile(active_threads=[
            ActiveThread(name="A", description="desc", platforms=["claude-code"]),
        ])
        score = _score_source_coverage(profile, all_sources=["claude-code", "chatgpt", "github"])
        # claude-code in thread platforms, but chatgpt/github also mentioned in text
        assert 0.0 < score.score <= 1.0

    def test_no_sources(self):
        profile = _make_profile(sources=[], active_threads=[])
        score = _score_source_coverage(profile, all_sources=[])
        assert score.score == 0.0


class TestCompleteness:
    """Test structural completeness scoring."""

    def test_full_profile_scores_high(self):
        profile = _make_profile()
        score = _score_completeness(profile)
        assert score.score == 1.0

    def test_missing_voice_patterns(self):
        profile = _make_profile(voice_patterns=None)
        score = _score_completeness(profile)
        assert score.score < 1.0
        assert "voice_patterns" in score.detail

    def test_mostly_empty_profile(self):
        profile = _make_profile(
            identity_anchor="x",
            active_threads=[],
            recent_detail="",
            background_context="",
            voice_patterns=None,
            sources=[],
        )
        score = _score_completeness(profile)
        assert score.score < 0.5


class TestRecentDetail:
    """Test recent_detail scoring."""

    def test_good_detail_scores_high(self):
        profile = _make_profile(
            recent_detail="Feb 13 2026: Working on Syke hackathon for Claude Code competition. "
                          "Deployed TraceWriter for full JSONL observability. Running 3-way benchmark "
                          "comparing legacy vs agentic v1 vs multi-agent v2. Deadline Feb 16 3PM ET."
        )
        score = _score_recent_detail(profile)
        assert score.score > 0.5

    def test_empty_detail_scores_zero(self):
        profile = _make_profile(recent_detail="")
        score = _score_recent_detail(profile)
        assert score.score == 0.0

    def test_detail_with_dates_scores_higher(self):
        no_dates = _make_profile(recent_detail="Working on a project and doing some research.")
        with_dates = _make_profile(
            recent_detail="Feb 13: Working on Syke. Feb 12: Deployed trace system. Currently running benchmarks."
        )
        assert _score_recent_detail(with_dates).score > _score_recent_detail(no_dates).score


class TestEvaluateProfile:
    """Test the full evaluation pipeline."""

    def test_full_evaluation_returns_all_dimensions(self):
        profile = _make_profile()
        result = evaluate_profile(profile, all_sources=["claude-code", "chatgpt", "github"])

        assert result.profile_user_id == "test"
        assert len(result.dimensions) == 6
        assert result.total_score > 0.0
        assert result.total_pct > 0.0

        names = {d.name for d in result.dimensions}
        assert names == {
            "thread_quality", "identity_anchor", "voice_patterns",
            "source_coverage", "completeness", "recent_detail",
        }

    def test_by_name_lookup(self):
        profile = _make_profile()
        result = evaluate_profile(profile)
        assert result.by_name("thread_quality") is not None
        assert result.by_name("nonexistent") is None

    def test_good_profile_scores_above_60(self):
        """A well-constructed profile should score above 60%."""
        profile = _make_profile()
        result = evaluate_profile(profile, all_sources=["claude-code", "chatgpt", "github"])
        assert result.total_pct > 60

    def test_empty_profile_scores_low(self):
        profile = UserProfile(user_id="empty", identity_anchor="")
        result = evaluate_profile(profile)
        assert result.total_pct < 20


class TestFreeformEval:
    """Test freeform (schema-free) profile evaluation."""

    def _make_freeform_schema(self, **overrides) -> dict:
        """Build a test freeform schema with sensible defaults."""
        schema = {
            "core_identity": {
                "essence": "Utkarsh Saxena is a 29-year-old founder-builder in San Francisco building AI memory systems.",
                "drives": ["philosophical depth", "personal AI", "getting rich while having fun"],
                "tension_map": {
                    "vision_vs_survival": "Grand vision for Persona vs immediate needs",
                    "depth_vs_speed": "Wants philosophical depth but needs to ship fast",
                },
            },
            "obsession_graph": [
                {
                    "name": "Syke Hackathon",
                    "platforms": ["claude-code", "github"],
                    "intensity": "high",
                    "signals": ["deployed v1", "running benchmarks", "Feb 2026 deadline"],
                },
                {
                    "name": "Persona Memory",
                    "platforms": ["claude-code", "chatgpt"],
                    "intensity": "medium",
                    "signals": ["65% on PersonaMem", "ALMA research"],
                },
            ],
            "temporal_rhythms": {
                "active_hours": "Late night builder (10PM-3AM)",
                "recent_activity": "Feb 13 2026: Working on Syke hackathon. Currently building benchmarks.",
            },
            "voice_signature": {
                "tone": "direct, intense, philosophical",
                "catchphrases": ["files = memory", "bitter lesson", "psychonaut"],
            },
        }
        schema.update(overrides)
        return schema

    def test_freeform_eval_returns_5_dimensions(self):
        from experiments.perception.eval import evaluate_freeform
        schema = self._make_freeform_schema()
        result = evaluate_freeform(schema, all_sources=["claude-code", "chatgpt", "github"])
        assert len(result.dimensions) == 5
        names = {d.name for d in result.dimensions}
        assert names == {"specificity", "cross_platform", "depth", "actionability", "schema_novelty"}

    def test_freeform_eval_scores_above_zero(self):
        from experiments.perception.eval import evaluate_freeform
        schema = self._make_freeform_schema()
        result = evaluate_freeform(schema, all_sources=["claude-code", "chatgpt", "github"])
        assert result.total_score > 0.0
        assert result.total_pct > 0.0

    def test_freeform_specificity_rewards_proper_nouns(self):
        from experiments.perception.eval import _score_freeform_specificity, _flatten_json
        specific = {"name": "Utkarsh Saxena in San Francisco building Persona for Feb 2026"}
        generic = {"name": "a person building things and working on projects"}
        flat_s = _flatten_json(specific)
        flat_g = _flatten_json(generic)
        assert _score_freeform_specificity(specific, flat_s).score > _score_freeform_specificity(generic, flat_g).score

    def test_freeform_cross_platform_detects_sources(self):
        from experiments.perception.eval import _score_freeform_cross_platform, _flatten_json
        schema = {"data": "activity on claude-code and github"}
        flat = _flatten_json(schema)
        result = _score_freeform_cross_platform(schema, flat, ["claude-code", "github", "chatgpt"])
        assert result.score > 0.0
        assert result.score < 1.0  # Missing chatgpt

    def test_freeform_novelty_rewards_novel_keys(self):
        from experiments.perception.eval import _score_freeform_novelty
        novel = {"obsession_graph": [], "tension_map": {}, "temporal_rhythms": {}}
        standard = {"identity_anchor": "", "active_threads": [], "voice_patterns": {}}
        assert _score_freeform_novelty(novel).score > _score_freeform_novelty(standard).score

    def test_freeform_depth_rewards_nested_structure(self):
        from experiments.perception.eval import _score_freeform_depth, _flatten_json
        shallow = {"a": "hello", "b": "world"}
        deep = {
            "level1": {
                "level2": {
                    "level3": {"data": "deep nested content with lots of detail " * 20}
                },
                "sibling": {"more": "data " * 50},
            },
            "other": [{"item": "value " * 30}],
        }
        flat_s = _flatten_json(shallow)
        flat_d = _flatten_json(deep)
        assert _score_freeform_depth(deep, flat_d).score > _score_freeform_depth(shallow, flat_s).score

    def test_freeform_actionability_rewards_temporal_markers(self):
        from experiments.perception.eval import _score_freeform_actionability, _flatten_json
        temporal = {"detail": "Feb 13 2026: Currently building and shipping. Deadline next week."}
        atemporal = {"detail": "Works on projects and does things."}
        flat_t = _flatten_json(temporal)
        flat_a = _flatten_json(atemporal)
        assert _score_freeform_actionability(temporal, flat_t).score > _score_freeform_actionability(atemporal, flat_a).score

    def test_empty_schema_scores_low(self):
        from experiments.perception.eval import evaluate_freeform
        result = evaluate_freeform({})
        assert result.total_pct < 30

    def test_rich_schema_scores_above_50(self):
        from experiments.perception.eval import evaluate_freeform
        schema = self._make_freeform_schema()
        result = evaluate_freeform(schema, all_sources=["claude-code", "chatgpt", "github"])
        assert result.total_pct > 50


class TestFormatEvalReport:
    """Test markdown report formatting."""

    def test_report_has_score_and_table(self):
        profile = _make_profile()
        result = evaluate_profile(profile)
        report = format_eval_report(result)

        assert "Profile Evaluation" in report
        assert "Composite Score" in report
        assert "thread_quality" in report
        assert "identity_anchor" in report
