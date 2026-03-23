"""Named constants for the observe layer — canonical event taxonomy."""

MAX_TITLE_CHARS = 120
CHARS_PER_TOKEN_ESTIMATE = 4

# Event types
EVENT_TYPE_SESSION_START = "session.start"
EVENT_TYPE_TURN = "turn"
EVENT_TYPE_TOOL_CALL = "tool_call"
EVENT_TYPE_TOOL_RESULT = "tool_result"
EVENT_TYPE_INGEST_ERROR = "ingest.error"
