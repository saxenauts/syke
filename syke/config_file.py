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
    timeout: int = 600
    first_run_budget: float = 2.00
    first_run_max_turns: int = 25


@dataclass(frozen=True)
class DaemonConfig:
    interval: int = 900


@dataclass(frozen=True)
class AskConfig:
    budget: float = 1.00
    max_turns: int = 15
    timeout: int = 300


@dataclass(frozen=True)
class RebuildConfig:
    budget: float = 3.00
    max_turns: int = 20
    thinking: int = 30000


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
    models: ModelsConfig = field(default_factory=ModelsConfig)
    synthesis: SynthesisConfig = field(default_factory=SynthesisConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    ask: AskConfig = field(default_factory=AskConfig)
    rebuild: RebuildConfig = field(default_factory=RebuildConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    providers: dict[str, dict[str, str]] = field(default_factory=dict)



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
    for key in ("user", "timezone"):
        if key in raw:
            kwargs[key] = raw[key]

    # Nested sections → sub-dataclasses
    section_map: dict[str, type[Any]] = {
        "models": ModelsConfig,
        "synthesis": SynthesisConfig,
        "daemon": DaemonConfig,
        "ask": AskConfig,
        "rebuild": RebuildConfig,
        "paths": PathsConfig,
    }
    for section_name, section_cls in section_map.items():
        if section_name in raw:
            section_raw = raw[section_name]
            if not isinstance(section_raw, dict):
                log.warning("config.toml: [%s] should be a table, ignoring", section_name)
                continue
            kwargs[section_name] = _build_nested(section_cls, section_raw)

    # Parse [providers.*] — dict-of-dicts, not a typed dataclass
    if "providers" in raw and isinstance(raw["providers"], dict):
        providers_raw = raw["providers"]
        providers: dict[str, dict[str, str]] = {}
        for name, settings in providers_raw.items():
            if isinstance(settings, dict):
                # Store only string values, skip non-string entries
                providers[name] = {k: str(v) for k, v in settings.items() if v is not None}
        kwargs["providers"] = providers

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
                models=cfg.models,
                synthesis=cfg.synthesis,
                daemon=cfg.daemon,
                ask=cfg.ask,
                rebuild=cfg.rebuild,
                paths=cfg.paths,
                providers=cfg.providers,
            )
        return cfg
    except tomllib.TOMLDecodeError as e:
        log.error("Failed to parse %s: %s — using defaults", config_path, e)
        return SykeConfig(user=getpass.getuser())
    except OSError as e:
        log.warning("Cannot read %s: %s — using defaults", config_path, e)
        return SykeConfig(user=getpass.getuser())


def write_provider_config(
    provider_id: str,
    settings: dict[str, str],
    path: Path | None = None,
) -> None:
    """Write (or update) a [providers.<provider_id>] section in config.toml.

    If the file doesn't exist, creates it with just the provider section.
    If it exists, preserves all other sections and merges the provider settings.
    Uses atomic write (temp file + rename) for safety.

    Args:
        provider_id: Provider name (e.g. "azure", "ollama").
        settings: Dict of non-secret settings (endpoint, model, base_url, api_version).
                  Secrets (auth_token/api_key) must NOT be passed here — use auth_store.
        path: Path to config.toml. Defaults to ~/.syke/config.toml.
    """
    config_path = path or CONFIG_PATH

    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                raw: dict[str, Any] = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError):
            raw = {}
    else:
        raw = {}

    if "providers" not in raw:
        raw["providers"] = {}
    if provider_id not in raw["providers"]:
        raw["providers"][provider_id] = {}
    raw["providers"][provider_id].update(settings)

    _write_toml(raw, config_path)


def _write_toml(data: dict[str, Any], path: Path) -> None:
    """Serialize a dict to TOML and write atomically to path.

    Handles the subset of TOML types used by Syke config:
    str, int, float, bool, and nested dicts (table sections).
    Does NOT handle arrays-of-tables, dates, or other TOML types.
    """
    import os
    import tempfile

    lines: list[str] = []

    def _quote(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        return f'"{v}"'

    def _render(d: dict[str, Any], prefix: str = "") -> None:
        for k, v in d.items():
            if not isinstance(v, dict):
                lines.append(f"{k} = {_quote(v)}")
        for k, v in d.items():
            if isinstance(v, dict):
                section = f"{prefix}.{k}" if prefix else k
                lines.append("")
                lines.append(f"[{section}]")
                for sk, sv in v.items():
                    if not isinstance(sv, dict):
                        lines.append(f"{sk} = {_quote(sv)}")
                for sk, sv in v.items():
                    if isinstance(sv, dict):
                        nested = f"{section}.{sk}"
                        lines.append("")
                        lines.append(f"[{nested}]")
                        for nk, nv in sv.items():
                            lines.append(f"{nk} = {_quote(nv)}")

    _render(data)
    content = "\n".join(lines) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=".config-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.rename(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Template generation (for `syke config init`)
# ---------------------------------------------------------------------------


def generate_default_config(user: str = "") -> str:
    """Generate a default config.toml with comments."""
    user = user or getpass.getuser()
    return f"""\
# Syke configuration
# Docs: https://github.com/saxenauts/syke

# ── Identity ────────────────────────────────────────────────────────────────
user = "{user}"
timezone = "auto"

# ── Model selection per task ────────────────────────────────────────────────
# Provider-native names for now. When multi-provider lands, these become
# "provider/model" format (e.g. "anthropic/claude-sonnet-4-6").
[models]
synthesis = "sonnet"     # cheap — runs every 15 min
# ask = ""              # interactive — defaults to provider's default
rebuild = "opus"         # expensive — full reconstruction, runs rarely

# ── Synthesis agent ─────────────────────────────────────────────────────────
[synthesis]
budget = 0.50            # USD per run
max_turns = 10
threshold = 5            # min new events before synthesizing
thinking = 2000          # thinking budget (tokens)
timeout = 600            # wall-clock timeout (seconds)
first_run_budget = 2.00  # first synthesis gets more room
first_run_max_turns = 25

# ── Background daemon ──────────────────────────────────────────────────────
[daemon]
interval = 900           # seconds between sync cycles

# ── Ask agent (syke ask "question") ─────────────────────────────────────────
[ask]
budget = 1.00
max_turns = 15
timeout = 300            # seconds

# ── Rebuild (syke rebuild) ──────────────────────────────────────────────────
[rebuild]
budget = 3.00
max_turns = 20
thinking = 30000

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

# ── Provider settings ────────────────────────────────────────────────────────
# Non-secret settings per provider. Secrets go in ~/.syke/auth.json via CLI.
# Uncomment and fill in the provider you want to use:
#
# [providers.azure]
# endpoint = "https://my-deployment.openai.azure.com"
# model = "gpt-5"
#
# [providers.openai]
# model = "gpt-5.4"
#
# [providers.ollama]
# base_url = "http://localhost:11434"
# model = "deepseek-r1"
#
# [providers.vllm]
# base_url = "http://localhost:8000"
# model = "meta-llama/Llama-3.2-8B-Instruct"
#
# [providers.llama-cpp]
# base_url = "http://localhost:8080"
# model = "llama3.2"
"""
