# Syke Config Reference

Authoritative reference for `~/.syke/config.toml` on the current 0.5 development branch.

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
provider = ""

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
| `provider` | `string` | `""` | Stored provider preference in config view | `SYKE_PROVIDER` |

---

## `[models]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `synthesis` | `string` | `"sonnet"` | Model used for synthesis cycles | `SYKE_SYNC_MODEL` |
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
| `budget` | `float` | `0.50` | Budget cap per synthesis cycle (USD) | `SYKE_SYNC_BUDGET` |
| `max_turns` | `int` | `10` | Max turns per synthesis cycle | `SYKE_SYNC_MAX_TURNS` |
| `threshold` | `int` | `5` | Minimum new events before synthesis runs | `SYKE_SYNC_THRESHOLD` |
| `thinking` | `int` | `2000` | Thinking token budget | `SYKE_SYNC_THINKING` |
| `timeout` | `int` | `600` | Wall-clock timeout in seconds | `SYKE_SYNC_TIMEOUT` |
| `first_run_budget` | `float` | `2.00` | Higher cold-start budget | `SYKE_SETUP_SYNC_BUDGET` |
| `first_run_max_turns` | `int` | `25` | Higher cold-start turn limit | `SYKE_SETUP_SYNC_MAX_TURNS` |

---

## `[daemon]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `interval` | `int` | `900` | Loop interval in seconds | `SYKE_DAEMON_INTERVAL` |

---

## `[ask]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `budget` | `float` | `1.00` | Budget cap for `syke ask` | `SYKE_ASK_BUDGET` |
| `max_turns` | `int` | `15` | Max turns for ask agent | `SYKE_ASK_MAX_TURNS` |
| `timeout` | `int` | `300` | Ask timeout in seconds | `SYKE_ASK_TIMEOUT` |

---

## `[rebuild]`

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `budget` | `float` | `3.00` | Budget cap for rebuild flows | `SYKE_REBUILD_BUDGET` |
| `max_turns` | `int` | `20` | Max turns for rebuild | `SYKE_REBUILD_MAX_TURNS` |
| `thinking` | `int` | `30000` | Thinking token budget for rebuild | `SYKE_REBUILD_THINKING` |

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
| `chatgpt_export` | `string` | `"~/Downloads"` | ChatGPT export search path |

### `[paths.distribution]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `claude_md` | `string` | `"~/.claude/CLAUDE.md"` | Current Claude Code include target |
| `skills_dirs` | `array[string]` | Claude/Codex/Cursor/Windsurf skill dirs | Skill installation targets |
| `hermes_home` | `string` | `"~/.hermes"` | Hermes home directory |

Example:

```toml
[paths]
data_dir = "~/.syke/data"
auth = "~/.syke/auth.json"

[paths.sources]
claude_code = "~/.claude"
codex = "~/.codex"
chatgpt_export = "~/Downloads"

[paths.distribution]
claude_md = "~/.claude/CLAUDE.md"
hermes_home = "~/.hermes"
```

---

## `[runtime]`

Legacy runtime config surface. Syke now routes `ask` and synthesis through Pi only.

| Key | Type | Default | Meaning | Env override |
|---|---|---|---|---|
| `backend` | `string` | `"pi"` | Canonical runtime backend. Keep this at `"pi"`. | `SYKE_RUNTIME` |

The field remains in config so older installs do not break, but `syke.llm.runtime_switch` always resolves to Pi.

Example:

```toml
[runtime]
backend = "pi"
```

### Legacy compatibility

For one release cycle, the legacy top-level `runtime` key is accepted as an alias for `[runtime].backend`:

```toml
runtime = "pi"  # Equivalent to [runtime] backend = "pi"
```

This alias will be removed in a future release. New configurations should use `[runtime].backend = "pi"`.

---

## `[providers]`



`[providers]` stores non-secret provider settings that Syke translates into Pi-native workspace settings and environment variables. Secrets still go through `syke auth set` into `~/.syke/auth.json`.

| Field | Applies to | Meaning |
|---|---|---|
| `endpoint` | `azure` | Azure OpenAI endpoint |
| `base_url` | `azure-ai`, `openai`, `ollama`, `vllm`, `llama-cpp` | Base URL override |
| `model` | Pi-native providers | Provider-specific runtime model name |
| `api_version` | `azure` | Legacy Azure config input. Syke normalizes Azure to Pi's `v1` Responses contract. |

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
| `azure-ai` | `AZURE_AI_API_BASE` | `base_url` |
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
- `skills_dirs` is written as a normal TOML array.
- The memex is the product artifact. `claude_md` is one current distribution target.
