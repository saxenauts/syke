"""Codex CLI adapter — ingests from two local stores.

Data sources:
1. ~/.codex/sessions/YYYY/MM/DD/rollout-DATETIME-UUID.jsonl — rich sessions
   with project context, git branch, cwd, user messages, assistant responses.
   Preferred source.
2. ~/.codex/history.jsonl — lightweight {session_id, ts, text} entries.
   Fallback for sessions not covered by session files.

Design decisions:
- One event per session (same unit as claude-code adapter)
- User messages are the semantic content
- Developer-role messages (permissions, AGENTS.md, env context) are stripped
- Project path and git branch extracted from session_meta line
- Content capped at 50K chars
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


class CodexAdapter(BaseAdapter):
    source = "codex"

    def ingest(self, **kwargs) -> IngestionResult:
        """Ingest Codex sessions from session files and history."""
        codex_dir = Path(os.path.expanduser("~/.codex"))
        run_id = self.db.start_ingestion_run(self.user_id, self.source)
        count = 0
        seen_sessions: set[str] = set()

        last_sync = self.db.get_last_sync_timestamp(self.user_id, self.source)
        last_sync_epoch = (
            datetime.fromisoformat(last_sync).replace(tzinfo=UTC).timestamp()
            if last_sync
            else 0
        )

        try:
            # Pass 1: Rich session files
            sessions_dir = codex_dir / "sessions"
            if sessions_dir.exists():
                for fpath in sorted(
                    sessions_dir.rglob("rollout-*.jsonl"), key=os.path.getmtime
                ):
                    session_id = self._session_id_from_path(fpath)
                    if session_id:
                        seen_sessions.add(session_id)
                    if fpath.stat().st_mtime < last_sync_epoch:
                        continue
                    try:
                        event = self._parse_session_file(fpath)
                        if event and event.content.strip():
                            filtered, _ = self.content_filter.process(
                                event.content, event.title or ""
                            )
                            if filtered is None:
                                continue
                            event.content = filtered
                            self.db.insert_event(event)
                            count += 1
                    except Exception as exc:
                        logger.warning(
                            "Failed to parse Codex session %s: %s", fpath.name, exc
                        )

            # Pass 2: history.jsonl fallback
            history_path = codex_dir / "history.jsonl"
            if history_path.exists():
                # Group history entries by session_id
                session_entries: dict[str, list[dict]] = {}
                with history_path.open() as f:
                    for raw in f:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            entry = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        sid = entry.get("session_id", "")
                        if sid:
                            session_entries.setdefault(sid, []).append(entry)

                for sid, entries in session_entries.items():
                    if sid in seen_sessions:
                        continue
                    # Use max ts for mtime comparison
                    max_ts = max(e.get("ts", 0) for e in entries)
                    if max_ts < last_sync_epoch:
                        continue
                    try:
                        event = self._parse_history_entries(sid, entries)
                        if event and event.content.strip():
                            filtered, _ = self.content_filter.process(
                                event.content, event.title or ""
                            )
                            if filtered is None:
                                continue
                            event.content = filtered
                            self.db.insert_event(event)
                            count += 1
                    except Exception as exc:
                        logger.warning(
                            "Failed to parse Codex history session %s: %s", sid, exc
                        )

            self.db.complete_ingestion_run(run_id, count)
            return IngestionResult(
                source=self.source,
                events_count=count,
                run_id=run_id,
                user_id=self.user_id,
            )
        except Exception as e:
            self.db.complete_ingestion_run(run_id, count, error=str(e))
            raise

    # --- Session file parser ---

    def _session_id_from_path(self, fpath: Path) -> str | None:
        """Extract UUID from filename: rollout-2026-02-03T10-01-10-{UUID}.jsonl"""
        stem = fpath.stem  # e.g. rollout-2026-02-03T10-01-10-019c24aa-5b5c-7163-8bff-9112bf5c34eb
        # UUID is everything after the datetime portion (last 5 hyphen-groups of UUID)
        # filename format: rollout-YYYY-MM-DDTHH-MM-SS-{uuid}
        # Split off "rollout-" prefix, then find the UUID at the end
        parts = stem.split("-")
        # UUID is 8-4-4-4-12 = 5 groups, joined by hyphens
        if len(parts) >= 6:
            return "-".join(parts[-5:])
        return stem

    def _parse_session_file(self, fpath: Path) -> Event | None:
        """Parse a session JSONL file (rich format)."""
        lines = self._read_jsonl(fpath)
        if not lines:
            return None

        # Extract session_meta (first line)
        meta_line = next(
            (l for l in lines if l.get("type") == "session_meta"), None
        )
        session_id = self._session_id_from_path(fpath)

        # Timestamp from filename or first line
        timestamp = self._parse_timestamp_from_path(fpath) or self._parse_timestamp(
            lines[0]
        )
        if not timestamp:
            return None

        # Project context from meta
        cwd = ""
        git_branch = ""
        model_provider = ""
        if meta_line:
            payload = meta_line.get("payload", {})
            cwd = payload.get("cwd", "")
            git = payload.get("git", {})
            git_branch = git.get("branch", "") if isinstance(git, dict) else ""
            model_provider = payload.get("model_provider", "")

        # Extract user messages from response_items
        user_texts = []
        assistant_texts = []
        tool_names: Counter = Counter()

        for line in lines:
            if line.get("type") != "response_item":
                continue
            payload = line.get("payload", {})
            if payload.get("type") != "message":
                continue
            role = payload.get("role", "")
            content_blocks = payload.get("content", [])
            text = self._extract_content_blocks(content_blocks)

            if role == "user":
                cleaned = self._strip_scaffolding(text)
                if cleaned.strip():
                    user_texts.append(cleaned.strip())
            elif role == "assistant":
                # Track tool calls in assistant output
                for block in content_blocks:
                    if isinstance(block, dict) and block.get("type") == "tool_call":
                        tool_names[block.get("name", "unknown")] += 1
                if text.strip():
                    assistant_texts.append(text.strip()[:2000])
            # "developer" role = permissions/AGENTS.md/env context — skip

        if not user_texts:
            return None

        content_parts = user_texts + [f"[assistant] {t}" for t in assistant_texts]
        content = "\n\n---\n\n".join(content_parts)

        if len(content) < 50:
            return None

        # Duration
        end_time = self._parse_timestamp(lines[-1])
        duration_minutes = 0.0
        if end_time and timestamp:
            duration_minutes = (end_time - timestamp).total_seconds() / 60

        metadata: dict = {
            "session_id": session_id or fpath.stem,
            "store": "session",
            "user_messages": len(user_texts),
            "assistant_messages": len(assistant_texts),
            "total_lines": len(lines),
            "duration_minutes": round(duration_minutes, 1),
        }
        if cwd:
            metadata["cwd"] = cwd
            # Apply ~/ shorthand
            home = str(Path.home())
            if cwd.startswith(home + "/"):
                metadata["project"] = "~/" + cwd[len(home) + 1:]
            elif cwd == home:
                metadata["project"] = "~"
            else:
                metadata["project"] = cwd
        if git_branch:
            metadata["git_branch"] = git_branch
        if model_provider:
            metadata["model_provider"] = model_provider
        if tool_names:
            metadata["tools_used"] = dict(tool_names.most_common(10))

        title = self._make_title(user_texts[0])

        return Event(
            user_id=self.user_id,
            source=self.source,
            timestamp=timestamp,
            event_type="session",
            title=title,
            content=content[:50000],
            metadata=metadata,
        )

    # --- History fallback parser ---

    def _parse_history_entries(
        self, session_id: str, entries: list[dict]
    ) -> Event | None:
        """Parse history.jsonl entries for a session (lightweight format)."""
        # Sort by timestamp
        entries = sorted(entries, key=lambda e: e.get("ts", 0))

        texts = [e.get("text", "").strip() for e in entries if e.get("text", "").strip()]
        if not texts:
            return None

        content = "\n\n---\n\n".join(texts)
        if len(content) < 50:
            return None

        # Timestamp from first entry (epoch seconds)
        first_ts = entries[0].get("ts", 0)
        try:
            timestamp = datetime.fromtimestamp(first_ts, tz=UTC)
        except (ValueError, OSError):
            return None

        metadata = {
            "session_id": session_id,
            "store": "history",
            "user_messages": len(texts),
        }

        title = self._make_title(texts[0])

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

    _GREETING_PREFIXES = [
        "hey, ", "hi, ", "hello, ",
        "hey ", "hi ", "hello ",
        "can you please ", "could you please ",
        "can you ", "could you ",
        "i would like to ", "i'd like to ",
        "i want to ", "i need to ",
        "please ",
    ]

    # Scaffolding sections to strip from user messages
    _SCAFFOLDING_MARKERS = [
        "<permissions instructions>",
        "<environment_context>",
        "<INSTRUCTIONS>",
        "# AGENTS.md instructions",
        "--- project-doc ---",
        "### Available skills",
    ]

    def _strip_scaffolding(self, text: str) -> str:
        """Strip injected scaffolding from user messages."""
        # If the text is entirely scaffolding (starts with a marker), skip it
        stripped = text.strip()
        for marker in self._SCAFFOLDING_MARKERS:
            if stripped.startswith(marker) or marker in stripped[:200]:
                # Return empty — this is a scaffolding block, not user input
                return ""
        return text

    def _make_title(self, text: str) -> str:
        """Build a clean session title from the first user message."""
        source = text.split("\n")[0].strip() if text else ""
        lower = source.lower()
        for prefix in self._GREETING_PREFIXES:
            if lower.startswith(prefix):
                remainder = source[len(prefix):]
                if len(remainder.strip()) > 20:
                    source = remainder.strip()
                    if source:
                        source = source[0].upper() + source[1:]
                break
        if len(source) > 120:
            truncated = source[:120]
            last_space = truncated.rfind(" ")
            if last_space > 60:
                source = truncated[:last_space]
            else:
                source = truncated
        return source.strip() if source else "Untitled session"

    def _extract_content_blocks(self, blocks: list | str) -> str:
        """Extract text from content blocks (list of dicts) or plain string."""
        if isinstance(blocks, str):
            return blocks
        if not isinstance(blocks, list):
            return ""
        parts = []
        for block in blocks:
            if isinstance(block, dict):
                btype = block.get("type", "")
                if btype in ("input_text", "text", "output_text"):
                    parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)

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
        ts = line.get("timestamp", "")
        if not ts:
            return None
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                return None
        if isinstance(ts, (int, float)):
            try:
                return datetime.fromtimestamp(ts / 1000, tz=UTC)
            except (ValueError, OSError):
                return None
        return None

    def _parse_timestamp_from_path(self, fpath: Path) -> datetime | None:
        """Extract timestamp from filename: rollout-2026-02-03T10-01-10-{uuid}.jsonl"""
        # stem: rollout-2026-02-03T10-01-10-019c24aa-...
        stem = fpath.stem
        if not stem.startswith("rollout-"):
            return None
        rest = stem[len("rollout-"):]  # 2026-02-03T10-01-10-019c24aa-...
        # The datetime portion is the first 19 chars: 2026-02-03T10-01-10
        # But hyphens replace colons in time, so: 2026-02-03T10-01-10
        dt_part = rest[:19]  # YYYY-MM-DDTHH-MM-SS
        # Convert HH-MM-SS → HH:MM:SS
        try:
            normalized = dt_part[:10] + "T" + dt_part[11:].replace("-", ":")
            return datetime.fromisoformat(normalized).replace(tzinfo=UTC)
        except (ValueError, IndexError):
            return None
