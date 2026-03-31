# Syke Platform Support

## Ingestion (data into Syke)

| Platform | Strategy File | Status |
|----------|--------------|--------|
| Claude Code | descriptor + local/runtime adapter path | Active |
| Codex | descriptor + local/runtime adapter path | Active |
| GitHub | historical/docs reference | Experimental |

## Distribution (Syke into agents)

Syke currently supports only three distribution surfaces:

| Surface | Path | Status |
|---------|------|--------|
| CLI | `syke ask`, `syke context`, `syke record`, `syke doctor`, `syke setup` | Active |
| MEMEX injection | exported memex in `~/.syke/data/{user}/MEMEX.md`, referenced additively from `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` | Active |
| Skill file | installed `SKILL.md` in detected skill-capable dirs such as Claude Code, Codex, and Cursor | Active |

## Adding a Platform

Agents should update this table when they:
- Add or validate a real adapter/runtime path
- Fit a new agent into one of the three supported distribution surfaces
- Promote an experimental ingestion path to active

Updated by agents as they self-heal and add new platforms.
