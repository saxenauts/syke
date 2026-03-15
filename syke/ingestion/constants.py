"""Named constants for ingestion — the canonical event IR."""

# Titles
MAX_TITLE_CHARS = 120

# Token estimation (rough chars-to-token ratio for measurement logging)
CHARS_PER_TOKEN_ESTIMATE = 4

# Event types (Observe taxonomy)
EVENT_TYPE_SESSION_START = "session.start"
EVENT_TYPE_TURN = "turn"
EVENT_TYPE_TOOL_CALL = "tool_call"
EVENT_TYPE_TOOL_RESULT = "tool_result"
EVENT_TYPE_INGEST_ERROR = "ingest.error"

# Turn roles
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
