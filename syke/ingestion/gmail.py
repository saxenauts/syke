"""Gmail adapter — reads email via gog CLI or Python OAuth fallback.

Two backends:
1. gog CLI (preferred if installed + authenticated): faster, handles keyring natively
2. Python google-auth-oauthlib (fallback): self-contained, no extra install

A real user just runs `syke ingest gmail` — the adapter picks the best backend.
"""

from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from syke.db import SykeDB
from syke.ingestion.base import BaseAdapter
from syke.models import Event, IngestionResult

logger = logging.getLogger(__name__)

# Optional dependency check — gmail extras may not be installed
try:
    import google.auth  # noqa: F401
    import google_auth_oauthlib  # noqa: F401
    import googleapiclient  # noqa: F401
    _HAS_GMAIL_DEPS = True
except ImportError:
    _HAS_GMAIL_DEPS = False


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

def _gog_authenticated(account: str) -> bool:
    """Check if gog CLI is installed AND has any valid token stored."""
    if not shutil.which("gog"):
        return False
    try:
        r = subprocess.run(
            ["gog", "auth", "list"], capture_output=True, text=True, timeout=5
        )
        return r.returncode == 0 and "No tokens stored" not in r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return False


def _python_oauth_available() -> bool:
    """Check if google-auth-oauthlib is installed."""
    return _HAS_GMAIL_DEPS


# ---------------------------------------------------------------------------
# gog backend
# ---------------------------------------------------------------------------

def _run_gog(args: list[str], account: str) -> list:
    """Run a gog command and parse JSON output."""
    cmd = ["gog"] + args + ["--account", account, "--json", "--no-input"]
    logger.debug(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"gog failed (exit {result.returncode}): {result.stderr.strip()}")
    if not result.stdout.strip():
        return []
    return json.loads(result.stdout)


def _fetch_via_gog(account: str, query: str, max_results: int) -> list[dict]:
    """Fetch messages using gog CLI."""
    return _run_gog(
        ["gmail", "messages", "search", query,
         "--max", str(max_results), "--include-body"],
        account,
    )


# ---------------------------------------------------------------------------
# Python OAuth backend
# ---------------------------------------------------------------------------

def _get_python_service(credentials_path: str, token_path: str):
    """Build Gmail API service using Python OAuth.

    Uses embedded Syke client credentials by default (Desktop app type —
    per Google docs, these are "not treated as a secret" for installed apps).
    Falls back to a credentials JSON file if the embedded credentials aren't set.
    Users can override with GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET env vars.
    """
    import os
    from pathlib import Path

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
    tok_path = Path(token_path).expanduser()

    # Embedded credentials (Desktop app — OK to publish per Google docs)
    # Override with GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET env vars.
    client_id = os.getenv("GMAIL_CLIENT_ID", "")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET", "")

    creds = None
    if tok_path.exists():
        creds = Credentials.from_authorized_user_file(str(tok_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if client_id and client_secret:
                # Use embedded / env-var credentials (no file needed)
                client_config = {
                    "installed": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": ["http://localhost"],
                    }
                }
                flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            else:
                # Fall back to credentials JSON file
                creds_path = Path(credentials_path).expanduser()
                if not creds_path.exists():
                    raise RuntimeError(
                        "Gmail OAuth not configured. Choose one:\n\n"
                        "Option A: Set env vars (recommended)\n"
                        "  GMAIL_CLIENT_ID=your-client-id\n"
                        "  GMAIL_CLIENT_SECRET=your-client-secret\n\n"
                        "Option B: Download credentials JSON\n"
                        "  1. Go to https://console.cloud.google.com/apis/credentials\n"
                        "  2. Create OAuth 2.0 Client ID (Desktop application)\n"
                        "  3. Download JSON → ~/.config/syke/gmail_credentials.json\n\n"
                        "Option C: Use gog CLI\n"
                        "  brew install gog && gog auth add --account you@gmail.com"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        tok_path.parent.mkdir(parents=True, exist_ok=True)
        tok_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _fetch_via_python(service, query: str, max_results: int) -> list[dict]:
    """Fetch messages using Python Gmail API client."""
    messages = []
    page_token = None
    fetched = 0

    while fetched < max_results:
        batch_size = min(100, max_results - fetched)
        results = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=batch_size, pageToken=page_token)
            .execute()
        )
        msg_refs = results.get("messages", [])
        if not msg_refs:
            break

        for ref in msg_refs:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=ref["id"], format="full")
                .execute()
            )
            messages.append(msg)
            fetched += 1
            if fetched >= max_results:
                break

        page_token = results.get("nextPageToken")
        if not page_token:
            break

    return messages


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class GmailAdapter(BaseAdapter):
    source = "gmail"

    def ingest(self, **kwargs) -> IngestionResult:
        """Ingest emails from Gmail.

        Automatically picks the best backend:
        1. gog CLI if installed + authenticated (fast, handles keyring)
        2. Python OAuth if google-auth-oauthlib is installed (self-contained)

        Keyword args:
            account: Gmail address (for gog backend)
            max_results: Max emails to fetch (default: 200)
            query: Gmail search query (overrides auto-filter)
            days: Days to look back on first run (default: 30)
            credentials_path: Path to OAuth credentials JSON (Python backend)
            token_path: Path to OAuth token cache (Python backend)
        """
        import os
        account = kwargs.get("account", os.getenv("GMAIL_ACCOUNT", ""))
        max_results = kwargs.get("max_results", 200)
        days = kwargs.get("days", 30)
        query = kwargs.get("query")
        credentials_path = kwargs.get(
            "credentials_path",
            os.getenv("GMAIL_CREDENTIALS_PATH", "~/.config/syke/gmail_credentials.json"),
        )
        token_path = kwargs.get(
            "token_path",
            os.getenv("GMAIL_TOKEN_PATH", "~/.config/syke/gmail_token.json"),
        )

        run_id = self.db.start_ingestion_run(self.user_id, self.source)

        try:
            if not query:
                query = self._build_query(days)

            # Pick backend
            use_gog = account and _gog_authenticated(account)

            if use_gog:
                logger.info(f"Using gog backend (account={account})")
                raw_messages = _fetch_via_gog(account, query, max_results)
            elif _python_oauth_available():
                logger.info("Using Python OAuth backend")
                service = _get_python_service(credentials_path, token_path)
                raw_messages = _fetch_via_python(service, query, max_results)
            else:
                raise RuntimeError(
                    "No Gmail backend available. Choose one:\n\n"
                    "Option A (recommended): Install gog CLI\n"
                    "  brew install gog\n"
                    "  gog auth add --account you@gmail.com\n\n"
                    "Option B: Install gmail extras\n"
                    "  pip install 'syke[gmail]'\n"
                    "  Download credentials from https://console.cloud.google.com/apis/credentials\n"
                    "  Save to: ~/.config/syke/gmail_credentials.json"
                )

            events = []
            for msg in raw_messages:
                event = self._message_to_event(msg)
                if event:
                    events.append(event)

            count = self.db.insert_events(events)
            self.db.complete_ingestion_run(run_id, count)
            logger.info(f"Gmail: {count} new events from {len(raw_messages)} messages")
            return IngestionResult(
                run_id=run_id, source=self.source, user_id=self.user_id,
                events_count=count,
            )
        except Exception as e:
            self.db.complete_ingestion_run(run_id, 0, error=str(e))
            raise

    def _build_query(self, days: int) -> str:
        """Build Gmail search query with incremental support."""
        last_sync = self.db.get_last_sync_timestamp(self.user_id, self.source)
        if last_sync:
            try:
                ts = datetime.fromisoformat(last_sync)
                date_filter = f"after:{ts.strftime('%Y/%m/%d')}"
            except (ValueError, TypeError):
                date_filter = f"newer_than:{days}d"
        else:
            date_filter = f"newer_than:{days}d"

        return f"in:inbox category:primary {date_filter}"

    def _message_to_event(self, msg: dict) -> Event | None:
        """Convert a Gmail API message to a Syke Event.

        Works with both gog --json output and Python API response
        (same structure — gog preserves the Gmail API format).
        """
        msg_id = msg.get("id", "")
        if not msg_id:
            return None

        # Already ingested? Skip early.
        if self.db.event_exists_by_external_id(self.source, self.user_id, msg_id):
            return None

        # Parse headers
        headers = {}
        payload = msg.get("payload", {})
        for h in payload.get("headers", []):
            headers[h.get("name", "")] = h.get("value", "")

        subject = headers.get("Subject", "(no subject)")
        from_addr = headers.get("From", "")
        to_addr = headers.get("To", "")
        date_str = headers.get("Date", "")

        # Parse timestamp with proper fallback
        timestamp = self._parse_timestamp(date_str, msg.get("internalDate"))

        # Extract body (full text if available, else snippet)
        snippet = msg.get("snippet", "")
        body = self._extract_body(msg)
        content_text = body if body else snippet

        labels = msg.get("labelIds", [])
        is_sent = "SENT" in labels
        event_type = "email_sent" if is_sent else "email_received"

        direction = "To" if is_sent else "From"
        addr = to_addr if is_sent else from_addr
        content = f"{direction}: {addr}\nSubject: {subject}\n\n{content_text}"

        # Content filter
        filtered, _reason = self.content_filter.process(content, subject)
        if filtered is None:
            return None
        content = filtered

        return Event(
            user_id=self.user_id,
            source=self.source,
            timestamp=timestamp,
            event_type=event_type,
            title=subject,
            content=content,
            external_id=msg_id,
            metadata={
                "from": from_addr,
                "to": to_addr,
                "labels": labels,
                "gmail_id": msg_id,
                "thread_id": msg.get("threadId", ""),
            },
        )

    def _parse_timestamp(
        self, date_str: str, internal_date_ms: str | int | None
    ) -> datetime:
        """Parse email timestamp: Date header -> internalDate -> now."""
        if date_str:
            try:
                return parsedate_to_datetime(date_str)
            except Exception:
                pass
        if internal_date_ms:
            try:
                ms = int(internal_date_ms)
                return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            except (ValueError, TypeError, OSError):
                pass
        return datetime.now(tz=timezone.utc)

    def _extract_body(self, msg: dict) -> str:
        """Extract plain text body from message payload.

        Handles simple, multipart, and nested multipart messages.
        """
        payload = msg.get("payload", {})

        # Collect candidate parts: top-level body, then text/plain parts at any depth
        candidates = [payload]
        for part in payload.get("parts", []):
            candidates.append(part)
            candidates.extend(part.get("parts", []))

        for part in candidates:
            # Skip non-text MIME types in multipart parts (but allow top-level payload)
            if part is not payload and part.get("mimeType") != "text/plain":
                continue
            data = part.get("body", {}).get("data")
            if data:
                try:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")[:5000]
                except Exception:
                    pass

        return ""
