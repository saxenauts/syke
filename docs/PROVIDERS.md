# Syke Providers

Authoritative provider reference for v0.4.5.

---

## Provider Resolution Order

Syke resolves provider selection in this exact order:

1. CLI flag: `--provider <id>`
2. Env var: `SYKE_PROVIDER`
3. Auth store: `~/.syke/auth.json` `active_provider`
4. Auto-detect `claude-login` (if Claude CLI session auth is available)

Source: `syke/llm/env.py::resolve_provider()`.

---

## Provider Matrix

| Provider | Setup | Requires | Env var token override | Base URL | Special behavior |
|---|---|---|---|---|---|
| `claude-login` | Run `claude login`, then `syke auth use claude-login` (optional) | Claude CLI installed + valid session auth | None | Anthropic default | No API key in Syke; runtime sets `ANTHROPIC_API_KEY=""` |
| `codex` | Run `codex login`, then `syke auth use codex` | Valid `~/.codex/auth.json` with `account_id` | None | Local proxy (`http://127.0.0.1:<port>`) | `needs_proxy=True`; starts Codex translator proxy automatically |
| `openrouter` | `syke auth set openrouter --api-key <key>` | OpenRouter API key | `SYKE_OPENROUTER_API_KEY` | `https://openrouter.ai/api` | Uses Anthropic-compatible endpoint with auth token |
| `zai` | `syke auth set zai --api-key <key>` | z.ai API key | `SYKE_ZAI_API_KEY` | `https://api.z.ai/api/anthropic` | Uses Anthropic-compatible endpoint with auth token |
| `kimi` | `syke auth set kimi --api-key <key>` | Kimi API key | `SYKE_KIMI_API_KEY` | `https://api.kimi.com/coding` | Uses Anthropic-compatible endpoint with auth token |

Source: `syke/llm/providers.py` and `syke/llm/env.py`.

---

## Auth Commands

| Command | What it does |
|---|---|
| `syke auth set <provider> --api-key <key>` | Store credentials in `~/.syke/auth.json` and set provider active |
| `syke auth use <provider>` | Switch active provider; validates credentials for non-`claude-login` providers |
| `syke auth status` | Show active provider and configured providers |
| `syke auth unset <provider>` | Remove stored credentials for provider; clears active provider if removed |

Storage details:

- Credentials are stored in `~/.syke/auth.json` with atomic writes and `0600` permissions.
- Provider-specific env vars override stored auth tokens for providers that define `token_env_var`.

Source: `syke/llm/auth_store.py`, `syke/cli.py`, `syke/llm/env.py`.

---

## Codex Proxy Behavior

`codex` is not a direct API-key provider. In `syke/llm/providers.py`, it is declared with `needs_proxy=True`.

At runtime (`syke/llm/env.py::_build_codex_env()`):

1. Syke validates and refreshes Codex credentials from `~/.codex/auth.json`.
2. Syke starts local proxy server (`syke/llm/codex_proxy.py`).
3. Syke sets env vars for the Agent SDK:
   - `ANTHROPIC_BASE_URL` → local proxy address
   - `ANTHROPIC_AUTH_TOKEN` → proxy auth token
   - `ANTHROPIC_API_KEY` → placeholder (proxy handles real auth)

The proxy translates Claude Messages requests into Codex backend responses.

---

## Quick Setup Flows

### Claude Login

```bash
claude login
syke auth use claude-login
```

### Codex

```bash
codex login
syke auth use codex
```

### API-Key Providers (OpenRouter, z.ai, Kimi)

```bash
syke auth set openrouter --api-key <key>
syke auth set zai --api-key <key>
syke auth set kimi --api-key <key>
```

Use `syke auth status` to confirm active provider and configured credentials.
