# Gmail OAuth Extraction Strategy

## Source
Gmail via Google API (OAuth 2.0)

## Authentication
- Uses OAuth 2.0 with `gmail.readonly` scope
- Token stored at `~/.config/syke/gmail_token.json`
- Credentials at `~/.config/syke/gmail_credentials.json`
- Auto-refreshes expired tokens
- Supports two backends:
  - `gog` CLI backend (preferred when authenticated)
  - Python OAuth backend (`google-auth-oauthlib` + Gmail API client)

## Extraction Approach
1. List messages using `users.messages.list` (paginated, 100 per page)
2. For each message, fetch full message payload (`format="full"` in Python backend; `--include-body` in `gog` backend)
3. Parse headers: Subject, From, To, Date
4. Extract plain-text body from payload (top-level + multipart `text/plain` parts)
5. Truncate extracted body to max 5000 characters
6. Fallback to Gmail `snippet` only when no decodable body is found
7. Parse labels to determine: SENT vs INBOX, IMPORTANT, STARRED, categories

## Event Mapping
- `email_sent` — messages with SENT label
- `email_received` — messages without SENT label

## Key Fields
- **timestamp**: From Date header (RFC 2822 format → ISO 8601)
- **title**: Subject header
- **content**: `"From/To: <address>\nSubject: <subject>\n\n<body_or_snippet>"`
  - `body_or_snippet` is usually extracted body text (up to 5000 chars), not just a snippet
- **metadata**: from, to, labels[], gmail_id, thread_id

## Privacy Filtering
- Gmail content runs through the shared pre-ingestion content filter before insertion into `syke.db`
- `redact_credentials` (default: `true`): sanitizes known secret/token patterns with `[REDACTED]`
- `skip_private_messages` (default: `true`): skips events that match private-message transcript patterns
- Result: raw fetched message content may be modified or dropped before persistence

## Rate Limits
- Gmail API: 250 quota units per user per second
- messages.list: 5 units, messages.get: 5 units
- Practical limit: ~25 messages/second

## Known Issues
- Some messages lack Date header — fall back to internalDate
- HTML-only messages may not yield clean plain text; snippet fallback is used when no text body is decodable
- Thread grouping available via thread_id for future use
