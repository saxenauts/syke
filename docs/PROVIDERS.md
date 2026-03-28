# Syke Providers

Authoritative provider reference for the current CLI/runtime surface.

---

## Fast Path

Pick one provider, make it active, then confirm the resolved runtime:

```bash
codex login
syke auth use codex

# or:
syke auth set openai --api-key <key> --model gpt-5-mini --use
syke auth status
```

`syke auth set` stores credentials and non-secret config. Add `--use` when you want that provider to become active immediately.

---

## Runtime

Syke runs on Pi.

`syke.llm.pi_runtime` is the runtime routing module used by the CLI. It dispatches to the Pi backends for both `ask` and synthesis, with daemon IPC reuse on the ask path when available.

---

## Provider Resolution Order

Syke resolves provider selection in this exact order:

1. CLI flag: `--provider <id>`
2. Env var: `SYKE_PROVIDER`
3. Auth store: `~/.syke/auth.json` `active_provider`

Source: `syke/llm/env.py::resolve_provider()`.

---

## Provider Matrix

| Provider | Setup | Requires | Env var token override | Base URL | Special behavior |
|---|---|---|---|---|---|
| `codex` | `codex login`, then `syke auth use codex` | Valid `~/.codex/auth.json` | None | Pi OAuth/auth flow | Pi-native Codex provider |
| `openrouter` | `syke auth set openrouter --api-key <key> --use` | OpenRouter API key | `SYKE_OPENROUTER_API_KEY` | `https://openrouter.ai/api` | Maps directly to Pi `openrouter` |
| `zai` | `syke auth set zai --api-key <key> --use` | z.ai API key | `SYKE_ZAI_API_KEY` | `https://api.z.ai/api/anthropic` | Maps directly to Pi `zai` |
| `kimi` | `syke auth set kimi --api-key <key> --use` | Kimi API key | `SYKE_KIMI_API_KEY` | `https://api.kimi.com/coding` | Maps directly to Pi `kimi-coding` |
| `openai` | `syke auth set openai --api-key <key> --model MODEL --use` | OpenAI API key | `OPENAI_API_KEY` | optional custom base URL | Pi built-in `openai` provider |
| `azure` | `syke auth set azure --api-key <key> --endpoint URL --model MODEL --use` | Azure OpenAI API key + endpoint + model | `AZURE_API_KEY` | resource endpoint | Syke normalizes to Pi's `azure-openai-responses` contract |
| `ollama` | `syke auth set ollama --model MODEL --use` | local Ollama | None | `http://localhost:11434` or override | Syke-generated Pi extension |
| `vllm` | `syke auth set vllm --base-url URL --model MODEL --use` | local/server vLLM | provider auth or env | custom base URL | Syke-generated Pi extension |
| `llama-cpp` | `syke auth set llama-cpp --base-url URL --model MODEL --use` | local/server llama.cpp | provider auth or env | custom base URL | Syke-generated Pi extension |

Source: `syke/llm/providers.py` and `syke/llm/env.py`.

---

## Auth Commands

| Command | What it does |
|---|---|
| `syke auth set <provider> ... --use` | Store credentials/config and make that provider active |
| `syke auth use <provider>` | Switch the active provider to an already configured provider |
| `syke auth status` | Show the selected runtime, auth source, model, endpoint, and configured providers |
| `syke auth status --json` | Machine-readable provider/auth/model/endpoint resolution |
| `syke auth unset <provider>` | Remove stored credentials for provider; clears active provider if removed |

Storage details:

- Credentials are stored in `~/.syke/auth.json` with atomic writes and `0600` permissions.
- Provider-specific env vars override stored auth tokens for providers that define `token_env_var`.
- `syke status` and `syke auth status` now show the resolved selection source, auth source, model source, and endpoint source so users can see exactly what will run.

Source: `syke/llm/auth_store.py`, `syke/cli.py`, `syke/llm/env.py`.

---

## Pi-Native Translation Notes

- `azure` config is migrated on read into Pi's Azure Responses contract.
- `openai` can optionally override Pi's built-in provider base URL.
- `ollama`, `vllm`, and `llama-cpp` are exposed through generated `.pi/extensions/syke-provider.mjs`.
- Legacy Claude/LiteLLM/Codex translation proxies were removed from the runtime path.

---

## Quick Setup Flows

### Codex

```bash
codex login
syke auth use codex
```

### API-Key Providers (OpenRouter, z.ai, Kimi)

```bash
syke auth set openrouter --api-key <key> --use
syke auth set zai --api-key <key> --use
syke auth set kimi --api-key <key> --use
```

### Pi Runtime Providers

```bash
syke auth set azure --api-key <key> --endpoint URL --model MODEL --use
syke auth set openai --api-key <key> --model MODEL --use
syke auth set ollama --model llama3.2 --use
syke auth set vllm --base-url URL --model MODEL --use
syke auth set llama-cpp --base-url URL --model MODEL --use
```

Use `syke auth status` to confirm active provider and configured credentials.
