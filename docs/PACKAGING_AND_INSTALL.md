# Packaging and Install Strategy

How Syke is installed today, and how it should be packaged if the goal is an end-to-end product rather than a repo that assumes the user's shell, Python, Node, or PATH are configured a certain way.

This document treats:

- DMG app distribution
- tool installs (`uv tool`, `pipx`, Homebrew later)
- source installs
- SSH/headless installs

as different delivery surfaces over one runtime model.

Unless a section is marked as a target packaging shape, statements in this file describe the current local/tool-install system.

---

## Product Principle

Syke should not be designed around "the user's machine already has the right Python, Node, npm, PATH, shell init, or nvm state."

That is acceptable for developer workflow. It is not acceptable as the core product architecture.

The product stance should be:

- Syke owns its runtime dependencies
- Syke uses absolute paths for background execution
- Syke stores user state separately from installed runtime payloads
- Syke supports multiple install surfaces without changing its runtime contract

This matters especially now that Pi is the canonical runtime substrate.

Pi is not "just a library." In practice, Syke needs:

- Python runtime
- Node runtime
- Pi package/runtime files
- a stable background execution path

If any of those depend on the interactive shell, the product is fragile.

---

## What Is Wrong With The Current Shape

Today the local development story works, but the installed background story is still shell-fragile:

- Pi currently resolves to `~/.syke/bin/pi`
- that path points to Pi's npm-installed CLI script
- the CLI script uses `#!/usr/bin/env node`
- macOS `launchd` does not inherit the user's interactive shell PATH
- so a daemon can fail even when `uv run syke ...` works perfectly in Terminal

That is a design smell, not just a bug.

The immediate lesson is:

- do not make background execution depend on `env node`
- do not make the daemon depend on user-managed runtimes
- do not treat `pipx`, `uv`, `nvm`, or shell startup files as product infrastructure

When the source checkout lives inside a macOS TCC-protected directory, the runtime locator now refuses to register that runtime directly. Launchd only accepts a safe non-editable installed `syke` whose install origin metadata proves it was built from the same checkout (for example `pipx install .`, `uv tool install --force --reinstall --refresh --no-cache .`, or `syke install-current`). Editable installs that import directly from the protected checkout are not launchd-safe, and if no matching non-editable install exists the daemon install fails with guidance to reinstall or move the repo instead of silently pointing at a different binary.

---

## The Runtime Contract

Syke should have one runtime contract regardless of install surface:

1. a Syke executable entrypoint
2. a Python runtime that can run Syke reliably
3. a Node runtime that can run Pi reliably
4. a Pi runtime payload
5. a writable user-owned state directory
6. a background registration mechanism appropriate to the platform

That means installation should always answer these questions explicitly:

- Where is Syke executed from?
- Where is Python?
- Where is Node?
- Where is Pi's CLI entrypoint?
- Where is the writable state root?
- How does the daemon start without shell assumptions?

---

## Separation Of Concerns

Syke should separate three things clearly.

### Product State

User-owned mutable state. This remains outside the installed app/runtime:

- `~/.syke/data/...`
- `~/.syke/auth.json`
- `~/.syke/config.toml`
- `~/.syke/workspace/...`
- logs, session artifacts, caches

### Runtime Payload

Versioned executable assets Syke can launch directly:

- Python runtime or executable
- Node runtime
- Pi package files
- helper launchers

This should live in one of:

- inside `Syke.app`
- or under a versioned per-user runtime root such as `~/.syke/runtime/<version>/<triple>/`

### Installation Surface Matrix

The install surface should not change the runtime contract. Below is the current
canon for how each delivery surface resolves the runtime payload and what to check:

| Surface | Runtime owner | Key validation | Notes |
| --- | --- | --- | --- |
| `uv tool install` | `~/.local/share/uv/tools/syke` owns Python/Node; launcher at `~/.syke/bin/syke`. | `syke doctor` reports `CLI runtime`/`Launcher` pointing back into `uv`. | Prefer `uv tool install --force --reinstall --refresh --no-cache .` while developing or run `syke install-current` so every rebuild matches the checkout and includes packaged assets. |
| `pipx install syke` / `pipx install .` | Pipx manages Python and installs `syke` under `~/.local/pipx/venvs/syke`. | `syke doctor` reports pipx runtime in `CLI runtime`. | Pipx is the canonical non-dev install for macOS/Windows, and launchd watches the stable `~/.syke/bin/syke` wrapper. |
| Homebrew / DMG app | Bundled runtimes inside `Syke.app`; `~/Syke/bin/syke` resolves into the bundle. | Application installer writes `~/.syke/bin/syke` targeting the bundle; `syke doctor` shows that path. | Bundled installs avoid TCC issues because the binary sits outside `~/Documents`/`~/Desktop`. |
| Source checkout / SSH or headless | Repo `.venv` plus `uv run syke`; background loops use `syke daemon run`. | Manual `syke daemon run` logs show `runtime started` and the launcher points at `~/.syke/bin/syke`. | If the checkout lives under a TCC-protected directory, either build a non-editable tool install before registering launchd or keep the daemon in the foreground. |
| Headless shared runtime | Prebuilt runtime staged under `~/.syke/runtime/<version>/<triple>` with Syke.app-style layout. | Same `syke doctor` checks as above; verify `describe_runtime_target` resolves into the shared runtime. | This surface reuses the same runtime contract; validate the launcher path after every refresh. |


---

## Distribution Contract

Packaging is only half the story. Syke also needs a stable distribution model for memory and agent UX across harnesses.

The right contract is:

- Syke holds the authoritative `events.db` and `syke.db` stores, and routes memex projections from that state
- harnesses receive derived, harness-native artifacts
- agents get capability access through CLI and, where appropriate, MCP
- external sandboxes are consumers of injected context, not the source of truth

In other words:

- canonical truth lives in Syke
- distribution surfaces are per-harness projections
- capabilities are exposed separately from passive context injection

This avoids two failure modes:

- forcing every harness to consume the same file format
- forcing every sandbox to connect directly to the live Syke store

---

## Distribution Surface Types

Syke should support four distribution surface types.

### 1. Passive Context Files

Files the harness already knows how to read at session start.

Examples:

- `CLAUDE.md`
- `AGENTS.md`
- `.cursor/rules/*.md`
- other harness-native instruction or memory files

These are the best low-friction surfaces because they:

- work without tool calls
- survive sandbox restrictions
- provide orientation before the first model turn

### 2. Skill Or Workflow Files

Agent-facing onboarding artifacts that teach the agent how to use Syke correctly.

Examples:

- `SKILL.md`
- command markdown bundles
- harness-native prompt packs or rules bundles

These are not the canonical memory and not the transport layer. They are the agent UX layer:

- what Syke is
- when to read the memex
- when to call `syke ask`
- when to write back with `syke record`
- what fallbacks to use when tool access is blocked

### 3. Capability Surfaces

Ways an agent can actively talk to Syke.

Examples:

- `syke context`
- `syke ask`
- `syke record`
- `syke doctor`
- future MCP tools

This is where deeper queries, write-back, diagnostics, and live interactions happen.

### 4. Native-Memory Coexistence Adapters

Harnesses that already have their own memory should not be overwritten blindly.

Instead, Syke should coexist with native memory systems and complement them.

Examples:

- Hermes `MEMORY.md` / `USER.md` / `SOUL.md`
- Claude Code auto memory
- future harness-local memory systems

The adapter should decide whether Syke is:

- the primary context surface
- a secondary memory layer
- a skill/tool companion
- or just a bridge into the live store

---

## Trusted Syke vs External Sandboxes

This boundary should stay explicit.

### Trusted Syke

Trusted Syke is the local first-class runtime that can:

- read and write the canonical DB
- run synthesis
- rebuild workspace state
- update the memex
- install or refresh harness artifacts
- run deeper `ask` queries against the live timeline

### External Sandboxes

External sandboxes should usually be treated as:

- passive recipients of injected context
- optional callers of safe capability surfaces
- not the place where canonical memory state is maintained

This means external sandboxes should degrade gracefully to:

- injected memex
- skill/onboarding instructions
- `syke context`

and only use deeper live queries when the host environment can safely reach Syke's runtime and store.

---

## Harness-Native Distribution Strategy

Syke should not force one universal format across tools. It should project the memex derived from authoritative Syke state into harness-native surfaces.

Current and intended examples:

- Claude Code: `CLAUDE.md` include chain plus rules and optional skills
- Claude Desktop: trusted-folder access now, later MCP/tool access and/or project instructions
- Pi workspace: minimal `AGENTS.md` bootstrap plus `MEMEX.md` in the workspace contract
- Hermes: Syke skill layered alongside Hermes native memory
- Cursor: project rules / instruction files plus optional MCP
- Windsurf: rules/instructions plus optional MCP
- Codex and similar coding CLIs: `AGENTS.md`-style instructions plus direct CLI access where possible

The invariant is:

- one authoritative Syke state, with routed memex projections
- one adapter per harness family
- one projection strategy per harness

---

## CLI And Agent UX Contract

The CLI should be treated as an agent-facing product surface, not just a human utility.

The minimum stable verbs are:

- `syke context` — instant read surface
- `syke ask` — deeper agentic query
- `syke record` — write-back
- `syke doctor` — health and diagnostics

This is the core UX contract a skill or harness integration should teach.

### Agent UX Principles

- read the memex first
- use `context` for fast, guaranteed access
- use `ask` sparingly for deeper retrieval
- use `record` to persist new knowledge
- use `doctor` when capabilities fail

### Why SKILL.md Matters

`SKILL.md` is not just packaging fluff. It is the agent-readable README for how to use Syke well.

Its job is to standardize:

- command discovery
- stdout/stderr expectations
- when to prefer injected context vs live queries
- write-back behavior
- fallback behavior in restricted sandboxes

So the right mental model is:

- memex = the memory artifact
- skill/rules/instructions = the usage guide
- CLI/MCP = the capability surface

---

## Comparison: Honcho vs Syke Distribution

Honcho's current public distribution story leans heavily on:

- hosted or self-hosted memory service
- MCP/tool integration
- instructions pasted into assistant/project surfaces
- agents querying Honcho actively for context

That is a coherent tool-first model.

Syke should take a broader distribution approach:

- passive harness-native context injection
- skill/instruction onboarding
- CLI capability surface
- optional MCP where tool-mediated access is the right fit

Put differently:

- Honcho is primarily an external memory/tool service that agents call
- Syke wants to be both the canonical local memory substrate and the distributor of derived context into many harness-native surfaces

Syke should still support MCP, but MCP should be one capability layer, not the entire distribution strategy.

---

## Install Surfaces

Current local surfaces:

- tool installs such as `uv tool install` and `pipx install`
- source-development checkouts with a repo-local `.venv`

Target packaging surfaces:

- app-bundled installs such as a DMG/macOS app
- managed shared runtimes for packaged CLI or headless installs

These are delivery surfaces, not different runtime backends. Syke is Pi-native in every case.

### Current: Tool Install Surface

For local non-dev usage today.

Characteristics:

- Python is owned by the installer (`uv tool`, `pipx`, later Homebrew)
- the stable launcher lives at `~/.syke/bin/syke`
- background execution must resolve through absolute paths, not shell init
- Pi is the only runtime path

This is the current non-dev shape that local users should expect.

### Target: App-Bundled Surface

For DMG and desktop-style product distribution.

Target characteristics:

- ships a self-contained app/runtime payload
- includes Node and Pi inside the app payload
- background execution uses app-owned absolute paths
- no dependency on `PATH`, `nvm`, `npm`, or repo checkout

This remains the intended "download Syke, move to Applications, run setup" path, but it is not the default local workflow today.

### Target: Managed Shared Runtime Surface

For packaged CLI installs where Syke owns the runtime payload directly.

Target characteristics:

- Syke downloads or unpacks a platform-specific runtime bundle for Node + Pi into a Syke-managed location
- daemon and Pi launches use absolute paths into that managed runtime bundle
- packaged/headless installs keep the same Pi-native runtime contract as local tool installs

This is still a target packaging direction, not the current default for local development.

### 3. Source-Dev Profile

For contributors and local development.

Characteristics:

- `uv sync --extra dev --locked`
- repo-local `.venv`
- local iteration and debugging
- developer tooling may still use local Node during development

This profile may remain less strict, but it should still be able to opt into the managed runtime bundle so development and product behavior stay close.

Source-dev is the exception path, not the architecture.

---

## The Right Pi Launch Model

Syke should stop treating Pi as "a binary path" and start treating it as "a launch command assembled by a runtime locator."

The current shape is conceptually:

```text
resolve_pi_binary() -> /path/to/pi
subprocess.Popen([pi_bin, "--mode", "rpc", ...])
```

The product-safe shape should be:

```text
resolve_runtime() -> {
  python_executable,
  node_executable,
  pi_cli_js,
  syke_entry,
  mode,
}

build_pi_command(runtime) -> [
  runtime.node_executable,
  runtime.pi_cli_js,
  "--mode", "rpc", ...
]
```

This removes the shebang/PATH problem entirely.

It also lets Syke support:

- bundled app runtimes
- managed downloaded runtimes
- source-dev fallbacks

without changing the Pi call sites.

---

## Runtime Locator

Syke should add one explicit runtime locator layer.

Suggested responsibilities:

- detect install profile
- resolve stable executable paths
- expose a structured runtime descriptor
- validate required artifacts
- report whether the runtime is relocatable, bundled, or external

Suggested outputs:

- `mode`: `app_bundle | managed_runtime | source_dev | external_runtime`
- `syke_executable`
- `python_executable`
- `node_executable`
- `pi_cli_js`
- `runtime_root`
- `state_root`
- `is_self_contained`

Every background path, health check, and Pi launch should use this layer instead of ad hoc `PATH` discovery.

---

## Managed Runtime Bundle

For non-app installs, Syke should own a platform-specific runtime bundle.

Suggested structure:

```text
~/.syke/runtime/
  current -> 0.5.0-macos-aarch64/
  0.5.0-macos-aarch64/
    bin/
      node
      syke-runtime-check
    pi/
      node_modules/@mariozechner/pi-coding-agent/dist/cli.js
    manifest.json
```

The bundle should be:

- versioned
- architecture-specific
- replaceable atomically
- independent from repo checkout and shell setup

Possible delivery mechanisms:

- bundled inside DMG/app
- downloaded from GitHub Releases on first setup
- downloaded by a shell installer for headless installs
- installed by Homebrew as a resource later

The exact transport can vary. The runtime contract should not.

---

## Daemon Strategy

There are two good daemon stories, depending on install surface.

### App Distribution On macOS

Prefer the modern Apple app-managed service path for a desktop app distribution.

That means:

- app bundle owns the helper/runtime
- background registration is managed as part of the app installation story
- Syke does not depend on a shell environment

### CLI / Source / Headless Installs

Use per-user background registration with absolute paths.

On macOS:

- `launchd` LaunchAgent is still appropriate
- `ProgramArguments` must be absolute
- environment must be explicit and minimal
- no dependency on shell init files

On Linux later:

- user-level `systemd --user` service should be the first-class path

On generic headless environments:

- a shell bootstrap can fall back to cron only as the lowest-common-denominator option

The important point is that "background execution" is a platform adapter, not a packaging strategy.

---

## Absolute Path Rule

For any non-dev daemon path:

- never rely on `PATH` to find `python`
- never rely on `PATH` to find `node`
- never rely on `PATH` to find `pi`
- never rely on shell init (`.zshrc`, `.bashrc`, `nvm`, `asdf`, etc.)

Every daemon config should point to explicit app-owned or Syke-managed paths.

This is the minimum bar for DMG, tool installs, and SSH bootstrap installs.

---

## Install Surface Recommendations

### DMG

Goal:

- double-click install experience
- self-contained runtime
- background helper support
- future signing/notarization path

Recommendation:

- package Syke as a native macOS app bundle
- embed Python + Node + Pi in the bundle or in an app-owned support payload
- register background work through the app-managed path
- treat this as the premium, least-fragile distribution surface

### `uv tool` / `pipx`

Goal:

- open-source friendly
- terminal-first
- easy upgrades

Recommendation:

- let `uv`/`pipx` provide the Python entrypoint
- let Syke setup install a managed Node + Pi runtime bundle under `~/.syke/runtime/...`
- daemon uses absolute paths into that bundle

This avoids requiring a separate Node installation for normal users.

### Source Install

Goal:

- contributor workflow
- fast local iteration

Recommendation:

- keep `uv sync --extra dev --locked`
- make source-dev able to use either:
  - local dev runtime
  - or the managed runtime bundle

This prevents "works in repo, fails in product" drift.

### SSH / Headless Install

Goal:

- one-command bootstrap on a remote machine
- no GUI assumptions

Recommendation:

- ship a shell/bootstrap installer that:
  - installs the Python package
  - installs or downloads the managed Node + Pi runtime bundle
  - writes user-level service files for the host OS

This should not require the user to set up `nvm` or install Node manually.

---

## Update Model

Syke should eventually support independent update channels for:

- Syke Python/app code
- managed runtime bundle
- Pi package/runtime bundle

Recommended rule:

- app-bundled installs update through app releases
- managed CLI installs can update the runtime bundle during `syke setup`, `syke doctor --fix`, or `syke self-update`
- runtime updates should be atomic and rollback-safe

Do not make runtime updates mutate active files in place without a versioned staging directory.

---

## Immediate Product Work

Before full DMG packaging, Syke should do these things first.

### Phase 1: Runtime Locator

Add a runtime locator abstraction and route Pi startup through it.

Deliverables:

- replace `resolve_pi_binary()` with a structured runtime descriptor
- support explicit `node_executable + pi_cli_js`
- support external runtime fallback for source/dev surfaces

### Phase 2: Managed Runtime Bundle

Introduce a Syke-managed runtime bundle under `~/.syke/runtime/...`.

Deliverables:

- install/download runtime bundle
- validate checksum/version
- wire Pi startup and daemon startup to bundled absolute paths

### Phase 3: Daemon Hardening

Make daemon registration runtime-aware.

Deliverables:

- generate absolute-path daemon registrations
- detect stale launch agents and reinstall cleanly
- add doctor checks for "can Pi start under launchd-like env"

### Phase 4: App Bundle / DMG

Wrap the self-contained runtime into a macOS app distribution.

Deliverables:

- app bundle layout
- background helper registration path
- codesign/notarization pipeline later

---

## Decision Summary

The right design is:

- one product runtime contract
- multiple install surfaces
- self-contained runtime ownership outside developer mode
- no shell/PATH dependence for background execution

The wrong design is:

- "works if the user has Node"
- "works if launchd inherits the shell"
- "works if the repo is still on disk"
- "works if pipx/uv/nvm happen to line up"

If Syke is going to be a real memory product, it has to own the end-to-end runtime.

---

## References

- Apple `SMAppService`: <https://developer.apple.com/documentation/servicemanagement/smappservice>
- Apple launchd job guidance: <https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html>
- Tauri sidecars / bundled external binaries: <https://v2.tauri.app/develop/sidecar/>
- BeeWare Briefcase macOS packaging: <https://briefcase.readthedocs.io/en/stable/reference/platforms/macOS/index.html>

These references matter less as implementation templates than as pattern confirmation:

- app distributions bundle and own helper runtimes
- background services should be app-managed or explicitly registered
- external helper binaries should be shipped as sidecars/resources, not assumed to exist on the user's PATH
