# GitHub API Extraction Strategy

## Source
GitHub REST API v3

## Authentication
- Personal access token via `GITHUB_TOKEN` env var
- Works without auth (lower rate limits: 60 req/hr vs 5000)
- Token needs: `repo`, `read:user` scopes for full access

## Extraction Approach
1. Fetch user repos: `GET /users/{username}/repos?sort=updated`
2. Fetch activity events: `GET /users/{username}/events`
3. Fetch starred repos: `GET /users/{username}/starred`
4. Paginate up to 5 pages (500 items) per endpoint

## Event Types
- `repo_created` — user's repositories
- `push` — push events with commit messages
- `create` — branch/tag creation
- `issues` — issue open/close/comment
- `pullrequest` — PR open/close/merge
- `watch` — starring a repo
- `star` — explicitly starred repos

## Key Fields
- **timestamp**: `created_at` from API (ISO 8601)
- **title**: varies by event type
- **content**: structured description with key details
- **metadata**: repo name, language, stars, topics

## Rate Limits
- Authenticated: 5000 requests/hour
- Unauthenticated: 60 requests/hour
- Events API: only returns last 90 days, max 300 events

## Known Issues
- Events API only returns public events unless using auth with repo scope
- Starred repos don't have a star timestamp via the basic endpoint
- Fork repos should be tracked but flagged in metadata
