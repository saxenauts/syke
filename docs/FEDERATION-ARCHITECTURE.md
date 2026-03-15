# Federation Architecture — Observe Layer

> How Syke captures data from 10,000+ harnesses through a single canonical schema.

---

## The Federation Thesis

Syke wins as unified memory because it observes all harnesses neutrally. Each harness has its own compaction strategy, context management, and epistemic assumptions. Syke doesn't take sides — it captures raw data from all of them, and the Map/Ask layer learns the patterns.

**Neutral observation enables federation only when paired with strong provenance.**

---

## Anti-Pattern: Memorix's Shared Directory Federation

Memorix (AVIDS2/memorix) uses a shared-directory model: all agents write to `~/.memorix/data/<projectId>/`. This is NOT federation — it's centralized storage requiring agent cooperation. If an agent doesn't write to the right directory, its data is invisible.

**Why this fails at scale:**
- Requires every harness to cooperate with a specific storage convention
- No provenance — can't tell which agent wrote what
- No conflict resolution — two agents writing the same key overwrites silently
- Doesn't work for cloud-based harnesses (Amp, remote Cursor)

---

## Syke's Federation Model: Adapters as Compilers

```
HARNESS (10,000+)          ADAPTER (compiler)           CANONICAL IR (1 table)
──────────────────         ──────────────────           ──────────────────────
Claude Code JSONL    →     ClaudeCodeAdapter     →
Codex JSONL          →     CodexAdapter          →
Gemini CLI JSON      →     GeminiAdapter         →     events (
Cursor SQLite        →     CursorAdapter         →       id, timestamp, source,
Windsurf SQLite      →     WindsurfAdapter       →       event_type, content,
Cline Multi-file     →     ClineAdapter          →       role, model, tokens,
Roo Code Multi-file  →     RooAdapter            →       tool_name, session_id,
Aider Markdown       →     AiderAdapter          →       sequence_index, ...
Continue.dev JSON    →     ContinueAdapter       →     )
OpenCode SQLite      →     OpenCodeAdapter       →
Pi JSONL             →     PiAdapter             →
Amp Cloud API        →     AmpAdapter            →
[10,000 more]        →     [generated adapters]  →
```

Each adapter compiles harness-native format into the same typed columns. The schema doesn't change per harness. The adapter code does.

---

## Format Clusters (6 types cover all harnesses)

| Cluster | Format | Harnesses | Parser Strategy |
|---|---|---|---|
| JSONL | Line-delimited JSON | Claude Code, Codex, Pi | `read_jsonl()` → line-by-line |
| JSON | Single file per session | Gemini CLI, Continue.dev | `json.load()` → message array |
| SQLite | Database tables | OpenCode, Cursor, Windsurf | SQL query → row extraction |
| Multi-file | Directory per task | Cline, Roo Code | Directory walk → merge files |
| Markdown | Freeform text | Aider | Regex split → block parsing |
| Cloud API | HTTP/REST | Amp, remote agents | API call → response parsing |

New harnesses fit one of these clusters. A new JSONL-based harness reuses the `read_jsonl()` parser. The adapter-specific logic is: where are the files, what fields to extract.

---

## Deployment Types (3 cover all harnesses)

| Type | How Syke Captures | Examples |
|---|---|---|
| Local CLI | File watching + hook listener | Claude Code, Codex, Aider, Gemini CLI |
| IDE Extension | Extension storage path + file watching | Cursor, Cline, Roo, Continue.dev, Windsurf |
| Cloud API | API polling or webhook | Amp, remote agents, CI/CD pipelines |

---

## Adapter Code Generation

For 10,000 harnesses, humans don't write every adapter. Agents do.

**Input**: Harness documentation (API docs, file format spec, example sessions)
**Output**: Adapter code that compiles to the canonical schema

```
Agent reads harness docs
  → Identifies format cluster (JSONL? SQLite? API?)
  → Generates adapter: discover() + iter_sessions()
  → Generates test fixtures from example sessions
  → Generates validation: does output match canonical schema?
```

**The adapter contract (ObserveAdapter ABC):**
- `discover() → list[Path]` — find harness data on disk
- `iter_sessions(since) → Iterable[ObservedSession]` — yield sessions with turns

This contract is simple enough for agents to implement reliably from docs.

---

## Real-Time Federation (4 Tiers)

```
TIER 1: Hook-based (<5ms)
  Claude Code HTTP hooks → POST to localhost:7749
  Captures: PostToolUse, Stop, SessionStart, SubagentStart/Stop
  Coverage: Claude Code (and future hook-enabled harnesses)

TIER 2: File watch (10-50ms)
  watchdog monitors harness directories
  Triggers: FileModified → incremental tail-read
  Coverage: All local CLI + IDE extension harnesses

TIER 3: MCP retrieval (<150ms)
  Syke as MCP server: ask, context, record tools
  Agent reads own history during session
  Coverage: Any harness supporting MCP

TIER 4: Daemon batch (15 min)
  Safety net + synthesis trigger
  Coverage: Everything, including cloud API polling
```

---

## Cross-Harness Correlation

**Problem**: User works in Claude Code for 2 hours, switches to Cursor for 30 minutes, then back to Claude Code. Three different harnesses, one continuous work session.

**Observe's role**: Capture events from all three with timestamps and source. Do NOT try to link them at Observe time.

**Map's role**: Discover that events from claude-code (session A), cursor (session B), and claude-code (session C) all touch the same git repository, same branch, overlapping time window → group as one "work session" retrospectively.

**What Observe provides for this:**
- `timestamp` — temporal alignment
- `source` — which harness
- `extras.git_branch`, `extras.cwd` — shared project context
- `session_id` — per-harness session grouping

Map uses these signals to build cross-harness session graphs. Observe just captures them.

---

## Epistemic Conflicts

**Problem**: Claude Code says the auth module uses JWT. Codex says it uses OAuth2. Both captured by Observe.

**Observe's role**: Store both claims with origin, time, and context. Never choose a winner.

**Map's role**: Detect the conflict. Surface both to the user. Let the user or a future resolution agent decide.

**This is a feature, not a bug.** Cross-harness disagreements are data about how different agents interpret the same codebase. For RLM/self-evolution research, these conflicts are training signal.

---

## Federation Invariants

1. **One schema, many adapters.** The events table is the IR. Adapters compile into it.
2. **Provenance on every event.** Source, source_path, source_event_type, adapter version.
3. **Time is the only correlation constant.** Cross-harness linking uses timestamps, not shared IDs.
4. **Observe doesn't link. Map links.** Session grouping across harnesses is a Map concern.
5. **Conflicts are data.** Store both sides. Never resolve at capture time.
6. **Adapters are code. Schema is stable.** New harness = new adapter. Schema changes only for new cross-harness primitives.

---

## Research Substrate

The federated event stream serves:

| Framework | What Federation Enables |
|---|---|
| RLM | Self-referential learning across harnesses (agent reads its own history from Claude Code + Codex) |
| GEPA | Execution trace comparison: same task, different harnesses → which performed better? |
| Context Engineering | Token budget analysis across providers (Anthropic cache vs OpenAI cache vs Google context) |
| SHARP | Failure pattern detection across harnesses — does the same bug cause failures everywhere? |
| Reflexion | Cross-harness self-evaluation — did switching tools improve outcomes? |

---

## What's Built vs What's Next

**Built (observe-phase2):**
- Canonical schema with typed columns
- ClaudeCodeAdapter as reference compiler
- Universal parsers (JSONL, content extraction)
- 7 Observe Principles enforced

**Next (observe-phase3):**
- Hook listener for real-time Claude Code capture
- File watcher for other harnesses
- Transport-agnostic dedup

**Future:**
- Agent-generated adapters for new harnesses
- Cross-harness correlation in Map layer
- Epistemic conflict detection
- Token cost comparison across providers
