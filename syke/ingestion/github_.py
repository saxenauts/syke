"""GitHub API adapter."""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import os

from syke.ingestion.base import BaseAdapter
from syke.models import Event, IngestionResult

logger = logging.getLogger(__name__)


def _parse_ts(raw: str) -> datetime:
    """Parse a GitHub ISO timestamp, falling back to now(UTC)."""
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


class GitHubAdapter(BaseAdapter):
    source = "github"

    def __init__(self, db, user_id: str, token: str | None = None):
        super().__init__(db, user_id)
        self.token = token or os.getenv("GITHUB_TOKEN", "") or self._detect_gh_token()
        self._last_sync_ts: datetime | None = None

    @staticmethod
    def _detect_gh_token() -> str:
        """Try to get a token from the gh CLI."""
        import subprocess
        try:
            r = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return ""

    def _api(self, url: str, headers_override: dict | None = None) -> dict | list:
        """Make a GitHub API request."""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "syke/0.1",
        }
        if headers_override:
            headers.update(headers_override)
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code == 401:
                raise RuntimeError(
                    "GitHub API authentication failed (HTTP 401). "
                    "Your token may be invalid or expired. "
                    "Set a valid GITHUB_TOKEN or pass --token."
                ) from e
            if e.code == 403:
                body = e.read().decode("utf-8", errors="replace")
                reset_ts = e.headers.get("X-RateLimit-Reset", "")
                remaining = e.headers.get("X-RateLimit-Remaining", "")
                if remaining == "0" or "rate limit" in body.lower():
                    reset_hint = ""
                    if reset_ts:
                        try:
                            reset_dt = datetime.fromtimestamp(int(reset_ts), tz=UTC)
                            reset_hint = f" Resets at {reset_dt.isoformat()}."
                        except (ValueError, OSError):
                            pass
                    raise RuntimeError(
                        f"GitHub API rate limit exceeded (HTTP 403).{reset_hint} "
                        "Authenticate with a GITHUB_TOKEN to get 5,000 requests/hour "
                        "instead of 60."
                    ) from e
            raise

    def _api_paginated(self, url: str, max_pages: int = 5) -> list:
        """Paginate through GitHub API results."""
        results = []
        for page in range(1, max_pages + 1):
            sep = "&" if "?" in url else "?"
            page_url = f"{url}{sep}page={page}&per_page=100"
            data = self._api(page_url)
            if not data:
                break
            results.extend(data)
        return results

    def ingest(self, **kwargs) -> IngestionResult:
        """Ingest data from GitHub for a username."""
        username = kwargs.get("username")
        if not username:
            raise ValueError("username is required for GitHub ingestion")

        run_id = self.db.start_ingestion_run(self.user_id, self.source)

        # Determine last sync time for early-stop optimization
        last_sync = self.db.get_last_sync_timestamp(self.user_id, self.source)
        if last_sync:
            ts = datetime.fromisoformat(last_sync)
            # SQLite datetime('now') is naive UTC — make it aware
            self._last_sync_ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts
        else:
            self._last_sync_ts = None

        try:
            events = []

            # Profile (1 API call)
            events.extend(self._fetch_profile(username))

            # Repos — fetch raw once, reuse for repo events + READMEs
            repos_raw = self._api_paginated(
                f"https://api.github.com/users/{username}/repos?sort=updated"
            )
            events.extend(self._make_repo_events(repos_raw))
            events.extend(self._fetch_readmes(username, repos_raw))

            # Activity events
            events.extend(self._fetch_events(username))

            # Stars with real timestamps
            events.extend(self._fetch_starred(username))

            count = self.db.insert_events(events)
            self.db.complete_ingestion_run(run_id, count)
            return IngestionResult(
                run_id=run_id, source=self.source, user_id=self.user_id,
                events_count=count,
            )
        except Exception as e:
            self.db.complete_ingestion_run(run_id, 0, error=str(e))
            raise

    def _fetch_profile(self, username: str) -> list[Event]:
        """Fetch GitHub user profile."""
        try:
            user = self._api(f"https://api.github.com/users/{username}")
        except HTTPError as e:
            logger.warning("Failed to fetch GitHub profile for %s: HTTP %s", username, e.code)
            return []

        parts = [f"GitHub profile: {user.get('login', username)}"]
        if user.get("name"):
            parts.append(f"Name: {user['name']}")
        if user.get("bio"):
            parts.append(f"Bio: {user['bio']}")
        if user.get("company"):
            parts.append(f"Company: {user['company']}")
        if user.get("location"):
            parts.append(f"Location: {user['location']}")
        if user.get("blog"):
            parts.append(f"Blog: {user['blog']}")
        parts.append(f"Public repos: {user.get('public_repos', 0)}")
        parts.append(f"Followers: {user.get('followers', 0)}")
        parts.append(f"Following: {user.get('following', 0)}")

        created = user.get("created_at", "")
        ts = _parse_ts(created)

        return [Event(
            user_id=self.user_id,
            source=self.source,
            timestamp=ts,
            event_type="profile",
            title=f"GitHub profile: {username}",
            content="\n".join(parts),
            metadata={
                "login": user.get("login"),
                "name": user.get("name"),
                "bio": user.get("bio"),
                "company": user.get("company"),
                "location": user.get("location"),
                "blog": user.get("blog"),
                "public_repos": user.get("public_repos", 0),
                "followers": user.get("followers", 0),
                "following": user.get("following", 0),
                "created_at": created,
            },
        )]

    def _make_repo_events(self, repos_raw: list[dict]) -> list[Event]:
        """Create events from raw repo data."""
        events = []
        for repo in repos_raw:
            ts = _parse_ts(repo.get("created_at", ""))

            desc = repo.get("description", "") or "No description"
            lang = repo.get("language", "N/A")
            stars = repo.get("stargazers_count", 0)
            forks = repo.get("forks_count", 0)
            topics = repo.get("topics", [])
            pushed = repo.get("pushed_at", "")
            issues = repo.get("open_issues_count", 0)

            content_parts = [
                desc,
                f"Language: {lang}",
                f"Stars: {stars}  Forks: {forks}  Open issues: {issues}",
            ]
            if topics:
                content_parts.append(f"Topics: {', '.join(topics)}")
            if pushed:
                content_parts.append(f"Last pushed: {pushed}")

            events.append(Event(
                user_id=self.user_id,
                source=self.source,
                timestamp=ts,
                event_type="repo_created",
                title=repo["full_name"],
                content="\n".join(content_parts),
                metadata={
                    "repo": repo["full_name"],
                    "language": repo.get("language"),
                    "stars": stars,
                    "forks": forks,
                    "topics": topics,
                    "is_fork": repo.get("fork", False),
                    "pushed_at": pushed,
                    "open_issues": issues,
                    "archived": repo.get("archived", False),
                    "license": (repo.get("license") or {}).get("spdx_id"),
                    "homepage": repo.get("homepage"),
                },
            ))
        return events

    def _fetch_readmes(self, username: str, repos_raw: list[dict]) -> list[Event]:
        """Fetch READMEs for top owned non-fork repos."""
        # Filter to owned, non-fork repos sorted by most recently pushed
        candidates = [
            r for r in repos_raw
            if not r.get("fork", False)
            and r.get("owner", {}).get("login", "").lower() == username.lower()
        ]
        candidates.sort(key=lambda r: r.get("pushed_at", ""), reverse=True)
        candidates = candidates[:15]

        events = []
        for repo in candidates:
            try:
                data = self._api(
                    f"https://api.github.com/repos/{repo['full_name']}/readme"
                )
                content_b64 = data.get("content", "")
                readme_text = base64.b64decode(content_b64).decode("utf-8", errors="replace").strip()
                if not readme_text:
                    continue
                readme_text = readme_text[:5000]

                ts = _parse_ts(repo.get("pushed_at", ""))

                events.append(Event(
                    user_id=self.user_id,
                    source=self.source,
                    timestamp=ts,
                    event_type="readme",
                    title=f"README: {repo['full_name']}",
                    content=readme_text,
                    metadata={
                        "repo": repo["full_name"],
                        "language": repo.get("language"),
                        "path": data.get("path", "README.md"),
                    },
                ))
            except HTTPError as e:
                logger.debug("Failed to fetch README for %s: HTTP %s", repo.get('full_name', '?'), e.code)
                continue
            except KeyError:
                continue
        return events

    def _fetch_events(self, username: str) -> list[Event]:
        """Fetch recent activity events."""
        raw_events = self._api_paginated(f"https://api.github.com/users/{username}/events", max_pages=3)
        events = []
        for ev in raw_events:
            ts = _parse_ts(ev.get("created_at", ""))
            # Stop pagination early if events are older than last sync
            if self._last_sync_ts and ts < self._last_sync_ts:
                return events
            event_type = ev.get("type", "unknown")
            repo_name = ev.get("repo", {}).get("name", "")
            payload = ev.get("payload", {})

            # Build readable content based on event type
            if event_type == "PushEvent":
                commits = payload.get("commits", [])
                content = f"Pushed {len(commits)} commits to {repo_name}\n"
                for c in commits[:5]:
                    content += f"  - {c.get('message', '').split(chr(10))[0]}\n"
                title = f"Push to {repo_name}"
            elif event_type == "CreateEvent":
                ref_type = payload.get("ref_type", "")
                content = f"Created {ref_type} in {repo_name}"
                title = f"Created {ref_type}: {repo_name}"
            elif event_type == "IssuesEvent":
                action = payload.get("action", "")
                issue = payload.get("issue", {})
                content = f"{action} issue #{issue.get('number', '')}: {issue.get('title', '')}\n{issue.get('body', '')[:500]}"
                title = f"Issue {action}: {issue.get('title', '')}"
            elif event_type == "PullRequestEvent":
                action = payload.get("action", "")
                pr = payload.get("pull_request", {})
                content = f"{action} PR #{pr.get('number', '')}: {pr.get('title', '')}\n{pr.get('body', '')[:500]}"
                title = f"PR {action}: {pr.get('title', '')}"
            elif event_type == "WatchEvent":
                content = f"Starred {repo_name}"
                title = f"Starred {repo_name}"
            else:
                content = f"{event_type} on {repo_name}"
                title = f"{event_type}: {repo_name}"

            events.append(Event(
                user_id=self.user_id,
                source=self.source,
                timestamp=ts,
                event_type=event_type.lower().replace("event", ""),
                title=title,
                content=content,
                metadata={"github_event_type": event_type, "repo": repo_name},
            ))
        return events

    def _fetch_starred(self, username: str) -> list[Event]:
        """Fetch starred repos with real starred_at timestamps."""
        events = []
        star_header = {"Accept": "application/vnd.github.v3.star+json"}

        for page in range(1, 6):  # Up to 5 pages = 500 stars
            url = (
                f"https://api.github.com/users/{username}/starred"
                f"?page={page}&per_page=100"
            )
            try:
                data = self._api(url, headers_override=star_header)
            except HTTPError as e:
                logger.warning("Failed to fetch starred repos (page %d): HTTP %s", page, e.code)
                break
            if not data:
                break

            for item in data:
                repo = item.get("repo", {})
                ts = _parse_ts(item.get("starred_at", ""))
                # Stop if stars are older than last sync
                if self._last_sync_ts and ts < self._last_sync_ts:
                    return events

                desc = repo.get("description", "") or "No description"
                lang = repo.get("language", "N/A")
                stars = repo.get("stargazers_count", 0)
                topics = repo.get("topics", [])

                content_parts = [desc, f"Language: {lang}", f"Stars: {stars}"]
                if topics:
                    content_parts.append(f"Topics: {', '.join(topics)}")

                events.append(Event(
                    user_id=self.user_id,
                    source=self.source,
                    timestamp=ts,
                    event_type="star",
                    title=f"Starred {repo.get('full_name', '?')}",
                    content="\n".join(content_parts),
                    metadata={
                        "repo": repo.get("full_name"),
                        "language": repo.get("language"),
                        "topics": topics,
                    },
                ))
        return events
