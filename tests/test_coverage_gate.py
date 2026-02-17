"""Tests for CoverageTracker in syke/perception/tools.py."""

import json

from syke.perception.tools import CoverageTracker


# --- browse_timeline source coverage ---

class TestBrowseTimelineCoverage:
    """Tests for how browse_timeline affects source coverage."""

    def test_browse_with_source_grants_coverage(self):
        tracker = CoverageTracker(known_sources=["github", "chatgpt", "claude-code"])
        tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        assert "github" in tracker.sources_browsed
        assert "chatgpt" not in tracker.sources_browsed

    def test_browse_without_source_does_not_grant_all_sources(self):
        """Browsing without a source filter should NOT grant coverage for all sources."""
        tracker = CoverageTracker(known_sources=["github", "chatgpt", "claude-code"])
        tracker.update_from_tool_call("browse_timeline", {})
        assert len(tracker.sources_browsed) == 0
        assert tracker.source_coverage == 0.0

    def test_browse_without_source_still_increments_tool_count(self):
        tracker = CoverageTracker(known_sources=["github"])
        tracker.update_from_tool_call("browse_timeline", {})
        assert tracker.tool_count == 1

    def test_multiple_source_browses_accumulate(self):
        tracker = CoverageTracker(known_sources=["github", "chatgpt"])
        tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        tracker.update_from_tool_call("browse_timeline", {"source": "chatgpt"})
        assert tracker.source_coverage == 1.0


# --- String matching in update_from_tool_result ---

class TestSourceDetectionInResults:
    """Tests for JSON-format source detection in tool results."""

    def test_json_source_field_grants_coverage(self):
        tracker = CoverageTracker(known_sources=["github", "chatgpt"])
        result_text = json.dumps({"events": [{"source": "github", "title": "test"}]})
        tracker.update_from_tool_result("search_footprint", result_text)
        assert "github" in tracker.sources_searched

    def test_source_in_url_does_not_grant_coverage(self):
        """The word 'github' in a URL should not count as exploring the github source."""
        tracker = CoverageTracker(known_sources=["github"])
        result_text = json.dumps({
            "events": [{
                "source": "chatgpt",
                "title": "Discussing github.com/user/repo",
                "content": "Check out github.com/saxenauts/syke"
            }]
        })
        tracker.update_from_tool_result("search_footprint", result_text)
        # "github" appears in content/title but not as "source": "github"
        assert "github" not in tracker.sources_searched

    def test_source_in_content_text_does_not_grant_coverage(self):
        """The word matching a source in content should not grant coverage."""
        tracker = CoverageTracker(known_sources=["claude-code"])
        result_text = '{"events": [{"source": "chatgpt", "content": "Working on claude-code adapter"}]}'
        tracker.update_from_tool_result("search_footprint", result_text)
        assert "claude-code" not in tracker.sources_searched

    def test_actual_source_field_grants_coverage(self):
        """The JSON source field format should correctly match."""
        tracker = CoverageTracker(known_sources=["claude-code", "chatgpt"])
        result_text = '{"events": [{"source": "claude-code", "title": "Session"}]}'
        tracker.update_from_tool_result("browse_timeline", result_text)
        assert "claude-code" in tracker.sources_searched

    def test_multiple_sources_in_results(self):
        tracker = CoverageTracker(known_sources=["github", "chatgpt", "claude-code"])
        result_text = json.dumps({
            "by_source": {
                "github": [{"source": "github", "title": "PR"}],
                "chatgpt": [{"source": "chatgpt", "title": "Chat"}],
            }
        })
        tracker.update_from_tool_result("cross_reference", result_text)
        assert "github" in tracker.sources_searched
        assert "chatgpt" in tracker.sources_searched
        assert "claude-code" not in tracker.sources_searched


# --- Minimum tool count ---

class TestMinimumToolCount:
    """Tests for the minimum tool count requirement (raised to 4)."""

    def test_three_tools_insufficient(self):
        tracker = CoverageTracker(known_sources=["github"])
        # Make 3 tool calls, all sources covered, has cross-reference
        tracker.update_from_tool_call("get_source_overview", {})
        tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        tracker.update_from_tool_call("cross_reference", {"topic": "test"})
        gaps = tracker.submission_gaps()
        assert "insufficient_exploration" in gaps
        assert gaps["tool_count"] == 3

    def test_four_tools_sufficient(self):
        tracker = CoverageTracker(known_sources=["github"])
        tracker.update_from_tool_call("get_source_overview", {})
        tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        tracker.update_from_tool_call("search_footprint", {"query": "test"})
        tracker.update_from_tool_call("cross_reference", {"topic": "test"})
        gaps = tracker.submission_gaps()
        assert "insufficient_exploration" not in gaps


# --- submission_gaps logic ---

class TestSubmissionGaps:
    """Tests for the full submission_gaps method."""

    def test_no_gaps_when_all_requirements_met(self):
        tracker = CoverageTracker(known_sources=["github", "chatgpt"])
        # Explore both sources
        tracker.update_from_tool_call("get_source_overview", {})
        tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        tracker.update_from_tool_call("browse_timeline", {"source": "chatgpt"})
        tracker.update_from_tool_call("cross_reference", {"topic": "memory"})
        gaps = tracker.submission_gaps()
        assert gaps == {}

    def test_missing_source_reported(self):
        tracker = CoverageTracker(known_sources=["github", "chatgpt", "claude-code"])
        for _ in range(5):
            tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        tracker.update_from_tool_call("cross_reference", {"topic": "test"})
        gaps = tracker.submission_gaps()
        assert "sources_missing" in gaps
        assert "chatgpt" in gaps["sources_missing"]
        assert "claude-code" in gaps["sources_missing"]

    def test_cross_platform_deficit_reported(self):
        tracker = CoverageTracker(known_sources=["github", "chatgpt"])
        # Explore both but no cross_reference
        tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        tracker.update_from_tool_call("browse_timeline", {"source": "chatgpt"})
        tracker.update_from_tool_call("search_footprint", {"query": "test"})
        tracker.update_from_tool_call("search_footprint", {"query": "test2"})
        gaps = tracker.submission_gaps()
        assert gaps.get("cross_platform_deficit") is True

    def test_single_source_no_cross_platform_required(self):
        """With only one source, cross-platform queries are not required."""
        tracker = CoverageTracker(known_sources=["github"])
        tracker.update_from_tool_call("get_source_overview", {})
        tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        tracker.update_from_tool_call("search_footprint", {"query": "test"})
        tracker.update_from_tool_call("search_footprint", {"query": "test2"})
        gaps = tracker.submission_gaps()
        assert "cross_platform_deficit" not in gaps

    def test_no_known_sources_returns_no_gaps(self):
        tracker = CoverageTracker(known_sources=[])
        for _ in range(5):
            tracker.update_from_tool_call("browse_timeline", {})
        gaps = tracker.submission_gaps()
        assert "sources_missing" not in gaps


# --- Coverage feedback ---

class TestCoverageFeedback:
    """Tests for the coverage_feedback method."""

    def test_no_feedback_when_all_covered(self):
        tracker = CoverageTracker(known_sources=["github"])
        tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        tracker.update_from_tool_call("search_footprint", {"query": "test"})
        tracker.update_from_tool_call("search_footprint", {"query": "test2"})
        assert tracker.coverage_feedback() is None

    def test_feedback_when_sources_missing(self):
        tracker = CoverageTracker(known_sources=["github", "chatgpt"])
        tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        tracker.update_from_tool_call("search_footprint", {"query": "test"})
        tracker.update_from_tool_call("search_footprint", {"query": "test2"})
        feedback = tracker.coverage_feedback()
        assert feedback is not None
        assert "chatgpt" in feedback
        assert "COVERAGE GAP" in feedback

    def test_no_feedback_before_minimum_tool_count(self):
        """Don't nag about coverage until agent has made a few tool calls."""
        tracker = CoverageTracker(known_sources=["github", "chatgpt"])
        tracker.update_from_tool_call("get_source_overview", {})
        tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        # Only 2 tool calls — too early for feedback
        assert tracker.coverage_feedback() is None


# --- Explored sources union ---

class TestExploredSources:
    """Tests for the explored_sources property (union of browsed and searched)."""

    def test_browsed_and_searched_are_unioned(self):
        tracker = CoverageTracker(known_sources=["github", "chatgpt"])
        tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        result_text = '{"events": [{"source": "chatgpt", "title": "test"}]}'
        tracker.update_from_tool_result("search_footprint", result_text)
        assert tracker.explored_sources == {"github", "chatgpt"}
        assert tracker.source_coverage == 1.0

    def test_same_source_from_both_paths(self):
        """A source found via both browse and search results is counted once."""
        tracker = CoverageTracker(known_sources=["github"])
        tracker.update_from_tool_call("browse_timeline", {"source": "github"})
        result_text = '{"events": [{"source": "github", "title": "test"}]}'
        tracker.update_from_tool_result("search_footprint", result_text)
        assert tracker.source_coverage == 1.0
        assert len(tracker.explored_sources) == 1
