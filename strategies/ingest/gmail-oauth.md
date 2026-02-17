# Gmail OAuth Extraction Strategy

## Source
Gmail via Google API (OAuth 2.0)

## Authentication
- Uses OAuth 2.0 with `gmail.readonly` scope
- Token stored at `~/.config/syke/gmail_token.json`
- Credentials at `~/.config/syke/gmail_credentials.json`
- Auto-refreshes expired tokens

## Extraction Approach
1. List messages using `users.messages.list` (paginated, 100 per page)
2. For each message, fetch metadata headers: Subject, From, To, Date
3. Extract snippet (first ~200 chars of body) from message object
4. Parse labels to determine: SENT vs INBOX, IMPORTANT, STARRED, categories

## Event Mapping
- `email_sent` — messages with SENT label
- `email_received` — messages without SENT label

## Key Fields
- **timestamp**: From Date header (RFC 2822 format → ISO 8601)
- **title**: Subject header
- **content**: "From/To: <address>\nSubject: <subject>\n\n<snippet>"
- **metadata**: from, to, labels[], gmail_id, thread_id

## Rate Limits
- Gmail API: 250 quota units per user per second
- messages.list: 5 units, messages.get: 5 units
- Practical limit: ~25 messages/second

## Known Issues
- Snippet may contain HTML entities — decoded by the API
- Some messages lack Date header — fall back to internalDate
- Thread grouping available via thread_id for future use
