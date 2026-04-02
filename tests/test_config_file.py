"""Tests for the trimmed config_file Pi-native contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from syke.config_file import SykeConfig, expand_path, generate_default_config, load_config


class TestDefaults:
    def test_default_config_has_runtime_knobs_only(self) -> None:
        cfg = SykeConfig()
        assert cfg.synthesis.threshold == 5
        assert cfg.synthesis.thinking_level == "medium"
        assert cfg.synthesis.timeout == 600
        assert cfg.synthesis.first_run_timeout == 1500
        assert cfg.ask.timeout == 300
        assert cfg.paths.data_dir == "~/.syke/data"
        assert not hasattr(cfg, "rebuild")

    def test_default_config_is_frozen(self) -> None:
        cfg = SykeConfig()
        with pytest.raises(AttributeError):
            cfg.user = "hacker"  # type: ignore[misc]


class TestLoadConfig:
    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path / "nonexistent.toml")
        assert cfg.synthesis.threshold == 5
        assert cfg.daemon.interval == 900

    def test_load_minimal_toml(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text('user = "testuser"\n')
        cfg = load_config(p)
        assert cfg.user == "testuser"
        assert cfg.synthesis.threshold == 5

    def test_load_runtime_sections(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text(
            """\
user = "saxenauts"
timezone = "America/Los_Angeles"

[synthesis]
threshold = 7
thinking_level = "high"
timeout = 420
first_run_timeout = 1800

[daemon]
interval = 600

[ask]
timeout = 180

[paths]
data_dir = "/custom/data"

[paths.sources]
claude_code = "/opt/claude"
"""
        )
        cfg = load_config(p)
        assert cfg.user == "saxenauts"
        assert cfg.timezone == "America/Los_Angeles"
        assert cfg.synthesis.threshold == 7
        assert cfg.synthesis.thinking_level == "high"
        assert cfg.synthesis.timeout == 420
        assert cfg.synthesis.first_run_timeout == 1800
        assert cfg.daemon.interval == 600
        assert cfg.ask.timeout == 180
        assert cfg.paths.data_dir == "/custom/data"
        assert cfg.paths.sources.claude_code == "/opt/claude"

    def test_ignores_removed_sections_and_keys(self, tmp_path: Path, caplog) -> None:
        p = tmp_path / "config.toml"
        p.write_text(
            """\
[synthesis]
budget = 0.75
max_turns = 15
first_run_budget = 2.0

[ask]
budget = 2.0
max_turns = 15

[rebuild]
budget = 5.0
thinking = 50000

[models]
synthesis = "sonnet"

[providers.openai]
model = "gpt-5.4"
"""
        )
        cfg = load_config(p)
        assert cfg.synthesis.threshold == 5
        assert cfg.synthesis.thinking_level == "medium"
        assert not hasattr(cfg, "models")
        assert not hasattr(cfg, "providers")
        assert not hasattr(cfg, "rebuild")
        assert "unknown top-level key 'rebuild'" in caplog.text
        assert "unknown top-level key 'models'" in caplog.text
        assert "unknown top-level key 'providers'" in caplog.text


class TestExpandPath:
    def test_expands_tilde(self) -> None:
        result = expand_path("~/test")
        assert str(result).startswith(str(Path.home()))
        assert result.name == "test"


class TestGenerateConfig:
    def test_generates_valid_toml(self, tmp_path: Path) -> None:
        import tomllib

        content = generate_default_config(user="testuser")
        parsed = tomllib.loads(content)
        assert parsed["user"] == "testuser"
        assert "models" not in parsed
        assert "providers" not in parsed
        assert "rebuild" not in parsed
        assert parsed["daemon"]["interval"] == 900

    def test_roundtrip_through_load(self, tmp_path: Path) -> None:
        content = generate_default_config(user="testuser")
        p = tmp_path / "config.toml"
        p.write_text(content)
        cfg = load_config(p)
        assert cfg.user == "testuser"
        assert cfg.synthesis.threshold == 5
        assert cfg.paths.data_dir == "~/.syke/data"

    def test_template_has_no_legacy_sections(self) -> None:
        content = generate_default_config()
        assert "[models]" not in content
        assert "[providers." not in content
        assert "auth.json" not in content
        assert "[rebuild]" not in content
        assert "max_turns" not in content
        assert "budget =" not in content
