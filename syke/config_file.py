"""TOML configuration file — ~/.syke/config.toml loading, defaults, validation.

Precedence: hardcoded defaults → config.toml → environment variables.
Config file is optional — everything works without it.
"""

from __future__ import annotations

import getpass
import logging
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, get_type_hints

log = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".syke" / "config.toml"


# ---------------------------------------------------------------------------
# Typed config sections (frozen dataclasses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelsConfig:
    synthesis: str = "sonnet"
    ask: str | None = None
    rebuild: str = "opus"


@dataclass(frozen=True)
class SynthesisConfig:
    budget: float = 0.50
    max_turns: int = 10
    threshold: int = 5
    thinking: int = 2000
    first_run_budget: float = 2.00
    first_run_max_turns: int = 25


@dataclass(frozen=True)
class DaemonConfig:
    interval: int = 900


@dataclass(frozen=True)
class AskConfig:
    budget: float = 1.00
    max_turns: int = 8
    timeout: int = 120


@dataclass(frozen=True)
class RebuildConfig:
    budget: float = 3.00
    max_turns: int = 20
    thinking: int = 30000


@dataclass(frozen=True)
class SourcesConfig:
    claude_code: bool = True
    codex: bool = True
    chatgpt: bool = True
    gmail: bool = False
    github_enabled: bool = True
    github_username: str = ""


@dataclass(frozen=True)
class DistributionConfig:
    claude_code: bool = True
    claude_desktop: bool = True
    hermes: bool = True


@dataclass(frozen=True)
class PrivacyConfig:
    redact_credentials: bool = True
    skip_private_messages: bool = True


@dataclass(frozen=True)
class SourcePathsConfig:
    claude_code: str = "~/.claude"
    codex: str = "~/.codex"
    chatgpt_export: str = "~/Downloads"


@dataclass(frozen=True)
class DistributionPathsConfig:
    claude_md: str = "~/.claude/CLAUDE.md"
    skills_dirs: tuple[str, ...] = (
        "~/.claude/skills",
        "~/.codex/skills",
        "~/.cursor/skills",
        "~/.windsurf/skills",
    )
    hermes_home: str = "~/.hermes"


@dataclass(frozen=True)
class PathsConfig:
    data_dir: str = "~/.syke/data"
    auth: str = "~/.syke/auth.json"
    sources: SourcePathsConfig = field(default_factory=SourcePathsConfig)
    distribution: DistributionPathsConfig = field(default_factory=DistributionPathsConfig)


@dataclass(frozen=True)
class SykeConfig:
    """Top-level Syke configuration."""

    user: str = ""
    timezone: str = "auto"
    provider: str = ""
    models: ModelsConfig = field(default_factory=ModelsConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    synthesis: SynthesisConfig = field(default_factory=SynthesisConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    ask: AskConfig = field(default_factory=AskConfig)
    rebuild: RebuildConfig = field(default_factory=RebuildConfig)
    distribution: DistributionConfig = field(default_factory=DistributionConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)


# ---------------------------------------------------------------------------
# Path expansion
# ---------------------------------------------------------------------------


def expand_path(p: str) -> Path:
    """Expand ~ and resolve a config path string to an absolute Path."""
    return Path(p).expanduser().resolve()


# ---------------------------------------------------------------------------
# TOML → dataclass construction
# ---------------------------------------------------------------------------


def _build_nested(cls: Any, raw: dict[str, Any]) -> Any:
    """Construct a frozen dataclass from a raw TOML dict, ignoring unknown keys."""
    kwargs: dict[str, Any] = {}
    valid_names = {f.name for f in fields(cls)}
    resolved_hints = get_type_hints(cls)
    for key, value in raw.items():
        py_key = key.replace("-", "_")
        if py_key not in valid_names:
            log.warning("config.toml: ignoring unknown key %r in [%s]", key, cls.__name__)
            continue
        field_type = resolved_hints.get(py_key)
        if isinstance(value, dict) and hasattr(field_type, "__dataclass_fields__"):
            kwargs[py_key] = _build_nested(field_type, value)
        elif py_key == "skills_dirs" and isinstance(value, list):
            kwargs[py_key] = tuple(value)
        else:
            kwargs[py_key] = value
    return cls(**kwargs)


def _build_config(raw: dict[str, Any]) -> SykeConfig:
    """Build SykeConfig from raw TOML dict."""
    kwargs: dict[str, Any] = {}

    # Scalar top-level keys
    for key in ("user", "timezone", "provider"):
        if key in raw:
            kwargs[key] = raw[key]

    # Nested sections → sub-dataclasses
    section_map: dict[str, type[Any]] = {
        "models": ModelsConfig,
        "sources": SourcesConfig,
        "synthesis": SynthesisConfig,
        "daemon": DaemonConfig,
        "ask": AskConfig,
        "rebuild": RebuildConfig,
        "distribution": DistributionConfig,
        "privacy": PrivacyConfig,
        "paths": PathsConfig,
    }
    for section_name, section_cls in section_map.items():
        if section_name in raw:
            section_raw = raw[section_name]
            if not isinstance(section_raw, dict):
                log.warning("config.toml: [%s] should be a table, ignoring", section_name)
                continue
            kwargs[section_name] = _build_nested(section_cls, section_raw)

    # Handle sources special case: TOML allows both flat bools and tables
    # e.g. claude-code = true  AND  [sources.github] enabled = true, username = "x"
    if "sources" in raw and isinstance(raw["sources"], dict):
        src: dict[str, Any] = raw["sources"]
        src_kwargs: dict[str, Any] = {}
        for key, val in src.items():
            py_key = key.replace("-", "_")
            if isinstance(val, bool):
                src_kwargs[py_key] = val
            elif isinstance(val, dict) and py_key == "github":
                src_kwargs["github_enabled"] = val.get("enabled", True)
                src_kwargs["github_username"] = val.get("username", "")
        kwargs["sources"] = SourcesConfig(**src_kwargs)

    return SykeConfig(**kwargs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(path: Path | None = None) -> SykeConfig:
    """Load config from TOML file, falling back to defaults.

    Args:
        path: Override config file path (for testing). Defaults to ~/.syke/config.toml.

    Returns:
        SykeConfig with values from file merged over defaults.
    """
    config_path = path or CONFIG_PATH

    if not config_path.exists():
        log.debug("No config file at %s, using defaults", config_path)
        # Still apply system defaults for user
        return SykeConfig(user=getpass.getuser())

    try:
        with open(config_path, "rb") as f:
            raw = tomllib.load(f)
        log.debug("Loaded config from %s", config_path)
        cfg = _build_config(raw)
        # Default user to system username if not set
        if not cfg.user:
            cfg = SykeConfig(
                user=getpass.getuser(),
                timezone=cfg.timezone,
                provider=cfg.provider,
                models=cfg.models,
                sources=cfg.sources,
                synthesis=cfg.synthesis,
                daemon=cfg.daemon,
                ask=cfg.ask,
                rebuild=cfg.rebuild,
                distribution=cfg.distribution,
                privacy=cfg.privacy,
                paths=cfg.paths,
            )
        return cfg
    except tomllib.TOMLDecodeError as e:
        log.error("Failed to parse %s: %s — using defaults", config_path, e)
        return SykeConfig(user=getpass.getuser())
    except OSError as e:
        log.warning("Cannot read %s: %s — using defaults", config_path, e)
        return SykeConfig(user=getpass.getuser())


# ---------------------------------------------------------------------------
# Template generation (for `syke config init`)
# ---------------------------------------------------------------------------


def generate_default_config(user: str = "", provider: str = "") -> str:
    """Generate a default config.toml with comments."""
    user = user or getpass.getuser()
    return f"""\
# Syke configuration
# Docs: https://github.com/saxenauts/syke

# ── Identity ────────────────────────────────────────────────────────────────
user = "{user}"
timezone = "auto"

# LLM provider (selected at setup)
# Options: claude-login, codex, openrouter, zai
provider = "{provider}"

# ── Model selection per task ────────────────────────────────────────────────
# Provider-native names for now. When multi-provider lands, these become
# "provider/model" format (e.g. "anthropic/claude-sonnet-4-6").
[models]
synthesis = "sonnet"     # cheap — runs every 15 min
# ask = ""              # interactive — defaults to provider's default
rebuild = "opus"         # expensive — full reconstruction, runs rarely

# ── Data sources ────────────────────────────────────────────────────────────
[sources]
claude-code = true
codex = true
chatgpt = true
gmail = false

[sources.github]
enabled = true
username = "{user}"

# ── Synthesis agent ─────────────────────────────────────────────────────────
[synthesis]
budget = 0.50            # USD per run
max_turns = 10
threshold = 5            # min new events before synthesizing
thinking = 2000          # thinking budget (tokens)
first_run_budget = 2.00  # first synthesis gets more room
first_run_max_turns = 25

# ── Background daemon ──────────────────────────────────────────────────────
[daemon]
interval = 900           # seconds between sync cycles

# ── Ask agent (syke ask "question") ─────────────────────────────────────────
[ask]
budget = 1.00
max_turns = 8
timeout = 120            # seconds

# ── Rebuild (syke rebuild) ──────────────────────────────────────────────────
[rebuild]
budget = 3.00
max_turns = 20
thinking = 30000

# ── Distribution targets ───────────────────────────────────────────────────
[distribution]
claude-code = true
claude-desktop = true
hermes = true

# ── Privacy filters (applied before events enter DB) ────────────────────────
[privacy]
redact_credentials = true
skip_private_messages = true

# ── Paths ───────────────────────────────────────────────────────────────────
[paths]
data_dir = "~/.syke/data"
auth = "~/.syke/auth.json"

[paths.sources]
claude_code = "~/.claude"
codex = "~/.codex"
chatgpt_export = "~/Downloads"

[paths.distribution]
claude_md = "~/.claude/CLAUDE.md"
skills_dirs = [
    "~/.claude/skills",
    "~/.codex/skills",
    "~/.cursor/skills",
    "~/.windsurf/skills",
]
hermes_home = "~/.hermes"
"""
