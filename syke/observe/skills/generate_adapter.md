Write `parse_line(line: str) -> dict | None` for the "$source_name" harness.

One JSONL line in, one dict out. Return None to skip non-event lines.
Only import `json`. Never raise — catch all exceptions and return None.

Return dict with these fields (set to None if absent in this line):

    timestamp       — ISO string or epoch
    session_id      — session/conversation identifier
    parent_session_id — parent session if this is a sub-agent/fork
    role            — "user" | "assistant" | "system" | "tool"
    content         — text content (if content is an array of blocks, join text from blocks with type "text")
    event_type      — "turn" | "tool_call" | "tool_result" | "session.start"
    model           — LLM model name (often nested inside a message/response object)
    input_tokens    — input token count (often nested inside usage/message object)
    output_tokens   — output token count (often nested inside usage/message object)
    tool_name       — tool/function name for tool_call and tool_result events

Key extraction patterns to look for in the samples:
- model and usage often live in a nested object (e.g. `obj.message.model`, `obj.message.usage.input_tokens`)
- content may be a string OR an array of typed blocks — extract text from blocks
- tool events may appear as separate lines OR as blocks within a content array
- session metadata lines (session_meta, isMeta) → event_type = "session.start"
- stop_reason/finish_reason in assistant messages → include in content if interesting, but don't force

Sample data:

$samples
