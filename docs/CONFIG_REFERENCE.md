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
[rebuild]
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
| `max_turns` | `int` | `10` | Scales first-run timeout proportionally | `SYKE_SYNC_MAX_TURNS` |
| `threshold` | `int` | `5` | Minimum new events before synthesis runs | `SYKE_SYNC_THRESHOLD` |
| `thinking` | `int` | `8192` | Thinking token budget | `SYKE_SYNC_THINKING` |
| `timeout` | `int` | `600` | Wall-clock timeout in seconds | `SYKE_SYNC_TIMEOUT` |
| `first_run_max_turns` | `int` | `25` | Higher cold-start turn limit (scales timeout) | `SYKE_SETUP_SYNC_MAX_TURNS` |

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

---

## `[paths]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `data_dir` | `string` | `"~/.syke/data"` | Root Syke data directory | `SYKE_DATA_DIR` |

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

Example:

```toml
[paths]
data_dir = "~/.syke/data"

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
max_turns = 10

[daemon]
interval = 900

[paths]
data_dir = "~/.syke/data"
```

---

## Notes

- Unknown keys in typed sections are ignored with warnings.
- Provider/model/auth state does not live in `config.toml`. Use `syke auth` or `syke setup` for persisted Pi-native state, override per-process with `SYKE_PROVIDER`, or override per-command with `--provider`.
- `skills_dirs` is written as a normal TOML array.
- The memex is the product artifact. `claude_md` is one current additive attachment target, not a runtime source of truth.
- Legacy `[models]` and `[providers]` sections from older configs are ignored.
