"""Tests for config_file.py — TOML loading, defaults, path expansion, template."""

from __future__ import annotations

from pathlib import Path

import pytest

from syke.config_file import (
    SykeConfig,
    expand_path,
    generate_default_config,
    load_config,
    write_provider_config,
)


class TestDefaults:
    def test_default_config_has_sensible_values(self) -> None:
        cfg = SykeConfig()
        assert cfg.models.synthesis == "sonnet"
        assert cfg.models.ask is None
        assert cfg.models.rebuild == "opus"
        assert cfg.synthesis.budget == 0.50
        assert cfg.synthesis.max_turns == 10
        assert cfg.synthesis.threshold == 5
        assert cfg.synthesis.first_run_budget == 2.00
        assert cfg.synthesis.first_run_max_turns == 25
        assert cfg.daemon.interval == 900
        assert cfg.ask.budget == 1.00
        assert cfg.ask.timeout == 300
        assert cfg.rebuild.budget == 3.00
        assert cfg.paths.data_dir == "~/.syke/data"
        assert cfg.paths.auth == "~/.syke/auth.json"

    def test_default_config_is_frozen(self) -> None:
        cfg = SykeConfig()
        with pytest.raises(AttributeError):
            cfg.user = "hacker"  # type: ignore[misc]


class TestLoadConfig:
    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path / "nonexistent.toml")
        assert cfg.models.synthesis == "sonnet"
        assert cfg.daemon.interval == 900

    def test_load_minimal_toml(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text('user = "testuser"\n')
        cfg = load_config(p)
        assert cfg.user == "testuser"
        assert cfg.models.synthesis == "sonnet"  # default preserved

    def test_load_models_section(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text('[models]\nsynthesis = "haiku"\nask = "sonnet"\nrebuild = "opus"\n')
        cfg = load_config(p)
        assert cfg.models.synthesis == "haiku"
        assert cfg.models.ask == "sonnet"
        assert cfg.models.rebuild == "opus"

    def test_load_synthesis_overrides(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text("[synthesis]\nbudget = 1.50\nmax_turns = 20\nthreshold = 10\n")
        cfg = load_config(p)
        assert cfg.synthesis.budget == 1.50
        assert cfg.synthesis.max_turns == 20
        assert cfg.synthesis.threshold == 10
        assert cfg.synthesis.first_run_budget == 2.00  # default preserved

    def test_load_daemon_interval(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text("[daemon]\ninterval = 300\n")
        cfg = load_config(p)
        assert cfg.daemon.interval == 300

    def test_load_paths(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text(
            '[paths]\ndata_dir = "/custom/data"\n\n[paths.sources]\nclaude_code = "/opt/claude"\n'
        )
        cfg = load_config(p)
        assert cfg.paths.data_dir == "/custom/data"
        assert cfg.paths.sources.claude_code == "/opt/claude"
        assert cfg.paths.sources.codex == "~/.codex"  # default preserved

    def test_unknown_keys_ignored(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text('user = "test"\nfuture_key = "ignored"\n')
        cfg = load_config(p)
        assert cfg.user == "test"

    def test_malformed_toml_returns_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text("this is not valid toml [[[")
        cfg = load_config(p)
        assert cfg.models.synthesis == "sonnet"

class TestExpandPath:
    def test_expands_tilde(self) -> None:
        result = expand_path("~/test")
        assert str(result).startswith(str(Path.home()))
        assert result.name == "test"

    def test_absolute_path_unchanged(self) -> None:
        result = expand_path("/opt/syke/data")
        assert result == Path("/opt/syke/data")

    def test_resolves_to_absolute(self) -> None:
        result = expand_path("relative/path")
        assert result.is_absolute()


class TestGenerateConfig:
    def test_generates_valid_toml(self, tmp_path: Path) -> None:
        import tomllib

        content = generate_default_config(user="testuser")
        parsed = tomllib.loads(content)
        assert parsed["user"] == "testuser"
        assert parsed["models"]["synthesis"] == "sonnet"
        assert parsed["daemon"]["interval"] == 900

    def test_roundtrip_through_load(self, tmp_path: Path) -> None:
        content = generate_default_config(user="testuser")
        p = tmp_path / "config.toml"
        p.write_text(content)
        cfg = load_config(p)
        assert cfg.user == "testuser"
        assert cfg.models.synthesis == "sonnet"
        assert cfg.models.rebuild == "opus"
        assert cfg.synthesis.budget == 0.50
        assert cfg.paths.data_dir == "~/.syke/data"

    def test_template_has_comments(self) -> None:
        content = generate_default_config()
        assert "# Syke configuration" in content
        assert "# cheap" in content

    def test_skills_dirs_list(self, tmp_path: Path) -> None:
        content = generate_default_config()
        p = tmp_path / "config.toml"
        p.write_text(content)
        cfg = load_config(p)
        assert len(cfg.paths.distribution.skills_dirs) == 4
        assert "~/.claude/skills" in cfg.paths.distribution.skills_dirs

    def test_default_config_includes_providers_section(self) -> None:
        """generate_default_config() includes commented provider examples."""
        config_str = generate_default_config()
        assert "providers.azure" in config_str
        assert "providers.ollama" in config_str
        assert "endpoint" in config_str
        assert "model" in config_str
        for line in config_str.split("\n"):
            if "providers." in line:
                assert line.strip().startswith("#"), f"Provider line not commented: {line!r}"


class TestProvidersSection:
    def test_providers_section_parsed(self, tmp_path: Path) -> None:
        """[providers.*] sections parse into SykeConfig.providers dict."""
        p = tmp_path / "config.toml"
        p.write_text("""\
[providers.azure]
endpoint = "https://test.openai.azure.com"
model = "gpt-4o"
api_version = "2024-02-01"

[providers.ollama]
base_url = "http://localhost:11434"
model = "llama3.2"
""")
        cfg = load_config(p)
        assert "azure" in cfg.providers
        assert cfg.providers["azure"]["endpoint"] == "https://test.openai.azure.com"
        assert cfg.providers["azure"]["model"] == "gpt-4o"
        assert "ollama" in cfg.providers
        assert cfg.providers["ollama"]["model"] == "llama3.2"

    def test_providers_section_absent_defaults_empty(self, tmp_path: Path) -> None:
        """Missing [providers] section gives empty dict."""
        p = tmp_path / "config.toml"
        p.write_text('user = "test"\n')
        cfg = load_config(p)
        assert cfg.providers == {}

    def test_write_provider_config_creates_file(self, tmp_path: Path) -> None:
        """write_provider_config creates config.toml if missing."""
        p = tmp_path / "config.toml"
        write_provider_config("azure", {"endpoint": "https://test.com", "model": "gpt-4o"}, p)
        cfg = load_config(p)
        assert cfg.providers == {"azure": {"endpoint": "https://test.com", "model": "gpt-4o"}}

    def test_write_provider_config_preserves_existing(self, tmp_path: Path) -> None:
        """write_provider_config preserves other config sections."""
        p = tmp_path / "config.toml"
        p.write_text('user = "alice"\n\n[models]\nsynthesis = "sonnet"\n')
        write_provider_config("azure", {"model": "gpt-4o"}, p)
        cfg = load_config(p)
        assert cfg.user == "alice"
        assert cfg.models.synthesis == "sonnet"
        assert cfg.providers == {"azure": {"model": "gpt-4o"}}

    def test_write_provider_config_merges(self, tmp_path: Path) -> None:
        """Writing twice merges settings without losing existing keys."""
        p = tmp_path / "config.toml"
        write_provider_config("azure", {"endpoint": "https://test.com"}, p)
        write_provider_config("azure", {"model": "gpt-4o"}, p)
        cfg = load_config(p)
        assert cfg.providers["azure"]["endpoint"] == "https://test.com"
        assert cfg.providers["azure"]["model"] == "gpt-4o"


class TestFullConfig:
    def test_full_config_toml(self, tmp_path: Path) -> None:
        """Full config file with all sections — validates the complete schema."""
        p = tmp_path / "config.toml"
        p.write_text("""\
user = "saxenauts"
timezone = "America/Los_Angeles"

[models]
synthesis = "haiku"
ask = "sonnet"
rebuild = "opus"

[synthesis]
budget = 0.75
max_turns = 15
threshold = 3
thinking = 4000
first_run_budget = 3.00
first_run_max_turns = 30

[daemon]
interval = 600

[ask]
budget = 2.00
max_turns = 12
timeout = 180

[rebuild]
budget = 5.00
max_turns = 30
thinking = 50000

[paths]
data_dir = "/custom/data"
auth = "/custom/auth.json"

[paths.sources]
claude_code = "/opt/claude"
codex = "/opt/codex"
chatgpt_export = "/opt/downloads"

[paths.distribution]
claude_md = "/opt/claude/CLAUDE.md"
skills_dirs = ["/opt/skills"]
hermes_home = "/opt/hermes"
""")
        cfg = load_config(p)

        assert cfg.user == "saxenauts"
        assert cfg.timezone == "America/Los_Angeles"

        assert cfg.models.synthesis == "haiku"
        assert cfg.models.ask == "sonnet"

        assert cfg.synthesis.budget == 0.75
        assert cfg.synthesis.max_turns == 15
        assert cfg.synthesis.threshold == 3
        assert cfg.synthesis.thinking == 4000
        assert cfg.synthesis.first_run_budget == 3.00

        assert cfg.daemon.interval == 600

        assert cfg.ask.budget == 2.00
        assert cfg.ask.timeout == 180

        assert cfg.rebuild.budget == 5.00
        assert cfg.rebuild.thinking == 50000

        assert cfg.paths.data_dir == "/custom/data"
        assert cfg.paths.sources.claude_code == "/opt/claude"
        assert cfg.paths.distribution.skills_dirs == ("/opt/skills",)
        assert cfg.paths.distribution.hermes_home == "/opt/hermes"




class TestRemovedLegacyConfig:
    def test_provider_key_is_ignored(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        p = tmp_path / "config.toml"
        p.write_text('provider = "codex"\n')
        cfg = load_config(p)
        assert cfg.user
        assert "top-level 'provider' is no longer used" in caplog.text

    def test_runtime_section_is_ignored(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        p = tmp_path / "config.toml"
        p.write_text('[runtime]\nbackend = "claude"\n')
        cfg = load_config(p)
        assert cfg.user
        assert "Pi is the only runtime" in caplog.text

    def test_runtime_not_included_in_default_template(self) -> None:
        content = generate_default_config()
        assert "[runtime]" not in content
