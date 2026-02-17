"""Claude Code adapter — ingests from two local stores.

Data sources:
1. ~/.claude/projects/{path}/*.jsonl — rich sessions with project context,
   git branch, cwd, assistant messages, summaries. Preferred source.
2. ~/.claude/transcripts/ses_*.jsonl — lightweight sessions with user messages
   and tool calls only. Fallback for sessions not in projects/.

Design decisions:
- One event per session (not per message) — sessions are the natural unit
- User messages are the semantic content — what the person asked/wanted
- Assistant messages included when available (from project store)
- System reminders stripped — they're scaffolding, not the user's words
- Tool usage tracked in metadata — reveals work patterns
- Project path and git branch in metadata — reveals what was being built
- Content capped at 50K chars — prevents DB bloat from long sessions
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from syke.ingestion.base import BaseAdapter
from syke.models import Event, IngestionResult

logger = logging.getLogger(__name__)


class ClaudeCodeAdapter(BaseAdapter):
    source = "claude-code"

    def ingest(self, **kwargs) -> IngestionResult:
        """Ingest Claude Code sessions from both project and transcript stores."""
        claude_dir = Path(os.path.expanduser("~/.claude"))
        run_id = self.db.start_ingestion_run(self.user_id, self.source)
        count = 0
        seen_sessions: set[str] = set()

        # Determine last sync time for mtime optimization
        # Note: DB stores UTC via datetime('now') — must interpret as UTC, not local
        last_sync = self.db.get_last_sync_timestamp(self.user_id, self.source)
        last_sync_epoch = (
            datetime.fromisoformat(last_sync).replace(tzinfo=UTC).timestamp()
            if last_sync
            else 0
        )

        try:
            # Pass 1: Project sessions (richer data — preferred)
            projects_dir = claude_dir / "projects"
            if projects_dir.exists():
                for project_dir in sorted(projects_dir.iterdir()):
                    if not project_dir.is_dir():
                        continue
                    project_path = self._decode_project_dir(project_dir.name)
                    for fpath in sorted(project_dir.glob("*.jsonl"), key=os.path.getmtime):
                        # Skip files older than last sync — already ingested
                        if fpath.stat().st_mtime < last_sync_epoch:
                            seen_sessions.add(fpath.stem)
                            continue
                        try:
                            event = self._parse_project_session(fpath, project_path)
                            if event and event.content.strip():
                                # Run content filter
                                filtered, _ = self.content_filter.process(
                                    event.content, event.title or ""
                                )
                                if filtered is None:
                                    seen_sessions.add(fpath.stem)
                                    continue
                                event.content = filtered
                                self.db.insert_event(event)
                                count += 1
                                seen_sessions.add(fpath.stem)
                        except Exception as exc:
                            logger.warning("Failed to parse session %s: %s", fpath.name, exc)
                            continue

            # Pass 2: Transcript sessions (fill in anything not covered)
            transcripts_dir = claude_dir / "transcripts"
            if transcripts_dir.exists():
                for fpath in sorted(transcripts_dir.glob("*.jsonl"), key=os.path.getmtime):
                    # Skip if already ingested from project store
                    if fpath.stem in seen_sessions:
                        continue
                    # Skip files older than last sync — already ingested
                    if fpath.stat().st_mtime < last_sync_epoch:
                        continue
                    try:
                        event = self._parse_transcript_session(fpath)
                        if event and event.content.strip():
                            # Run content filter
                            filtered, _ = self.content_filter.process(
                                event.content, event.title or ""
                            )
                            if filtered is None:
                                continue
                            event.content = filtered
                            self.db.insert_event(event)
                            count += 1
                    except Exception as exc:
                        logger.warning("Failed to parse transcript %s: %s", fpath.name, exc)
                        continue

            self.db.complete_ingestion_run(run_id, count)
            return IngestionResult(
                source=self.source, events_count=count,
                run_id=run_id, user_id=self.user_id,
            )
        except Exception as e:
            self.db.complete_ingestion_run(run_id, count, error=str(e))
            raise

    def _decode_project_dir(self, dirname: str) -> str:
        """Convert project dir name back to a path.

        Claude Code encodes paths by replacing `/` with `-`, but real directory
        names can contain hyphens (e.g. `claude-hack`) or spaces encoded as `-`.
        We resolve ambiguity by DFS-walking the actual filesystem.

        ~/.claude/projects/-Users-jane-Documents-myproject → ~/Documents/myproject
        """
        # Strip leading dash, split into tokens
        raw = dirname.lstrip("-")
        tokens = raw.split("-")

        resolved = self._resolve_path_dfs(Path("/"), tokens, 0)
        if resolved is None:
            # Fallback for deleted/moved projects: naive replacement
            path = "/" + dirname.lstrip("-").replace("-", "/")
        else:
            path = str(resolved)

        # Apply ~/ shorthand
        home = str(Path.home())
        if path.startswith(home + "/"):
            path = "~/" + path[len(home) + 1:]
        elif path == home:
            path = "~"
        return path

    def _resolve_path_dfs(self, base: Path, tokens: list[str], idx: int) -> Path | None:
        """DFS backtracking resolver: try consuming 1..N tokens as a single
        directory segment (joined with `-` or ` `), checking the filesystem
        at each step. Returns the first valid complete path."""
        if idx == len(tokens):
            return base if base.is_dir() else None

        # Try consuming 1..remaining tokens as one segment
        for end in range(idx + 1, len(tokens) + 1):
            # Try hyphen-joined (e.g. "claude-hack")
            segment_hyphen = "-".join(tokens[idx:end])
            candidate = base / segment_hyphen
            if candidate.is_dir():
                result = self._resolve_path_dfs(candidate, tokens, end)
                if result is not None:
                    return result

            # Try space-joined (e.g. "Acme Corp Inc")
            if end > idx + 1:
                segment_space = " ".join(tokens[idx:end])
                candidate = base / segment_space
                if candidate.is_dir():
                    result = self._resolve_path_dfs(candidate, tokens, end)
                    if result is not None:
                        return result

        return None

    def _parse_project_session(self, fpath: Path, project_path: str) -> Event | None:
        """Parse a project-store session JSONL (rich format)."""
        lines = self._read_jsonl(fpath)
        if not lines:
            return None

        # Extract messages by type
        user_lines = [l for l in lines if l.get("type") == "user"]
        assistant_lines = [l for l in lines if l.get("type") == "assistant"]
        summary_lines = [l for l in lines if l.get("type") == "summary"]

        if not user_lines:
            return None

        # Timestamp from first line
        timestamp = self._parse_timestamp(lines[0])
        if not timestamp:
            return None

        # Extract user content — from message.content field
        content_parts = []
        for msg in user_lines:
            text = self._extract_message_content(msg)
            cleaned = self._strip_system_tags(text)
            cleaned = self._strip_agent_scaffolding(cleaned)
            if cleaned.strip():
                content_parts.append(cleaned.strip())

        # Also include assistant responses (they show what was built/decided)
        for msg in assistant_lines:
            text = self._extract_message_content(msg)
            if text.strip():
                content_parts.append(f"[assistant] {text.strip()[:2000]}")

        content = "\n\n---\n\n".join(content_parts)
        if not content.strip():
            return None

        # Skip very short sessions (noise: "Hello", interrupted requests)
        if len(content) < 50:
            return None

        # Metadata
        first_user = user_lines[0]
        git_branch = first_user.get("gitBranch", "")
        cwd = first_user.get("cwd", "")
        session_id = first_user.get("sessionId", fpath.stem)

        # Tool usage from progress lines
        progress_lines = [l for l in lines if l.get("type") == "progress"]
        tool_names = Counter()
        for p in progress_lines:
            data = p.get("data", {})
            if isinstance(data, dict) and data.get("toolName"):
                tool_names[data["toolName"]] += 1

        # Duration
        end_time = self._parse_timestamp(lines[-1])
        duration_minutes = 0.0
        if end_time and timestamp:
            duration_minutes = (end_time - timestamp).total_seconds() / 60

        metadata = {
            "session_id": session_id,
            "store": "project",
            "project": project_path,
            "user_messages": len(user_lines),
            "assistant_messages": len(assistant_lines),
            "total_lines": len(lines),
            "duration_minutes": round(duration_minutes, 1),
        }
        if git_branch:
            metadata["git_branch"] = git_branch
        if cwd:
            metadata["cwd"] = cwd
        if tool_names:
            metadata["tools_used"] = dict(tool_names.most_common(10))
        summary = None
        if summary_lines:
            # Include summary text — it's a condensed version of the session
            for s in summary_lines:
                summary_text = self._extract_message_content(s)
                if summary_text.strip():
                    metadata["summary"] = summary_text[:1000]
                    summary = summary_text
                    break

        # Title: prefer summary first sentence, fallback to first user message
        raw_title = content_parts[0] if content_parts else fpath.stem
        title = self._make_title(raw_title, summary=summary)

        return Event(
            user_id=self.user_id,
            source=self.source,
            timestamp=timestamp,
            event_type="session",
            title=title,
            content=content[:50000],
            metadata=metadata,
        )

    def _parse_transcript_session(self, fpath: Path) -> Event | None:
        """Parse a transcript-store session JSONL (lightweight format)."""
        lines = self._read_jsonl(fpath)
        if not lines:
            return None

        user_messages = [l for l in lines if l.get("type") == "user"]
        if not user_messages:
            return None

        timestamp = self._parse_timestamp(lines[0])
        if not timestamp:
            return None

        # Build content from user messages
        content_parts = []
        for msg in user_messages:
            text = msg.get("content", "")
            cleaned = self._strip_system_tags(text)
            cleaned = self._strip_agent_scaffolding(cleaned)
            if cleaned.strip():
                content_parts.append(cleaned.strip())

        content = "\n\n---\n\n".join(content_parts)
        if not content.strip():
            return None

        # Skip very short sessions (noise: "Hello", interrupted requests)
        if len(content) < 50:
            return None

        raw_title = content_parts[0] if content_parts else fpath.stem
        title = self._make_title(raw_title)

        # Tool stats
        tool_uses = [l for l in lines if l.get("type") == "tool_use"]
        tool_names = Counter(t.get("tool_name", "unknown") for t in tool_uses)

        # Duration
        end_time = self._parse_timestamp(lines[-1])
        duration_minutes = 0.0
        if end_time and timestamp:
            duration_minutes = (end_time - timestamp).total_seconds() / 60

        # Try to extract project from system reminders in content
        project = None
        for msg in user_messages:
            text = msg.get("content", "")
            if "Working directory:" in text:
                for line in text.split("\n"):
                    if "Working directory:" in line:
                        project = line.split("Working directory:")[-1].strip()
                        break
            if project:
                break

        metadata = {
            "session_id": fpath.stem,
            "store": "transcript",
            "user_messages": len(user_messages),
            "tool_calls": len(tool_uses),
            "total_lines": len(lines),
            "duration_minutes": round(duration_minutes, 1),
        }
        if tool_names:
            metadata["tools_used"] = dict(tool_names.most_common(10))
        if project:
            metadata["project"] = project

        return Event(
            user_id=self.user_id,
            source=self.source,
            timestamp=timestamp,
            event_type="session",
            title=title,
            content=content[:50000],
            metadata=metadata,
        )

    # --- Helpers ---

    # Greeting prefixes to strip from titles (wastes title space)
    _GREETING_PREFIXES = [
        "hey, ", "hi, ", "hello, ",
        "hey ", "hi ", "hello ",
        "can you please ", "could you please ",
        "can you ", "could you ",
        "i would like to ", "i'd like to ",
        "i want to ", "i need to ",
        "please ",
    ]

    def _make_title(self, text: str, summary: str | None = None) -> str:
        """Build a clean session title.

        Priority:
        1. First sentence of summary (if provided and non-empty)
        2. First line of text, with greeting prefixes stripped

        Truncates at last word boundary before 120 chars.
        """
        source = None

        # Prefer summary's first sentence
        if summary and summary.strip():
            first_line = summary.strip().split("\n")[0]
            # Extract first sentence
            for sep in [". ", "! ", "? "]:
                idx = first_line.find(sep)
                if idx != -1:
                    first_line = first_line[: idx + 1]
                    break
            if len(first_line) > 10:
                source = first_line

        # Fallback to first line of text
        if source is None:
            source = text.split("\n")[0].strip() if text else ""

        # Strip greeting prefixes (only if remainder is still > 20 chars)
        lower = source.lower()
        for prefix in self._GREETING_PREFIXES:
            if lower.startswith(prefix):
                remainder = source[len(prefix):]
                if len(remainder.strip()) > 20:
                    source = remainder.strip()
                    # Capitalize first char
                    if source:
                        source = source[0].upper() + source[1:]
                break

        # Truncate at last word boundary before 120 chars
        if len(source) > 120:
            truncated = source[:120]
            last_space = truncated.rfind(" ")
            if last_space > 60:
                source = truncated[:last_space]
            else:
                source = truncated

        return source.strip() if source else "Untitled session"

    def _read_jsonl(self, fpath: Path) -> list[dict]:
        """Read a JSONL file, skipping malformed lines."""
        lines = []
        skipped = 0
        for raw in fpath.open():
            raw = raw.strip()
            if not raw:
                continue
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                skipped += 1
        if skipped and not lines:
            logger.warning("File %s: all %d lines failed JSON parse", fpath.name, skipped)
        elif skipped:
            logger.debug("File %s: skipped %d malformed lines (%d valid)", fpath.name, skipped, len(lines))
        return lines

    def _parse_timestamp(self, line: dict) -> datetime | None:
        """Extract timestamp from a JSONL line (handles multiple formats)."""
        ts = line.get("timestamp", "")
        if not ts:
            return None
        # Handle ISO string
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                return None
        # Handle epoch millis (from history.jsonl)
        if isinstance(ts, (int, float)):
            try:
                return datetime.fromtimestamp(ts / 1000, tz=UTC)
            except (ValueError, OSError):
                return None
        return None

    def _extract_message_content(self, line: dict) -> str:
        """Extract text content from a message line.

        Project store: content is in line["message"]["content"]
        Transcript store: content is in line["content"]
        """
        # Project store format
        msg = line.get("message", {})
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            # Could be a list of content blocks
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        parts.append(block)
                return "\n".join(parts)

        # Transcript store format
        content = line.get("content", "")
        if isinstance(content, str):
            return content

        return ""

    # Headers that indicate agent scaffolding (template structure, not user signal)
    _SCAFFOLDING_HEADERS = [
        "## Notepad Location",
        "## Plan Location (READ ONLY)",
        "## CERTAINTY PROTOCOL",
        "## DECISION FRAMEWORK",
        "## AVAILABLE RESOURCES",
        "## **ABSOLUTE CERTAINTY",
        "## **NO EXCUSES",
    ]

    def _strip_agent_scaffolding(self, text: str) -> str:
        """Remove agent scaffolding sections that are template structure, not user signal.

        Strips from each known scaffolding header to the next non-scaffolding
        ## header. Keeps numbered task sections and other content.
        """
        lines = text.split("\n")
        result = []
        skipping = False

        for line in lines:
            stripped = line.strip()
            is_scaffolding = any(stripped.startswith(h) for h in self._SCAFFOLDING_HEADERS)

            if is_scaffolding:
                skipping = True
                continue

            if skipping and stripped.startswith("#"):
                # Any non-scaffolding markdown header ends the skip
                skipping = False

            if not skipping:
                result.append(line)

        return "\n".join(result)

    def _strip_system_tags(self, text: str) -> str:
        """Remove <system-reminder> and other system tags from text."""
        result = []
        depth = 0
        for line in text.split("\n"):
            # Track nested system tags
            for tag in ["<system-reminder>", "<EXTREMELY_IMPORTANT>", "<EXTREMELY-IMPORTANT>"]:
                if tag in line:
                    depth += 1
                    before = line.split(tag)[0]
                    if before.strip():
                        result.append(before)
                    line = ""
                    break
            closing_tags = ["</system-reminder>", "</EXTREMELY_IMPORTANT>", "</EXTREMELY-IMPORTANT>"]
            for tag in closing_tags:
                if tag in line:
                    depth = max(0, depth - 1)
                    after = line.split(tag)[-1]
                    if after.strip():
                        result.append(after)
                    line = ""
                    break
            if depth == 0 and line:
                result.append(line)
        return "\n".join(result)
