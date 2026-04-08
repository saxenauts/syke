# Syke Config Reference

Authoritative reference for `~/.syke/config.toml` in the current runtime.

This document only covers the config model that actually exists in `syke/config_file.py` and `syke/config.py`.

---

## Precedence

Effective values resolve in this order:

1. Hardcoded defaults in `syke/config_file.py`
2. `~/.syke/config.toml`
3. Environment variables read by `syke/config.py` and provider runtime

Config is optional. Syke runs without a config file.

---

## What Exists

Current top-level config shape:

```toml
user = ""
timezone = "auto"

[synthesis]
[daemon]
[ask]
[paths]
```

What does not currently exist as typed config:

- `[sources]`
- `[distribution]`
- `[privacy]`

Those may return later, but they are not part of the current config contract.

---

## CLI

```bash
syke config init
syke config show
syke config path
```

---

## Top-Level Keys

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `user` | `string` | `""` | Default user ID; resolves to system username if empty | `SYKE_USER` |
| `timezone` | `string` | `"auto"` | Timezone mode for rendering/parsing | `SYKE_TIMEZONE` |

---

## `[synthesis]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `threshold` | `int` | `5` | Legacy config key (synthesis always runs; the agent decides via temporal context whether anything warrants updating) | `SYKE_SYNC_THRESHOLD` |
| `thinking_level` | `string` | `"medium"` | Pi thinking level written to workspace settings | `SYKE_SYNC_THINKING_LEVEL` |
| `timeout` | `int` | `600` | Wall-clock timeout in seconds | `SYKE_SYNC_TIMEOUT` |
| `first_run_timeout` | `int` | `1500` | Wall-clock timeout for the first synthesis run | `SYKE_SYNC_FIRST_RUN_TIMEOUT` |

---

## `[daemon]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `interval` | `int` | `900` | Loop interval in seconds | `SYKE_DAEMON_INTERVAL` |

---

## `[ask]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `timeout` | `int` | `300` | Ask timeout in seconds | `SYKE_ASK_TIMEOUT` |

---

## `[paths]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `data_dir` | `string` | `"~/.syke/data"` | Legacy config key (flat workspace model means `user_data_dir()` returns `~/.syke/` directly; this key is not used for path resolution) | `SYKE_DATA_DIR` |

### `[paths.sources]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `claude_code` | `string` | `"~/.claude"` | Claude Code source root |
| `codex` | `string` | `"~/.codex"` | Codex source root |

### `[paths.distribution]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `claude_md` | `string` | `"~/.claude/CLAUDE.md"` | Retained only for deferred harness-specific memex injection work |
| `skills_dirs` | `array[string]` | `.agents`, Claude, Gemini, Hermes, Codex, Cursor, OpenCode skill dirs | Capability installation targets |

Note: In the flat workspace model, everything lives at `~/.syke/` directly. There is no `data/{user}/` nesting. `data_dir` is a legacy key in the config schema.

Example:

```toml
[paths]
data_dir = "~/.syke/data"  # legacy, not used for path resolution

[paths.sources]
claude_code = "~/.claude"
codex = "~/.codex"

[paths.distribution]
claude_md = "~/.claude/CLAUDE.md"
skills_dirs = [
    "~/.agents/skills",
    "~/.claude/skills",
    "~/.gemini/skills",
    "~/.hermes/skills",
    "~/.codex/skills",
    "~/.cursor/skills",
    "~/.config/opencode/skills",
]
```

---

## Pi Agent State

Provider, model, auth, and endpoint state no longer live in `config.toml`.

Syke now keeps Pi-native runtime state in:

- `~/.syke/pi-agent/auth.json`
- `~/.syke/pi-agent/settings.json`
- `~/.syke/pi-agent/models.json`

Use the CLI to manage that state:

```bash
syke setup
syke auth
syke auth status
syke auth set openai --api-key KEY --model gpt-5.4 --use
syke auth login openai-codex --use
syke auth set localproxy --base-url URL --model MODEL --use
```

---

## Minimal Example

```toml
user = "saxenauts"
timezone = "auto"

[synthesis]
thinking_level = "medium"
timeout = 600
first_run_timeout = 1500

[daemon]
interval = 900

[ask]
timeout = 300
```

---

## Additional Environment Variables

These env vars are not config-file keys but are read by the runtime:

| Env Var | Default | Meaning |
|---|---|---|
| `SYKE_PROVIDER` | — | Per-process provider override |
| `SYKE_DB` | — | Override per-user DB path (testing/custom setups) |
| `SYKE_WORKSPACE_ROOT` | `~/.syke` | Override Pi workspace directory |
| `SYKE_DISABLE_SELF_OBSERVATION` | — | Disable self-observation event capture |
| `SYKE_PI_AGENT_DIR` | `~/.syke/pi-agent` | Override Pi agent state directory |
| `SYKE_PI_STATE_AUDIT_PATH` | `~/.config/syke/pi-state-audit.log` | Override Pi state audit log path |

---

## Notes

- Unknown keys in typed sections are ignored with warnings.
- Provider/model/auth state does not live in `config.toml`. Use `syke auth` or `syke setup` for persisted Pi-native state, override per-process with `SYKE_PROVIDER`, or override per-command with `--provider`.
- `skills_dirs` is written as a normal TOML array.
- The memex is the product artifact. `claude_md` is one current additive attachment target, not a runtime source of truth.
- Removed `[rebuild]`, `[models]`, and `[providers]` sections from older configs are ignored.
