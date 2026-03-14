"""Named constants for ingestion - replaces all inline magic numbers."""

# Content limits
LEGACY_CONTENT_PREVIEW_CHARS = 800
MAX_LEGACY_CONTENT_CHARS = 50_000
MIN_CONTENT_CHARS = 50

# Titles
MAX_TITLE_CHARS = 120
MIN_TITLE_REMAINDER_CHARS = 20

# Token estimation (rough chars-to-token ratio for measurement logging)
CHARS_PER_TOKEN_ESTIMATE = 4

# Event types (Observe taxonomy)
EVENT_TYPE_SESSION_START = "session.start"
EVENT_TYPE_TURN = "turn"
EVENT_TYPE_LEGACY_SESSION = "session"
EVENT_TYPE_INGEST_ERROR = "ingest.error"

# Turn roles
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

# Greeting prefixes stripped from titles
GREETING_PREFIXES = [
    "hey, ",
    "hi, ",
    "hello, ",
    "hey ",
    "hi ",
    "hello ",
    "can you please ",
    "could you please ",
    "can you ",
    "could you ",
    "i would like to ",
    "i'd like to ",
    "i want to ",
    "i need to ",
    "please ",
]

# Agent scaffolding headers stripped from content
SCAFFOLDING_HEADERS = [
    "## Notepad Location",
    "## Plan Location (READ ONLY)",
    "## CERTAINTY PROTOCOL",
    "## DECISION FRAMEWORK",
    "## AVAILABLE RESOURCES",
    "## **ABSOLUTE CERTAINTY",
    "## **NO EXCUSES",
]
