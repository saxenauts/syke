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

[models]
[synthesis]
[daemon]
[ask]
[rebuild]
[paths]
[providers]
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

## `[models]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `synthesis` | `string` | `"sonnet"` | Fallback synthesis model hint. When a provider is active, Syke resolves this against that provider and may require `[providers.<id>].model` for an exact Pi-native model ID. | `SYKE_SYNC_MODEL` |
| `ask` | `string \| null` | `null` | Model used for `syke ask`; provider default if unset | `SYKE_ASK_MODEL` |
| `rebuild` | `string` | `"opus"` | Model used for rebuild flows | `SYKE_REBUILD_MODEL` |

Example:

```toml
[models]
synthesis = "sonnet"
ask = "sonnet"
rebuild = "opus"
```

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
| `auth` | `string` | `"~/.syke/auth.json"` | Auth store path | `SYKE_AUTH_PATH` |

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
auth = "~/.syke/auth.json"

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

## `[providers]`

`[providers]` stores non-secret provider settings that Syke translates into Pi-native workspace settings and environment variables. Secrets still go through `syke auth set` into `~/.syke/auth.json`.

| Field | Applies to | Meaning |
|---|---|---|
| `endpoint` | `azure` | Azure OpenAI endpoint |
| `base_url` | `openai`, `ollama`, `vllm`, `llama-cpp` | Base URL override |
| `model` | Pi-native providers | Exact provider-specific Pi runtime model name. This is the preferred place to pin the runtime model. |
| `api_version` | `azure` | Azure config input. Syke normalizes Azure to Pi's `v1` Responses contract. |

Example:

```toml
[providers.azure]
endpoint = "https://my-deployment.openai.azure.com"
model = "gpt-4o"
api_version = "v1"

[providers.openai]
model = "gpt-4o"

[providers.ollama]
base_url = "http://localhost:11434"
model = "llama3.2"
```

Runtime env overrides:

| Provider | Variable | Overrides |
|---|---|---|
| `azure` | `AZURE_API_BASE` | `endpoint` |
| `azure` | `AZURE_API_VERSION` | `api_version` |
| `openai` | `OPENAI_BASE_URL` | `base_url` |
| `ollama` | `OLLAMA_HOST` | `base_url` |
| `vllm` | `VLLM_API_BASE` | `base_url` |
| `llama-cpp` | `LLAMA_CPP_API_BASE` | `base_url` |

---

## Minimal Example

```toml
user = "saxenauts"
timezone = "auto"

[models]
synthesis = "sonnet"

[synthesis]
budget = 0.50
max_turns = 10

[daemon]
interval = 900

[paths]
data_dir = "~/.syke/data"
auth = "~/.syke/auth.json"

[providers.openai]
model = "gpt-4o"
```

---

## Notes

- Unknown keys in typed sections are ignored with warnings.
- Provider selection does not live in `config.toml`. Use `syke auth use <provider>` for the persisted choice, or override per-process with `SYKE_PROVIDER` or per-command with `--provider`.
- `skills_dirs` is written as a normal TOML array.
- The memex is the product artifact. `claude_md` is one current additive attachment target, not a runtime source of truth.
- Legacy distribution-only paths from older configs are ignored.
