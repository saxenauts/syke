# ChatGPT Export Extraction Strategy

## Source
ChatGPT data export (ZIP file from Settings → Data Controls → Export)

## File Structure
```
export.zip
├── conversations.json      # All conversations
├── user.json               # Account info
├── message_feedback.json   # Thumbs up/down
├── model_comparisons.json  # A/B test results
├── chat.html               # Browsable HTML version
└── shared_conversations.json
```

## Extraction Approach
1. Unzip the export
2. Parse `conversations.json` — array of conversation objects
3. Each conversation has `mapping` — a tree of message nodes
4. Walk the mapping to reconstruct message order
5. Extract user messages and assistant responses
6. Build conversation content as role-tagged dialogue

## Event Mapping
- `conversation` — one event per conversation

## Key Fields
- **timestamp**: `create_time` (Unix epoch → ISO 8601)
- **title**: conversation title (may be auto-generated)
- **content**: Full dialogue with [role]: prefix
- **metadata**: conversation_id, message_count, model slug, update_time

## Chunking
- Conversations can be very long (100K+ chars)
- Truncate at 50K chars with `[...truncated]` marker
- Message count preserved in metadata for awareness

## Known Issues
- Some messages have null content (system messages, tool calls)
- `parts` may contain non-string items (images, files) — filter to strings only
- `create_time` can be null for old conversations — use `update_time` fallback
- Model slug may be empty for very old conversations
