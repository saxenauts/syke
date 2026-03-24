Write a Python function `parse_line(line: str) -> dict | None` for the "$source_name" AI harness.

## What this function does

It receives one raw line from a data file and returns a dict with canonical fields, or None to skip.

## Canonical fields (use None if not available)

- timestamp: ISO 8601 string
- session_id: string grouping turns in one conversation
- parent_session_id: links sub-agent sessions to parent
- event_type: "turn" | "tool_call" | "tool_result" | "session.start"
- role: "user" | "assistant" | "system"
- content: text content of the turn
- tool_name: tool name if event_type is tool_call/tool_result
- model: model name/id
- input_tokens: int
- output_tokens: int

## Known harness formats

- **claude-code**: JSONL at ~/.claude/projects/*/.jsonl, ~/.claude/transcripts/*.jsonl. Fields: type(user|assistant|tool_use), sessionId, timestamp, message.content[].text. parentSessionId for sub-agents. message.model, message.usage for tokens.
- **codex**: JSONL at ~/.codex/sessions/rollout-*.jsonl. First line: session_meta with cwd. Turns: response_item, payload.type=message, payload.role, payload.content[].type(input_text|output_text).text. Tools: payload.type=function_call.
- **opencode**: SQLite at ~/.local/share/opencode/opencode.db. Tables: session(id, title, time_created ms), message(session_id, data JSON with role+time), part(message_id, data JSON with type=text|tool).
- **hermes**: SQLite at ~/.hermes/state.db. Messages/turns with role, content, tool_calls. Also ~/.hermes/sessions/ for JSONL logs.
- **cursor**: JSON at ~/.cursor/. Workspace conversations with role+content turns and tool calls.
- **windsurf**: JSONL/JSON at ~/.windsurf/ or ~/.codeium/windsurf/. Workspace conversations similar to Cursor.
- **aider**: JSONL at ~/.aider*/. Chat history with role(user|assistant), content, file edit blocks.
- **zed**: JSON at ~/.zed/ or ~/.config/zed/. Conversations with assistant messages and tool use.
- **pi**: JSONL/JSON at ~/.pi/. Session files with role-based turns. Format varies by version.
- **gemini**: JSON at ~/.gemini/. Google AI Studio exports with parts[].text, role(user|model).

## Sample data from this harness

$samples

## Quality contract

Your parse_line() will be tested against the samples above. To pass:

- session_id must be present on every event (derive from sessionId, session_id, or filename context)
- event_type must be one of: "turn", "tool_call", "tool_result", "session.start"
- role must be present on turn events ("user", "assistant", "system")
- content must contain the actual text, not a JSON dump of the whole line
- model and token counts should be extracted when the data has them (assistant turns typically do)
- tool_name must be present on tool_call and tool_result events

If a field genuinely doesn't exist in the source data, return None.
But if the data has it under a different name, you must map it.

## Rules

- Only import `json` from stdlib. No other imports.
- Handle malformed lines: return None, never raise.
- Map harness-specific field names to the canonical names above.
- If you recognize the harness from the hints above, use that knowledge.
- If you don't recognize it, infer the structure from the samples.
