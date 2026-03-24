Write `parse_line(line: str) -> dict | None` for the "$source_name" harness.

One JSONL line in, one dict out. Return None to skip non-event lines. Only import `json`. Never raise.

Target fields (None if absent):

    timestamp, session_id, parent_session_id, role, content,
    event_type ("turn" | "tool_call" | "tool_result" | "session.start"),
    model, input_tokens, output_tokens, tool_name

Sample data:

$samples
