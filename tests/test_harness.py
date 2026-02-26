"""Tests for the harness adapter system and Hermes adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from syke.distribution.harness.base import AdapterResult, AdapterStatus, HarnessAdapter


# ---------------------------------------------------------------------------
# AdapterResult
# ---------------------------------------------------------------------------


class TestAdapterResult:
    def test_ok_with_installed(self):
        r = AdapterResult(installed=[Path("/tmp/test")])
        assert r.ok

    def test_not_ok_when_empty(self):
        r = AdapterResult()
        assert not r.ok

    def test_not_ok_with_warnings(self):
        r = AdapterResult(installed=[Path("/tmp/x")], warnings=["oops"])
        assert not r.ok

    def test_skipped(self):
        r = AdapterResult(skipped=["not installed"])
        assert not r.ok
        assert r.skipped == ["not installed"]


# ---------------------------------------------------------------------------
# HermesAdapter — detection
# ---------------------------------------------------------------------------


class TestHermesDetect:
    def test_detect_with_hermes_installed(self, tmp_path):
        """Detect returns True when ~/.hermes/config.yaml exists."""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text("model:\n  default: glm-5\n")

        from syke.distribution.harness.hermes import HermesAdapter

        with (
            patch("syke.distribution.harness.hermes.HERMES_HOME", hermes_home),
        ):
            adapter = HermesAdapter()
            assert adapter.detect()

    def test_detect_without_hermes(self, tmp_path):
        """Detect returns False when ~/.hermes doesn't exist."""
        from syke.distribution.harness.hermes import HermesAdapter

        with patch("syke.distribution.harness.hermes.HERMES_HOME", tmp_path / "nope"):
            adapter = HermesAdapter()
            assert not adapter.detect()

    def test_detect_without_config(self, tmp_path):
        """Detect returns False when ~/.hermes exists but config.yaml doesn't."""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()

        from syke.distribution.harness.hermes import HermesAdapter

        with patch("syke.distribution.harness.hermes.HERMES_HOME", hermes_home):
            adapter = HermesAdapter()
            assert not adapter.detect()


# ---------------------------------------------------------------------------
# HermesAdapter — install
# ---------------------------------------------------------------------------


class TestHermesInstall:
    @pytest.fixture()
    def hermes_env(self, tmp_path):
        """Set up a fake Hermes home directory."""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text("model:\n  default: glm-5\n")
        (hermes_home / "skills").mkdir()
        (hermes_home / "memories").mkdir()
        (hermes_home / "memories" / "MEMORY.md").write_text("Session notes here.\n")
        (hermes_home / "memories" / "USER.md").write_text("User profile here.\n")
        return hermes_home

    def test_install_creates_skill(self, hermes_env):
        """Install creates SKILL.md in the correct Hermes skills path."""
        from syke.distribution.harness.hermes import HermesAdapter

        skill_dir = hermes_env / "skills" / "memory" / "syke"
        skill_path = skill_dir / "SKILL.md"
        cat_path = hermes_env / "skills" / "memory" / "DESCRIPTION.md"

        with (
            patch("syke.distribution.harness.hermes.HERMES_HOME", hermes_env),
            patch(
                "syke.distribution.harness.hermes.HERMES_SKILLS", hermes_env / "skills"
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_CATEGORY",
                hermes_env / "skills" / "memory",
            ),
            patch("syke.distribution.harness.hermes.SYKE_SKILL_DIR", skill_dir),
            patch("syke.distribution.harness.hermes.SYKE_SKILL_PATH", skill_path),
            patch("syke.distribution.harness.hermes.CATEGORY_DESC_PATH", cat_path),
        ):
            adapter = HermesAdapter()
            result = adapter.install()

        assert result.ok
        assert skill_path.exists()
        assert cat_path.exists()
        assert len(result.installed) == 2

        # Verify SKILL.md content
        content = skill_path.read_text()
        assert "name: syke" in content
        assert "hermes:" in content
        assert "Memory" in content
        assert "Don't replace your own memory" in content

    def test_install_creates_category_descriptor(self, hermes_env):
        """Install creates DESCRIPTION.md for the memory category."""
        from syke.distribution.harness.hermes import HermesAdapter

        cat_path = hermes_env / "skills" / "memory" / "DESCRIPTION.md"

        with (
            patch("syke.distribution.harness.hermes.HERMES_HOME", hermes_env),
            patch(
                "syke.distribution.harness.hermes.HERMES_SKILLS", hermes_env / "skills"
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_CATEGORY",
                hermes_env / "skills" / "memory",
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_SKILL_DIR",
                hermes_env / "skills" / "memory" / "syke",
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_SKILL_PATH",
                hermes_env / "skills" / "memory" / "syke" / "SKILL.md",
            ),
            patch("syke.distribution.harness.hermes.CATEGORY_DESC_PATH", cat_path),
        ):
            adapter = HermesAdapter()
            result = adapter.install()

        assert cat_path.exists()
        desc = cat_path.read_text()
        assert "Memory and context skills" in desc

    def test_install_does_not_touch_native_memory(self, hermes_env):
        """A/B mode: install never modifies MEMORY.md or USER.md."""
        mem_before = (hermes_env / "memories" / "MEMORY.md").read_text()
        user_before = (hermes_env / "memories" / "USER.md").read_text()

        from syke.distribution.harness.hermes import HermesAdapter

        with (
            patch("syke.distribution.harness.hermes.HERMES_HOME", hermes_env),
            patch(
                "syke.distribution.harness.hermes.HERMES_SKILLS", hermes_env / "skills"
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_CATEGORY",
                hermes_env / "skills" / "memory",
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_SKILL_DIR",
                hermes_env / "skills" / "memory" / "syke",
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_SKILL_PATH",
                hermes_env / "skills" / "memory" / "syke" / "SKILL.md",
            ),
            patch(
                "syke.distribution.harness.hermes.CATEGORY_DESC_PATH",
                hermes_env / "skills" / "memory" / "DESCRIPTION.md",
            ),
        ):
            adapter = HermesAdapter()
            adapter.install()

        assert (hermes_env / "memories" / "MEMORY.md").read_text() == mem_before
        assert (hermes_env / "memories" / "USER.md").read_text() == user_before

    def test_install_without_hermes_skips(self, tmp_path):
        """Install returns skip when Hermes is not installed."""
        from syke.distribution.harness.hermes import HermesAdapter

        with patch("syke.distribution.harness.hermes.HERMES_HOME", tmp_path / "nope"):
            adapter = HermesAdapter()
            result = adapter.install()

        assert not result.ok
        assert len(result.skipped) == 1
        assert "not installed" in result.skipped[0].lower()

    def test_install_with_custom_skill_content(self, hermes_env):
        """Install accepts custom skill content override."""
        from syke.distribution.harness.hermes import HermesAdapter

        skill_path = hermes_env / "skills" / "memory" / "syke" / "SKILL.md"
        custom = "---\nname: syke\n---\nCustom content.\n"

        with (
            patch("syke.distribution.harness.hermes.HERMES_HOME", hermes_env),
            patch(
                "syke.distribution.harness.hermes.HERMES_SKILLS", hermes_env / "skills"
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_CATEGORY",
                hermes_env / "skills" / "memory",
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_SKILL_DIR",
                hermes_env / "skills" / "memory" / "syke",
            ),
            patch("syke.distribution.harness.hermes.SYKE_SKILL_PATH", skill_path),
            patch(
                "syke.distribution.harness.hermes.CATEGORY_DESC_PATH",
                hermes_env / "skills" / "memory" / "DESCRIPTION.md",
            ),
        ):
            adapter = HermesAdapter()
            adapter.install(skill_content=custom)

        assert skill_path.read_text() == custom


# ---------------------------------------------------------------------------
# HermesAdapter — status
# ---------------------------------------------------------------------------


class TestHermesStatus:
    def test_status_detected_connected(self, tmp_path):
        """Status shows detected + connected when skill is installed."""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text("model:\n  default: glm-5\n")
        skill_dir = hermes_home / "skills" / "memory" / "syke"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text("---\nname: syke\n---\n")
        cat_path = hermes_home / "skills" / "memory" / "DESCRIPTION.md"
        cat_path.write_text("---\ndescription: test\n---\n")
        (hermes_home / "memories").mkdir()
        (hermes_home / "memories" / "MEMORY.md").write_text("notes")
        (hermes_home / "memories" / "USER.md").write_text("profile")

        from syke.distribution.harness.hermes import HermesAdapter

        with (
            patch("syke.distribution.harness.hermes.HERMES_HOME", hermes_home),
            patch("syke.distribution.harness.hermes.SYKE_SKILL_PATH", skill_path),
            patch("syke.distribution.harness.hermes.CATEGORY_DESC_PATH", cat_path),
        ):
            adapter = HermesAdapter()
            s = adapter.status()

        assert s.detected
        assert s.connected
        assert s.native_memory
        assert len(s.files) == 2
        assert "MEMORY.md" in s.notes
        assert "USER.md" in s.notes

    def test_status_detected_not_connected(self, tmp_path):
        """Status shows detected but not connected when no skill installed."""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text("model:\n  default: glm-5\n")

        from syke.distribution.harness.hermes import HermesAdapter

        with (
            patch("syke.distribution.harness.hermes.HERMES_HOME", hermes_home),
            patch(
                "syke.distribution.harness.hermes.SYKE_SKILL_PATH",
                hermes_home / "skills" / "memory" / "syke" / "SKILL.md",
            ),
        ):
            adapter = HermesAdapter()
            s = adapter.status()

        assert s.detected
        assert not s.connected

    def test_status_not_detected(self, tmp_path):
        """Status shows not detected when Hermes isn't installed."""
        from syke.distribution.harness.hermes import HermesAdapter

        with patch("syke.distribution.harness.hermes.HERMES_HOME", tmp_path / "nope"):
            adapter = HermesAdapter()
            s = adapter.status()

        assert not s.detected
        assert not s.connected


# ---------------------------------------------------------------------------
# HermesAdapter — uninstall
# ---------------------------------------------------------------------------


class TestHermesUninstall:
    def test_uninstall_removes_skill(self, tmp_path):
        """Uninstall removes SKILL.md and skill directory."""
        skill_dir = tmp_path / "syke"
        skill_dir.mkdir()
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text("---\nname: syke\n---\n")

        from syke.distribution.harness.hermes import HermesAdapter

        with (
            patch("syke.distribution.harness.hermes.SYKE_SKILL_PATH", skill_path),
            patch("syke.distribution.harness.hermes.SYKE_SKILL_DIR", skill_dir),
        ):
            adapter = HermesAdapter()
            assert adapter.uninstall()

        assert not skill_path.exists()
        assert not skill_dir.exists()

    def test_uninstall_noop_when_not_installed(self, tmp_path):
        """Uninstall succeeds even when nothing is installed."""
        from syke.distribution.harness.hermes import HermesAdapter

        with (
            patch(
                "syke.distribution.harness.hermes.SYKE_SKILL_PATH",
                tmp_path / "nope" / "SKILL.md",
            ),
            patch("syke.distribution.harness.hermes.SYKE_SKILL_DIR", tmp_path / "nope"),
        ):
            adapter = HermesAdapter()
            assert adapter.uninstall()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_get_all_adapters(self):
        """get_all_adapters returns at least one adapter."""
        from syke.distribution.harness import get_all_adapters

        adapters = get_all_adapters()
        assert len(adapters) >= 1
        assert adapters[0].name == "hermes"

    def test_get_detected_adapters_filters(self, tmp_path):
        """get_detected_adapters excludes platforms not installed."""
        from syke.distribution.harness import get_detected_adapters

        with patch("syke.distribution.harness.hermes.HERMES_HOME", tmp_path / "nope"):
            adapters = get_detected_adapters()

        # Hermes shouldn't be detected if ~/.hermes doesn't exist
        names = [a.name for a in adapters]
        assert "hermes" not in names

    def test_install_all_with_detected(self, tmp_path):
        """install_all runs on detected adapters and returns results."""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text("model:\n  default: glm-5\n")
        (hermes_home / "skills").mkdir()

        from syke.distribution.harness import install_all

        with (
            patch("syke.distribution.harness.hermes.HERMES_HOME", hermes_home),
            patch(
                "syke.distribution.harness.hermes.HERMES_SKILLS", hermes_home / "skills"
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_CATEGORY",
                hermes_home / "skills" / "memory",
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_SKILL_DIR",
                hermes_home / "skills" / "memory" / "syke",
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_SKILL_PATH",
                hermes_home / "skills" / "memory" / "syke" / "SKILL.md",
            ),
            patch(
                "syke.distribution.harness.hermes.CATEGORY_DESC_PATH",
                hermes_home / "skills" / "memory" / "DESCRIPTION.md",
            ),
        ):
            results = install_all()

        assert "hermes" in results
        assert results["hermes"].ok

    def test_status_all_includes_all(self):
        """status_all returns status for all known adapters."""
        from syke.distribution.harness import status_all

        statuses = status_all()
        names = [s.name for s in statuses]
        assert "hermes" in names

    def test_adapter_metadata(self):
        """Verify adapter metadata fields are set correctly."""
        from syke.distribution.harness.hermes import HermesAdapter

        adapter = HermesAdapter()
        assert adapter.name == "hermes"
        assert adapter.display_name == "Hermes Agent"
        assert adapter.protocol == "agentskills"
        assert adapter.has_native_memory is True
