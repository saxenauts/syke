# Memex in Use

This document picks up where `MEMEX_EVOLUTION.md` left off.

That document showed that the memex could respond to the language used to shape the architecture. This one asks the next question: what happens when the same system is exposed to real cross-harness activity over time.

The thesis is simple. Memory has to work at `n=1` first. Personal memory is measured against one person's actual work as it moves across harnesses, sessions, repos, chats, and time.

## What is being measured

Most memory benchmarks measure recall on bounded synthetic conversations.

Syke is under a different pressure.

The pressures here are:

- how much work it takes to reconstruct context
- whether decisions carry across harnesses
- whether current intent stays coherent as priorities change
- whether native memory and Syke can work together
- whether the memory layer gets better at routing through repeated use
- what it costs to keep the loop alive in the background

Those are the measurements that matter here.

## The live corpus

As of April 1, 2026, the local instance used for this report held:

- **454,741 observed events**
- **973 memories**
- **27 active memories**
- **425 links**
- **591 completed synthesis cycles**
- **5,955 metrics rows**

Largest active harnesses in that instance:

- **Claude Code**: 157,303 events
- **OpenCode**: 142,923
- **Codex**: 136,930
- **Hermes**: 2,392

That is real volume. The system is carrying real work.

## What changes when Syke is in the loop

### 1. The same repo produces a different reading

On March 20, 2026, a manual evaluation note compared native Claude Code with Claude Code plus Syke on the same repo.

Without Syke, the run got the visible layer: colors, fonts, components, stack.

With Syke, the run pulled in the brand system, internal principles, and the strategic layer behind the same files.

The key line from that note remains the cleanest summary of the difference:

> The difference wasn't tool access. Both had the same filesystem. The difference was context routing.

That is the point in one sentence. The files were already there. The hard part was knowing which ones mattered first.

### 2. One ask replaced five to ten reconstruction calls

In another real note, one `syke ask` call pulled together ChatGPT conversations, GitHub activity, and Claude Code sessions in one answer.

The same note says that answer would have taken **5 to 10 tool calls** to reconstruct manually.

That is the kind of work Syke is here to collapse.

### 3. An adjacent agentic search path did much worse

A forensic comparison on March 13, 2026 looked at a practical repo question: where `syke observe` lived.

The non-Syke path needed:

- **21 tool calls**
- **3 turns**
- **2 confabulations**
- **2 user corrections**

The Syke path needed:

- **1 `syke ask` call**
- **3 internal searches**
- the right answer with the why on the first try

This is a practical retrieval and reconstruction comparison on real work.

### 4. No adapter means no continuity

A manual note from March 14, 2026 recorded the absence case clearly.

OpenCode had no Syke adapter. There was zero context injection. The agents there could not see user history.

That makes the continuity layer legible. When Syke is not connected, that bridge is absent.

### 5. Native memory plus Syke is the real comparison

The product lesson in the repo is clear. Syke should be measured next to native harness memory.

The Hermes adapter path already follows that idea:

- native memory stays untouched
- Syke is added as a supplemental layer
- the meaningful comparison is native memory alone versus native memory plus Syke

That product stance matters. Syke wins by binding together what native memory leaves fragmented.

### 6. The hard problem is temporal contradiction

One contradiction-hunting report from March 21, 2026 put the problem more sharply than any recall benchmark can.

The report says the real contradictions in personal data are overwhelmingly temporal, not simultaneous.

The hard question is often not which source is right. The hard question is which version of intent is current.

That is the coherence problem a memory system hits in real use.

## What the system is learning to do

The interesting part is how Syke changes its routes through the material.

`MEMEX_EVOLUTION.md` already showed the first strong sign of this. Under pressure, the memex stopped trying to copy everything inline and started pointing to where the story lived. Pointer structures appeared. Duplicate content dropped. The map got smaller while coverage grew.

That was the first sign that memory management itself could improve through use.

The practical question since then has been whether that routing behavior survives contact with real work.

So far, the answer is yes.

The system keeps the raw timeline separate from learned memory. It revises memories when evidence sharpens an existing conclusion. It keeps the memex as a current map instead of letting it collapse into a giant archive. It uses repeated use to move toward better routes through one person's own history.

That is the part that still feels distinct.

## Cost, time, and cadence

### Last 30 days

The local instance recorded:

- **142 ask runs**
- **314 synthesis runs**
- **1,271,951 ask tokens**
- **348,017 synthesis tokens**
- **$1.1219 ask cost**
- **$0.5127 synthesis cost**

Average over that period:

- ask: about **$0.0079 per run**
- synthesis: about **$0.0016 per run**

This matters because it shows where the cost lives. The background memory loop is cheap enough to keep alive. The deeper query path is where the user gets the value and where most spend lands.

### Runtime behavior

Ask telemetry from live events shows:

- **117 daemon IPC asks**
- **18 direct asks**
- **16 IPC fallbacks**
- ask p50 around **26.8s**

Daemon cycle telemetry shows:

- **32 recorded cycle completes**
- cycle p50 around **49.4s**
- cycle p95 around **64.8s**
- `memex_updated=true` in **32 / 32** recorded cycle completes

The loop is real and active.

### One controlled comparison that still matters

On February 26, 2026, the same question against the same codebase and the same minute went from:

- **51 user-facing calls** to **2**
- **3 subagents** to **0**
- about **970K tokens** to **431K**

That is one measured example. It shows the scale of reconstruction overhead continuity can remove.

## What this proves today

This evidence supports these claims:

- Syke is useful as a local-first continuity layer across supported harnesses.
- Syke can reduce real reconstruction work in practical workflows.
- Syke is strongest when it complements harness-native memory rather than replacing it.
- The memory layer can improve its routing through repeated use.
- The current system is live enough to judge as an operating memory loop.

## Why `n=1` matters

`n=1` is the whole point.

A personal memory system has to work against one person's actual work, one person's drift, one person's contradictions, one person's way of moving across tools, and one person's changing priorities.

The next step is to gather more real users and compare multiple `n=1` worlds to see which patterns hold, which diverge, and which temporal attractors keep appearing.

That is the measurement program this problem calls for.

## What this opens

The larger research question is now concrete enough to pursue in the open.

The next stage is to compare more real users, more harness mixes, and more long-running memory traces without flattening them into one benchmark frame.

## Where to go next

If you want the current product surface, start with:

- `README.md`
- `docs/SETUP.md`
- `docs/PROVIDERS.md`

If you want the deeper system contract and history, continue with:

- `docs/CURRENT_STATE.md`
- `docs/ARCHITECTURE.md`
- `docs/RUNTIME_AND_REPLAY.md`
- `docs/MEMEX_EVOLUTION.md`
