# Syke Config Reference

Authoritative reference for `~/.syke/config.toml` in v0.4.5.

---

## Precedence

Effective values are resolved in this order (last wins):

1. Hardcoded defaults (dataclasses in `syke/config_file.py`)
2. `~/.syke/config.toml`
3. Environment variables (read in `syke/config.py` and provider runtime)

This matches the runtime contract in `syke/config_file.py` and `syke/config.py`.

---

## CLI Commands

| Command | Purpose |
|---|---|
| `syke config init` | Generate commented `~/.syke/config.toml` from `generate_default_config()` |
| `syke config init --force` | Overwrite existing config file |
| `syke config show` | Show effective config after file + env overrides |
| `syke config show --raw` | Print raw TOML file contents |
| `syke config path` | Print config path (`~/.syke/config.toml`) |

---

## Top-Level Keys

| Setting | Type | Default | Description | Env override |
|---|---:|---|---|---|
| `user` | `string` | `""` (resolved to system username if empty) | Default user ID for data directory and commands | `SYKE_USER` |
| `timezone` | `string` | `"auto"` | Timezone mode for time rendering/parsing | `SYKE_TIMEZONE` |
| `provider` | `string` | `""` | Stored provider preference in config view; runtime provider routing uses provider resolution order | `SYKE_PROVIDER` (runtime provider selection) |

---

## `[models]`

| Setting | Type | Default | Description | Env override |
|---|---:|---|---|---|
| `synthesis` | `string` | `"sonnet"` | Model for periodic synthesis cycles | `SYKE_SYNC_MODEL` |
| `ask` | `string \| null` | `null` | Model for `syke ask`; if unset, provider default is used | `SYKE_ASK_MODEL` |
| `rebuild` | `string` | `"opus"` | Model for full rebuild operations | `SYKE_REBUILD_MODEL` |

---

## `[sources]`

| Setting | Type | Default | Description | Env override |
|---|---:|---|---|---|
| `claude-code` | `bool` | `true` | Enable Claude Code ingestion | None |
| `codex` | `bool` | `true` | Enable Codex ingestion | None |
| `chatgpt` | `bool` | `true` | Enable ChatGPT export ingestion | None |
| `gmail` | `bool` | `false` | Enable Gmail ingestion | None |
| `github.enabled` | `bool` | `true` | Enable GitHub ingestion | None |
| `github.username` | `string` | `""` | GitHub username used by adapter configuration | None |

Notes: TOML supports both flat booleans and `[sources.github]` table. Unknown keys are ignored with warnings.

---

## `[synthesis]`

| Setting | Type | Default | Description | Env override |
|---|---:|---|---|---|
| `budget` | `float` | `0.50` | USD budget cap per synthesis cycle | `SYKE_SYNC_BUDGET` |
| `max_turns` | `int` | `10` | Max model turns per synthesis cycle | `SYKE_SYNC_MAX_TURNS` |
| `threshold` | `int` | `5` | Minimum new events before synthesis runs | `SYKE_SYNC_THRESHOLD` |
| `thinking` | `int` | `2000` | Thinking budget/tokens for synthesis | `SYKE_SYNC_THINKING` |
| `first_run_budget` | `float` | `2.00` | Higher first-run budget for cold start synthesis | `SYKE_SETUP_SYNC_BUDGET` |
| `first_run_max_turns` | `int` | `25` | Higher first-run turn limit | `SYKE_SETUP_SYNC_MAX_TURNS` |

---

## `[daemon]`

| Setting | Type | Default | Description | Env override |
|---|---:|---|---|---|
| `interval` | `int` | `900` | Daemon sync interval in seconds | `SYKE_DAEMON_INTERVAL` |

---

## `[ask]`

| Setting | Type | Default | Description | Env override |
|---|---:|---|---|---|
| `budget` | `float` | `1.00` | USD budget cap for `syke ask` | `SYKE_ASK_BUDGET` |
| `max_turns` | `int` | `8` | Max turns for `syke ask` | `SYKE_ASK_MAX_TURNS` |
| `timeout` | `int` | `120` | Ask timeout in seconds | `SYKE_ASK_TIMEOUT` |

---

## `[rebuild]`

| Setting | Type | Default | Description | Env override |
|---|---:|---|---|---|
| `budget` | `float` | `3.00` | USD budget cap for full rebuild | `SYKE_REBUILD_BUDGET` |
| `max_turns` | `int` | `20` | Max turns for rebuild runs | `SYKE_REBUILD_MAX_TURNS` |
| `thinking` | `int` | `30000` | Thinking budget/tokens for rebuild | `SYKE_REBUILD_THINKING` |

---

## `[distribution]`

| Setting | Type | Default | Description | Env override |
|---|---:|---|---|---|
| `claude-code` | `bool` | `true` | Enable Claude Code distribution target | None |
| `claude-desktop` | `bool` | `true` | Enable Claude Desktop trusted-folder integration | None |
| `hermes` | `bool` | `true` | Enable Hermes harness distribution target | None |

---

## `[privacy]`

| Setting | Type | Default | Description | Env override |
|---|---:|---|---|---|
| `redact_credentials` | `bool` | `true` | Redact credentials before events are persisted | None |
| `skip_private_messages` | `bool` | `true` | Skip private messages during ingestion filtering | None |

---

## `[paths]`

| Setting | Type | Default | Description | Env override |
|---|---:|---|---|---|
| `data_dir` | `string` | `"~/.syke/data"` | Root data directory | `SYKE_DATA_DIR` |
| `auth` | `string` | `"~/.syke/auth.json"` | Auth store file path | `SYKE_AUTH_PATH` |

### `[paths.sources]`

| Setting | Type | Default | Description | Env override |
|---|---:|---|---|---|
| `claude_code` | `string` | `"~/.claude"` | Source path for Claude Code data | None |
| `codex` | `string` | `"~/.codex"` | Source path for Codex data | None |
| `chatgpt_export` | `string` | `"~/Downloads"` | Directory scanned for ChatGPT exports | None |

### `[paths.distribution]`

| Setting | Type | Default | Description | Env override |
|---|---:|---|---|---|
| `claude_md` | `string` | `"~/.claude/CLAUDE.md"` | Global memex/context output file | None |
| `skills_dirs` | `array[string]` | `~/.claude/skills`, `~/.codex/skills`, `~/.cursor/skills`, `~/.windsurf/skills` | Skill distribution targets | None |
| `hermes_home` | `string` | `"~/.hermes"` | Hermes base directory | None |

---

## `[providers]`

Provider-specific settings for LiteLLM-based providers (azure, azure-ai, openai, ollama, vllm, llama-cpp). These are **non-secret** settings only. Secrets (API keys, auth tokens) go in `~/.syke/auth.json` via `syke auth set`.

### `[providers.<name>]` Fields

| Field | Applies To | Description |
|---|---|---|
| `endpoint` | azure | Azure OpenAI endpoint URL (e.g. `https://my-deployment.openai.azure.com`) |
| `base_url` | azure-ai, ollama, vllm, llama-cpp | Base URL for the local/remote API server |
| `model` | all | Model identifier to use (provider-specific format) |
| `api_version` | azure | Azure OpenAI API version (e.g. `2024-02-01`) |

### Environment Variable Overrides

Env vars take precedence over `config.toml` values.

| Provider | Variable | Overrides config.toml field |
|---|---|---|
| azure | `AZURE_API_KEY` | auth_token (in auth.json, set via CLI) |
| azure | `AZURE_API_BASE` | endpoint |
| azure | `AZURE_API_VERSION` | api_version |
| azure-ai | `AZURE_AI_API_KEY` | auth_token (in auth.json, set via CLI) |
| azure-ai | `AZURE_AI_API_BASE` | base_url |
| openai | `OPENAI_API_KEY` | auth_token (in auth.json, set via CLI) |
| openai | `OPENAI_BASE_URL` | base_url |
| ollama | `OLLAMA_HOST` | base_url |
| vllm | `VLLM_API_BASE` | base_url |
| llama-cpp | `LLAMA_CPP_API_BASE` | base_url |

### Example Configurations

**Azure OpenAI:**

```toml
[providers.azure]
endpoint = "https://my-deployment.openai.azure.com"
model = "gpt-4o"
api_version = "2024-02-01"
```

**Azure AI Foundry:**

```toml
[providers.azure-ai]
base_url = "https://my-project.services.ai.azure.com/models"
model = "Kimi-K2.5"
```

Note: Azure AI Foundry does NOT use `api_version`. The model name uses the catalog name (e.g., `Kimi-K2.5`, `Phi-4`).

**OpenAI:**

```toml
[providers.openai]
model = "gpt-4o"
# Optional: override base URL for proxies
# base_url = "https://api.openai.com/v1"
```

**Ollama (local):**

```toml
[providers.ollama]
base_url = "http://localhost:11434"
model = "llama3.2"
```

**vLLM (self-hosted):**

```toml
[providers.vllm]
base_url = "http://localhost:8000"
model = "meta-llama/Llama-3.2-8B-Instruct"
```

**llama.cpp (server mode):**

```toml
[providers.llama-cpp]
base_url = "http://localhost:8080"
model = "llama3.2"
```

**Note:** API keys are never stored in `config.toml`. Use `syke auth set <provider> --api-key KEY` to store them securely in `~/.syke/auth.json`.

---

## Full Default `config.toml` (Generated)

This is the template returned by `generate_default_config()` in `syke/config_file.py`.
`user` and `provider` are filled dynamically at generation time.

```toml
# Syke configuration
# Docs: https://github.com/saxenauts/syke

# -- Identity ---------------------------------------------------------------
user = "your-user"
timezone = "auto"

# LLM provider (selected at setup)
# Options: claude-login, codex, openrouter, zai, kimi, azure, azure-ai, openai, ollama, vllm, llama-cpp
provider = ""

# -- Model selection per task -----------------------------------------------
# Provider-native names for now. When multi-provider lands, these become
# "provider/model" format (e.g. "anthropic/claude-sonnet-4-6").
[models]
synthesis = "sonnet"     # cheap -- runs every 15 min
# ask = ""              # interactive -- defaults to provider's default
rebuild = "opus"         # expensive -- full reconstruction, runs rarely

# -- Data sources ------------------------------------------------------------
[sources]
claude-code = true
codex = true
chatgpt = true
gmail = false

[sources.github]
enabled = true
username = "your-user"

# -- Synthesis agent ---------------------------------------------------------
[synthesis]
budget = 0.50            # USD per run
max_turns = 10
threshold = 5            # min new events before synthesizing
thinking = 2000          # thinking budget (tokens)
first_run_budget = 2.00  # first synthesis gets more room
first_run_max_turns = 25

# -- Background daemon ------------------------------------------------------
[daemon]
interval = 900           # seconds between sync cycles

# -- Ask agent (syke ask "question") ----------------------------------------
[ask]
budget = 1.00
max_turns = 8
timeout = 120            # seconds

# -- Rebuild (syke rebuild) --------------------------------------------------
[rebuild]
budget = 3.00
max_turns = 20
thinking = 30000

# -- Distribution targets ---------------------------------------------------
[distribution]
claude-code = true
claude-desktop = true
hermes = true

# -- Privacy filters (applied before events enter DB) -----------------------
[privacy]
redact_credentials = true
skip_private_messages = true

# -- Paths ------------------------------------------------------------------
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

# -- Provider settings (LiteLLM gateway) --------------------------------------
# Non-secret settings per provider. Secrets go in ~/.syke/auth.json via CLI.
# Uncomment and fill in the provider you want to use:
#
# [providers.azure]
# endpoint = "https://my-deployment.openai.azure.com"
# model = "gpt-4o"
# api_version = "2024-02-01"
#
# [providers.azure-ai]
# base_url = "https://my-project.services.ai.azure.com/models"
# model = "Kimi-K2.5"
#
# [providers.openai]
# model = "gpt-4o"
#
# [providers.ollama]
# base_url = "http://localhost:11434"
# model = "llama3.2"
#
# [providers.vllm]
# base_url = "http://localhost:8000"
# model = "meta-llama/Llama-3.2-8B-Instruct"
#
# [providers.llama-cpp]
# base_url = "http://localhost:8080"
# model = "llama3.2"
```

Note: runtime provider support includes `azure-ai` and `kimi` in addition to the options listed in this template comment.
