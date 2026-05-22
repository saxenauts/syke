---
name: syke
description: "Local-first cross-harness memory for agents. Syke observes activity across supported harnesses, keeps a current memex in context, and gives agents `syke ask`, `syke memex`, and `syke record` for continuity across sessions."
version: 0.5.7
author: saxenauts
license: AGPL-3.0-only
metadata:
  hermes:
    tags: [Memory, Context, Identity, Cross-Platform, Agentic-Memory]
    related_skills: []
    requires_toolsets: [terminal]
  requires:
    bins: ["syke"]
  install:
    - id: pipx
      kind: pipx
      package: syke
      bins: ["syke"]
      label: "Install Syke (pipx)"
---

# Syke

Read the user's memex before doing anything else. It is the current map of what is active, what changed, and where deeper evidence lives.

Canonical memex path: `~/.syke/MEMEX.md`

## When to Use

- **`syke ask`**: deeper timeline and evidence-backed queries
- **`syke memex`**: fastest read of the current memex
- **`syke record`**: write observations back into memory
- **`syke status`**: quick operational snapshot
- **`syke doctor`**: deeper diagnostic when setup or runtime looks wrong

## Quick Reference

| Command | Use | Exit 0 | Exit 1 |
|---------|-----|--------|--------|
| `syke ask "question"` | Deep memory query | Answer on stdout | Error on stderr, stdout empty |
| `syke memex` | Current memex | Memex on stdout | Error message |
| `syke record "text"` | Write observation | Confirmation | Error message |
| `syke status` | Runtime snapshot | Status on stdout | Error message |
| `syke doctor` | Health check | All OK | Issues found |

## Procedure

1. Read the memex already in context or call `syke memex`.
   If you need the file directly, start with `~/.syke/MEMEX.md`.
2. Use `syke ask` when the memex is not enough.
3. Use `syke record` after useful work so the next session inherits it.
4. Use `syke status` for a quick state check.
5. Use `syke doctor` when setup or runtime looks wrong.

## Pitfalls

- If `syke ask` fails, do not treat stderr as the answer. Fall back to `syke memex`.
- If `syke ask` is killed by a caller timeout, fall back to `syke memex`.
- Some sandboxes can read the memex but cannot open the live store. In those cases, use `syke memex` or the injected memex there, and run `syke ask` from a trusted host shell if needed.
- If the memex is empty, Syke may not be set up yet or synthesis may not have produced a useful memex.
- The background loop can lag behind the newest event. `syke ask` can still search the underlying timeline.

## Verification

- After `syke ask`, check the exit code. Exit 0 means answer on stdout. Exit 1 means failure on stderr.
- After `syke record`, exit 0 means the observation was written.
- After setup, `syke doctor` confirms health.

## Setup & Onboarding

If Syke is not installed or configured, guide setup first.

For humans at a terminal:

1. Install: `pipx install syke` (or `uv tool install syke`)
2. Run: `syke setup`
3. Follow the interactive provider, source, and daemon prompts.
4. Confirm with `syke doctor`.

For unattended agents, installers, and CI:

1. Run `syke setup --agent`.
2. Parse the JSON `status`, `next_steps`, and `exit_code` fields.
3. If `status` is `"needs_runtime"`, install Node.js 20+ (22 LTS recommended)
   and rerun `syke setup --agent`.
4. If `status` is `"needs_provider"`, configure provider auth with
   `syke auth set <provider> --api-key <API_KEY> --use` or
   `syke auth login <provider> --use`, then rerun `syke setup --agent`.
5. If `status` is `"complete"`, stop setup work and follow the returned
   `next_steps`. Do not loop on setup.
6. If `status` is `"failed"`, read the `error` field and fix that issue.

Common providers: `anthropic`, `openai`, `azure-openai-responses`, `kimi-coding`, `openrouter`.
For Azure, also pass `--base-url https://<resource>.openai.azure.com/openai/v1` and `--model <model>`.

## Provider Commands

| Command | What It Does |
|---------|-------------|
| `syke auth status` | Show selected provider, auth source, model, and endpoint |
| `syke auth use <name>` | Switch active provider |
| `syke auth set <name> --api-key <KEY> --use` | Store credentials and make this the active provider |
| `syke config show` | Show effective config |

Provider resolution: CLI `--provider` flag > `SYKE_PROVIDER` env > Pi `defaultProvider` in `~/.syke/pi-agent/settings.json`.
