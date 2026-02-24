# Syke — Agent Skill

Syke is agentic memory for AI. It collects a user's digital footprint (Claude Code, ChatGPT, GitHub, Gmail), uses Agent SDK to synthesize a living memory of who they are, and feeds that understanding back to you via MCP. After setup, you know who you're talking to.

## Setup

Any agent on any platform can self-install Syke:

1. **Clone and install**
   ```bash
   git clone <repo-url> && cd syke
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e .
   ```

2. **Configure API key**
   ```bash
   cp .env.example .env
   # Set ANTHROPIC_API_KEY in .env
   ```

3. **Detect available sources**
   ```bash
   python -m syke detect
   ```

4. **Run full pipeline** (detect, collect, build profile, output)
   ```bash
   python -m syke --user <name> setup --yes
   ```

5. **Register Syke on your platform**
   Check `strategies/dist/` for your platform's strategy file. If none exists, figure out how your platform registers MCP servers or tool sources, register Syke, then write a new strategy file in `strategies/dist/<platform>.md` and commit it.

6. **Validate setup**
   ```bash
   python -m syke --user <name> validate
   ```

## Ongoing Responsibilities

Every Syke-aware agent should:

- **Read context**: Call `get_live_context()` when you need to understand who you're talking to. Don't guess — read the profile.
- **Ask questions**: Call `ask()` to explore the user's timeline in natural language — "What did they work on last week?" or "What's their stance on testing?"
- **Push signals**: Call `record()` when something meaningful happens in conversation — a decision made, a preference expressed, a project started. This is how the federated model works: each surface contributes data.
- **Keep sync alive**: If your platform supports scheduling or hooks, set up periodic `syke sync` to pull new data from all sources and refresh memories.
- **Self-heal**: If an adapter breaks (ingestion or distribution), read the error, check or update the relevant strategy file in `strategies/`, and commit the fix. Strategy files are living documents maintained by agents.

## Available MCP Tools

| Tool | Direction | Purpose |
|------|-----------|---------|
| `get_live_context(format)` | Read | Identity profile (json, markdown, claude-md, user-md) |
| `ask(question)` | Read | Natural language questions about the user — explores timeline agenically |
| `record(observation)` | Write | Push an observation from this session into the user's timeline |
