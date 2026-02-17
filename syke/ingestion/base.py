"""Base adapter ABC with content filtering."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod

from syke.db import SykeDB
from syke.models import IngestionResult

logger = logging.getLogger(__name__)

# Sources that access private/personal data require explicit consent
PRIVATE_SOURCES = {"claude-code", "chatgpt", "gmail", "twitter", "youtube"}
PUBLIC_SOURCES = {"github"}

# --- Content Filter ---
# The system should proactively decide what to ignore vs keep.
# This runs BEFORE events enter the timeline.

# Patterns that indicate private messaging pasted into AI conversations
_PRIVATE_MSG_PATTERNS = [
    # WhatsApp: [10/6/25, 5:08:37 AM] Name: message
    re.compile(r'\[\d{1,2}/\d{1,2}/\d{2,4},\s+\d{1,2}:\d{2}:\d{2}\s*[AP]M\]\s+\w+'),
    # iMessage/SMS timestamp dumps
    re.compile(r'(?:iMessage|SMS)\s+\d{1,2}/\d{1,2}/\d{2,4}'),
    # Telegram export format
    re.compile(r'\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}\s+-\s+\w+'),
]

# Credential patterns to strip from content
_CREDENTIAL_PATTERNS = [
    re.compile(r'sk-ant-api\d+-[A-Za-z0-9_-]{20,}'),  # Anthropic keys
    re.compile(r'sk-[A-Za-z0-9]{20,}'),                 # OpenAI keys
    re.compile(r'ghp_[A-Za-z0-9]{36,}'),                # GitHub tokens
    re.compile(r'gho_[A-Za-z0-9]{36,}'),                # GitHub OAuth
    re.compile(r'xoxb-[A-Za-z0-9-]+'),                  # Slack tokens
    re.compile(r'Bearer\s+[A-Za-z0-9._-]{20,}'),        # Bearer tokens
    re.compile(r'password\s*[=:]\s*["\']?[^\s"\']{8,}', re.IGNORECASE),
    re.compile(r'AKIA[0-9A-Z]{16}'),                     # AWS access keys
    re.compile(r'-----BEGIN\s[\w\s]*PRIVATE KEY-----'),   # SSH/PEM private keys
    re.compile(r'\w+://\w+:[^@\s]{3,}@[\w.-]+'),         # DB connection strings with passwords
]


class ContentFilter:
    """Pre-ingestion content filter.

    Decides what to keep, sanitize, or skip before events enter the timeline.
    Logs all filter decisions for auditability.

    Policy:
    - SKIP: Events dominated by private messaging between individuals
      (WhatsApp chats, iMessage logs, DMs pasted into AI conversations)
    - SANITIZE: Strip credentials, API keys, tokens from all content
    - KEEP: Everything else — professional, technical, creative, health,
      financial strategy, research, personal reflection
    """

    def __init__(self):
        self.stats = {"kept": 0, "skipped": 0, "sanitized": 0}

    def should_skip(self, content: str, title: str = "") -> tuple[bool, str]:
        """Check if an event should be skipped entirely.

        Returns (should_skip, reason).
        """
        if not content:
            return True, "empty content"

        # Check for private messaging content
        # Two thresholds: high ratio of lines, OR absolute count of private messages
        lines = content.split("\n")
        if len(lines) > 5:
            msg_lines = sum(
                1 for line in lines
                if any(p.search(line) for p in _PRIVATE_MSG_PATTERNS)
            )
            ratio = msg_lines / len(lines)
            # Skip if >20% of lines are private messages
            if ratio > 0.2:
                self.stats["skipped"] += 1
                return True, f"private messaging content ({ratio:.0%} of lines match)"
            # Also skip if >10 private message lines regardless of ratio
            # (catches large events with embedded chat logs)
            if msg_lines > 10:
                self.stats["skipped"] += 1
                return True, f"embedded private messages ({msg_lines} lines detected)"

        return False, ""

    def sanitize(self, content: str) -> str:
        """Remove credentials and sensitive patterns from content."""
        sanitized = content
        changed = False
        for pattern in _CREDENTIAL_PATTERNS:
            new = pattern.sub("[REDACTED]", sanitized)
            if new != sanitized:
                changed = True
                sanitized = new
        if changed:
            self.stats["sanitized"] += 1
        self.stats["kept"] += 1
        return sanitized

    def process(self, content: str, title: str = "") -> tuple[str | None, str]:
        """Full filter pipeline: skip check → sanitize → return.

        Returns (filtered_content_or_None, reason).
        None means the event should be skipped.
        """
        skip, reason = self.should_skip(content, title)
        if skip:
            logger.debug(f"Content filter SKIP: {reason} | title={title[:50]}")
            return None, reason
        return self.sanitize(content), "kept"


class BaseAdapter(ABC):
    """Abstract base class for platform adapters."""

    source: str  # Override in subclass: "chatgpt", "github", etc.

    def __init__(self, db: SykeDB, user_id: str):
        self.db = db
        self.user_id = user_id
        self.content_filter = ContentFilter()

    @abstractmethod
    def ingest(self, **kwargs) -> IngestionResult:
        """Run ingestion for this platform. Returns result with event count."""
        ...
