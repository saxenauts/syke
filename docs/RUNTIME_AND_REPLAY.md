# Runtime And Replay

Canonical runtime boundary after the replay split.

## Runtime (Syke Repo)

The Syke product/runtime surface lives in this repository:

- product CLI and daemon (`syke ...`)
- observe/sync/synthesis orchestration
- distribution (`syke context`, `syke ask`, `syke record`)
- user-facing docs and release artifacts

Primary local runtime artifacts:

- `~/.syke/syke.db`
- `~/.syke/MEMEX.md`
- `~/.syke/PSYCHE.md`
- `~/.syke/adapters/{source}.md`
- `~/.syke/pi-agent/{auth.json,settings.json,models.json}`

## Replay Lab (Separate Repo)

Replay/eval/research infrastructure is intentionally outside this repo.

Current canonical path:

- `/Users/saxenauts/Documents/personal/syke-replay-lab`

Use replay-lab for:

- replay experiments
- benchmark orchestration
- judge calibration and packet tooling
- lab-only research assets

Do not reintroduce mirror copies of replay-lab code or research into this
repo.

## Cross-Repo Contract

The replay-lab may import Syke modules. It should resolve Syke source via:

- `SYKE_REPO_ROOT` (explicit override), defaulting to sibling layout

So with the current layout:

- Syke repo: `/Users/saxenauts/Documents/personal/syke`
- Replay repo: `/Users/saxenauts/Documents/personal/syke-replay-lab`

the default resolution works without additional setup.

## Operator Notes

- If a command references
  `/Users/saxenauts/Documents/personal/syke/_internal/syke-replay-lab`,
  it is stale and should be updated.
- Runtime state under replay `runs/` is local operational state; treat it as
  mutable, and avoid assuming it is a commit-backed source of truth.
