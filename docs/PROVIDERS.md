# Syke Providers

Authoritative provider reference for the current CLI/runtime surface.

---

## Fast Path

Pick a provider you already trust, activate it, then confirm the resolved runtime:

```bash
syke auth set openai --api-key <key> --model gpt-5.4 --use
syke auth status
```

`syke auth set` stores Pi-native credentials and config under `~/.syke/pi-agent/`. Add `--use` when you want that provider to become active immediately.

---

## Runtime

Syke runs on Pi.

Pi is the only runtime. Syke reads Pi's provider/model reality from the live Pi catalog and launches the Pi runtime with Syke-owned Pi state under `~/.syke/pi-agent/`.

---

## Provider Resolution Order

Syke resolves provider selection in this exact order:

1. CLI flag: `--provider <id>`
2. Env var: `SYKE_PROVIDER`
3. Pi settings: `~/.syke/pi-agent/settings.json` `defaultProvider`

Source: `syke/llm/env.py::resolve_provider()`.

Important:

- `--provider` and `SYKE_PROVIDER` are per-process routing overrides.
- The daemon-safe active provider path is persisted Pi-owned state under `~/.syke/pi-agent/`.
- `syke auth set ... --use`, `syke auth login ... --use`, `syke auth use`, and `syke setup` are the supported ways to set that active state.

---

## Provider Matrix

| Provider Class | Example | Notes |
|---|---|---|
| API-key Pi provider | `syke auth set openrouter --api-key <key> --model openai/gpt-5.1-codex --use` | Use Pi provider IDs such as `openai`, `openrouter`, `zai`, `kimi-coding`, or `azure-openai-responses`. |
| Pi-native OAuth provider | `syke auth login openai-codex --use` | Uses Pi's native login flow and stores the result in `~/.syke/pi-agent/auth.json`. |
| Custom OpenAI-compatible provider | `syke auth set localproxy --base-url URL --model MODEL --use` | For self-hosted or local OpenAI-compatible endpoints that are not in Pi's built-in catalog. |

Syke does not ship its own provider registry anymore. The available built-in providers and models come from Pi's live catalog.

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

- Credentials are stored in `~/.syke/pi-agent/auth.json`.
- Active provider and model are stored in `~/.syke/pi-agent/settings.json`.
- Provider endpoint/base-url overrides are stored in `~/.syke/pi-agent/models.json`.
- `syke status` and `syke auth status` show the resolved selection source, auth source, model source, and endpoint source so users can see exactly what will run.

Source: `syke/pi_state.py`, `syke/cli_support/providers.py`, `syke/llm/env.py`.

---

## Pi-Native Notes

- `azure-openai-responses` requires a base URL or resource endpoint before it is ready.
- Advanced Azure API-version overrides are Pi-native env config, not persisted by Syke.
- Provider activation is probe-gated and daemon-safe: `syke setup`, `syke auth set --use`, `syke auth login --use`, and `syke auth use` only commit active state after Syke finds persisted auth/config and a live Pi request succeeds.

---

## Quick Setup Flows

### Example API-Key Provider

```bash
syke auth set openai --api-key <key> --model gpt-5.4 --use
```

### Pi-Native OAuth Provider

```bash
syke auth login openai-codex --use
```

### Other Supported Providers

```bash
syke auth set openrouter --api-key <key> --model openai/gpt-5.1-codex --use
syke auth set zai --api-key <key> --model glm-5 --use
syke auth set kimi-coding --api-key <key> --model k2p5 --use
syke auth set azure-openai-responses --api-key <key> --endpoint URL --model gpt-5.4-mini --use
syke auth set localproxy --base-url URL --model MODEL --use
```

Use `syke auth status` to confirm active provider and configured credentials.
