# Observe Architecture — The Bidirectional Loop

## The System

```
┌─────────────────────────────────────────────────────────────────────┐
│                        THE EXTERNAL WORLD                           │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │  Claude   │  │  Codex   │  │ OpenCode │  │    Pi    │  ...      │
│  │   Code    │  │          │  │          │  │          │           │
│  └────┬──▲──┘  └────┬──▲──┘  └────┬──▲──┘  └────┬──▲──┘           │
│       │  │          │  │          │  │          │  │               │
│       │  │          │  │          │  │          │  │               │
│       │  │SKILL.md  │  │SKILL.md  │  │SKILL.md  │  │SKILL.md      │
│       │  │injected  │  │injected  │  │injected  │  │injected      │
└───────┼──┼──────────┼──┼──────────┼──┼──────────┼──┼───────────────┘
        │  │          │  │          │  │          │  │
        │  │          │  │          │  │          │  │
   ═════╪══╪══════════╪══╪══════════╪══╪══════════╪══╪═══════════════
        │  │          │  │          │  │          │  │
        ▼  │          ▼  │          ▼  │          ▼  │
┌──────────┴──────────────┴──────────────┴──────────────┴─────────────┐
│                                                                     │
│    OBSERVE (deterministic)              DISTRIBUTE (deterministic)   │
│                                                                     │
│    Adapters compile                     Skills inject context        │
│    harness data ──►                     ◄── back into harnesses     │
│    canonical events                     from the memex              │
│                                                                     │
│  ┌─────────────────┐                 ┌────────────────────┐         │
│  │  TOML Descriptor │                 │  syke context      │         │
│  │  ──► Adapter     │                 │  syke ask           │         │
│  │  ──► Events      │                 │  SKILL.md injection │         │
│  └────────┬────────┘                 └──────▲─────────────┘         │
│           │                                  │                       │
│           ▼                                  │                       │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │                    EVENT STORE                           │        │
│  │           (SQLite, typed columns, append-only)           │        │
│  │                                                         │        │
│  │  session.start │ turn │ tool_call │ tool_result │ ...   │        │
│  └────────────────────────┬────────────────────────────────┘        │
│                           │                                         │
│                           ▼                                         │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │                    SYNTHESIS (Map)                       │        │
│  │           Events ──► Memex (the learned representation)  │        │
│  │           Patterns, preferences, context, history        │        │
│  └─────────────────────────┬───────────────────────────────┘        │
│                             │                                       │
│                             ▼                                       │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │                      MEMEX                               │        │
│  │           The complete picture of the user ──────────────┼───►   │
│  │           Distributed to all harnesses via skills        │  (back │
│  └─────────────────────────────────────────────────────────┘  up)   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

   ═══════════════════════════════════════════════════════════════════

┌─────────────────────────────────────────────────────────────────────┐
│                    CONTROL PLANE                                    │
│                    (stable infrastructure, not learned)              │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │                 ADAPTER DROID (skill)                    │        │
│  │                                                         │        │
│  │  The system's senses for the external world.            │        │
│  │  An agent loads this skill and gains the ability to:    │        │
│  │                                                         │        │
│  │  1. SENSE  — health check all harnesses                 │        │
│  │  2. CREATE — generate adapter from protocol + data      │        │
│  │  3. HEAL   — fix adapter when harness format changes    │        │
│  │  4. VERIFY — validate external_id stability             │        │
│  │                                                         │        │
│  │  Inputs:  harness data on disk + ADAPTER-PROTOCOL.md    │        │
│  │  Outputs: TOML descriptor + adapter code + tests        │        │
│  │                                                         │        │
│  │  NOT a daemon. NOT a program.                           │        │
│  │  A skill that agents execute when needed.               │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │              ADAPTER PROTOCOL (contract)                 │        │
│  │                                                         │        │
│  │  Descriptor schema │ Parser registry │ external_id law  │        │
│  │  Format clusters   │ Health checks   │ Generation tree  │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │              HARNESS REGISTRY (state)                    │        │
│  │                                                         │        │
│  │  20 descriptors │ health status │ adapter factory        │        │
│  │  Deterministic   │ no LLM       │ runtime lookup         │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## The Bidirectional Relationship

```
HARNESS ──(session data)──► ADAPTER ──► EVENTS ──► SYNTHESIS ──► MEMEX
                                                                   │
HARNESS ◄──(SKILL.md)───── DISTRIBUTE ◄──(context)──────────────────┘
```

Every harness has TWO connections to Syke:

**Inbound (Observe)**: Adapter reads harness data, compiles to canonical events.
Deterministic. No LLM. Follows the 7 Observe Principles.

**Outbound (Distribute)**: Syke skill injected into harness context.
The memex — synthesized from ALL harness events — flows back.
Each harness benefits from observations across ALL harnesses.

## The Droid Is Not a Program

The Adapter Droid is a skill. It lives in the control plane because:

**Control plane** (stable, doesn't learn): protocol, descriptors, health checks, registry.
**Learned plane** (evolves): memex, memories, links, patterns.

The Droid maintains the control plane. It doesn't evolve — it follows a fixed protocol.
But it IS executed by agents (which have LLM capability) at build time, not runtime.

**When an agent loads the Droid skill and sees:**
```
Pi: JSONL at ~/.pi/agent/sessions/, 1 session, NO ADAPTER
```
**It follows the protocol:**
1. Read the JSONL → understand the format
2. Check: is this expressible as a TOML descriptor? (Pi is nearly identical to Claude Code JSONL)
3. Write the descriptor
4. If needed, generate adapter code
5. Validate external_id stability
6. Register in the harness registry

**No human writes the adapter. The agent does.**
The protocol + droid skill + harness data = sufficient for generation.

## What This Means for the 7 Installed Harnesses

```
Claude Code  ──► Observe adapter ──► Events ──► Memex ──► SKILL.md back to CC
     ✅ complete bidirectional loop

Codex        ──► Observe adapter ──► Events ──► Memex ──► (no skill installed yet)
     ⚠️ inbound partial (format bug), outbound not connected

OpenCode     ──► (no adapter) ──► ??? 
Pi           ──► (no adapter) ──► ???
Hermes       ──► (no adapter) ──► ???
Cursor       ──► (no adapter) ──► ???
Gemini CLI   ──► (no adapter) ──► ???
     ❌ data exists on disk, no inbound connection

ALL of them  ◄── (Droid skill can generate adapters from protocol + data on disk)
     🔧 the mechanism exists, hasn't been exercised yet
```

## The Completion Question

The architecture is a closed loop. The mechanism for creating adapters exists
(Droid skill + Protocol + Registry). What hasn't happened yet is the first
exercise of that mechanism — an agent loading the Droid skill, seeing the
gaps, and generating the adapters for the 5 blind harnesses.

That IS the test of whether this architecture works.
