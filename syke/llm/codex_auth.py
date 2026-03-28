"""Codex OAuth credential management — read ~/.codex/auth.json, refresh tokens."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from syke.config_file import expand_path

log = logging.getLogger(__name__)

_CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"
_REFRESH_URL = "https://auth.openai.com/oauth/token"
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

# Refresh 5 minutes before expiry
_REFRESH_MARGIN_SECONDS = 300
_DEFAULT_CODEX_MODEL = "gpt-5.3-codex"


@dataclass
class CodexCredentials:
    access_token: str
    refresh_token: str
    account_id: str
    expires_at: float  # unix timestamp (0 = unknown)

    @property
    def is_expired(self) -> bool:
        if self.expires_at <= 0:
            return False  # unknown expiry = assume valid
        return time.time() >= (self.expires_at - _REFRESH_MARGIN_SECONDS)


def read_codex_auth(path: Path | None = None) -> CodexCredentials | None:
    """Read Codex CLI credentials from ~/.codex/auth.json.

    Returns None if file doesn't exist or is unparseable.
    """
    p = path or _CODEX_AUTH_PATH
    if not p.exists():
        return None

    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read Codex auth file: %s", e)
        return None

    tokens = data.get("tokens", {})
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    account_id = tokens.get("account_id", "")

    if not access_token:
        log.warning("Codex auth.json has no access_token")
        return None

    # Try to extract expiry from JWT (access_token is a JWT)
    expires_at = _jwt_exp(access_token)

    return CodexCredentials(
        access_token=access_token,
        refresh_token=refresh_token or "",
        account_id=account_id,
        expires_at=expires_at,
    )


def refresh_codex_token(creds: CodexCredentials) -> CodexCredentials | None:
    """Refresh the Codex access token using the refresh token.

    Returns new credentials on success, None on failure.
    """
    if not creds.refresh_token:
        log.warning("No refresh token available for Codex")
        return None

    try:
        import httpx
    except ImportError:
        log.error("httpx required for token refresh — pip install httpx")
        return None

    try:
        resp = httpx.post(
            _REFRESH_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": creds.refresh_token,
                "client_id": _CODEX_CLIENT_ID,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        new_access = data.get("access_token", "")
        new_refresh = data.get("refresh_token", creds.refresh_token)

        if not new_access:
            log.error("Token refresh returned empty access_token")
            return None

        return CodexCredentials(
            access_token=new_access,
            refresh_token=new_refresh,
            account_id=creds.account_id,
            expires_at=_jwt_exp(new_access),
        )
    except Exception as e:
        log.error("Codex token refresh failed: %s", e)
        return None


def ensure_valid_token(path: Path | None = None) -> CodexCredentials | None:
    """Read credentials and refresh if expired. Returns valid creds or None."""
    creds = read_codex_auth(path)
    if creds is None:
        return None

    if not creds.is_expired:
        return creds

    log.info("Codex token expired, refreshing...")
    refreshed = refresh_codex_token(creds)
    if refreshed is None:
        return None

    # Write refreshed tokens back to the Codex auth file
    _update_codex_auth_file(refreshed, path or _CODEX_AUTH_PATH)
    return refreshed


def _update_codex_auth_file(creds: CodexCredentials, path: Path) -> None:
    """Update the Codex auth file with refreshed tokens."""
    try:
        data = json.loads(path.read_text())
        data["tokens"]["access_token"] = creds.access_token
        data["tokens"]["refresh_token"] = creds.refresh_token
        data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path.write_text(json.dumps(data, indent=2) + "\n")
    except Exception as e:
        log.warning("Failed to update Codex auth file: %s", e)


def _jwt_exp(token: str) -> float:
    """Extract expiry timestamp from a JWT without verification.

    JWTs are base64url(header).base64url(payload).signature.
    We only need the payload's "exp" claim.
    """
    try:
        import base64

        parts = token.split(".")
        if len(parts) < 2:
            return 0

        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding

        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return float(payload.get("exp", 0))
    except Exception:
        return 0


def get_codex_model(path: Path | None = None) -> str:
    """Return the configured Codex model name (appends -codex if missing)."""
    cfg_path = path or expand_path("~/.codex/config.toml")
    if not cfg_path.exists():
        return _DEFAULT_CODEX_MODEL

    try:
        import tomllib

        with open(cfg_path, "rb") as f:
            raw = tomllib.load(f)
        model = str(raw.get("model", "")).strip()
        if not model:
            return _DEFAULT_CODEX_MODEL
        return model if "-codex" in model else f"{model}-codex"
    except Exception:
        return _DEFAULT_CODEX_MODEL
