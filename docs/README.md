# Docs

Syke docs by audience and by topic. Product docs describe the current runtime;
the memex narrative docs are historical design evidence.

## Reading Paths

**New user.** Start with the [top-level README](../README.md), then use the
[Setup Guide](SETUP.md) for the first-run flow, timeline setup states, and
fresh setup testing. [Providers](PROVIDERS.md) covers auth when you need it.

**Agent / automation.** Use [Setup](SETUP.md#agent-mode-non-interactive) for
the JSON contract, `next_steps`, daemon skip mode, and isolated fresh-install
smoke flow. Agents should parse setup output and avoid rerunning setup after
`status=complete`.

**Operator / maintainer.** [Runtime and Replay](RUNTIME_AND_REPLAY.md) and
[Architecture](ARCHITECTURE.md) explain the runtime shape. [Current State](CURRENT_STATE.md)
is the one-page snapshot. For release operations, use [Scripts Surface](../scripts/README.md)
and run `scripts/release-candidate.sh` before any push, tag, or publish step.

**The story.** [Memex Evolution](MEMEX_EVOLUTION.md) and
[Memex Update 2](MEMEX_UPDATE_2.md) are historical design narratives. Read them
for context, not as the current product contract.

**Agent integration.** [Skill Contract](../SKILL.md) is the integration point. [Platforms](../PLATFORMS.md) lists the harnesses Syke reads from and distributes into.

## All Docs

**Product:**

- [Setup](SETUP.md)
- [Providers](PROVIDERS.md)
- [Config Reference](CONFIG_REFERENCE.md)
- [Platforms](../PLATFORMS.md)
- [Runtime and Replay](RUNTIME_AND_REPLAY.md)
- [Skill Contract](../SKILL.md)

**Internals and maintainer:**

- [Architecture](ARCHITECTURE.md) — system design
- [Current State](CURRENT_STATE.md) — implementation snapshot

**Historical narrative:**

- [Memex Evolution](MEMEX_EVOLUTION.md) — design evidence from Feb 2026
- [Memex Update 2](MEMEX_UPDATE_2.md) — architecture cleanup narrative from Apr 2026

**Ops:**

- [Scripts Surface](../scripts/README.md) — release-critical vs internal scripts
