"""Credential store — read/write ~/.syke/auth.json with atomic writes and file locking."""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
from fcntl import LOCK_EX, LOCK_SH, flock
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Default path; tests can override via constructor arg.
_DEFAULT_PATH = Path.home() / ".syke" / "auth.json"

_SCHEMA_VERSION = 1


def _redact(token: str) -> str:
    """Redact a token for display: show first 6 and last 4 chars."""
    if len(token) <= 12:
        return "***"
    return f"{token[:6]}...{token[-4:]} ({len(token)} chars)"


class AuthStore:
    """File-backed credential store at ~/.syke/auth.json.

    Schema (v1):
    {
      "version": 1,
      "active_provider": "claude-login",
      "providers": {
        "openrouter": {"auth_token": "sk-or-..."},
        "zai":        {"auth_token": "..."}
      }
    }

    All writes are atomic (write to temp + rename). File permissions are
    set to 0600. Advisory file locking prevents daemon/CLI races.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _DEFAULT_PATH

    # ── Read ──────────────────────────────────────────────────────────

    def _read_raw(self) -> dict[str, Any]:
        """Read and parse auth.json. Returns empty schema if missing/corrupt."""
        if not self.path.exists():
            return self._empty()

        try:
            with open(self.path) as f:
                flock(f, LOCK_SH)
                try:
                    data = json.load(f)
                finally:
                    # Lock released on close
                    pass
        except (json.JSONDecodeError, OSError) as e:
            log.warning("auth.json unreadable (%s), treating as empty", e)
            return self._empty()

        if not isinstance(data, dict) or data.get("version") != _SCHEMA_VERSION:
            log.warning("auth.json has unknown version, treating as empty")
            return self._empty()

        return data

    def _empty(self) -> dict[str, Any]:
        return {
            "version": _SCHEMA_VERSION,
            "active_provider": None,
            "providers": {},
        }

    # ── Write (atomic) ────────────────────────────────────────────────

    def _write_raw(self, data: dict[str, Any]) -> None:
        """Atomically write auth.json with 0600 permissions."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file in same directory, then rename (atomic on POSIX).
        fd, tmp_path = tempfile.mkstemp(
            dir=self.path.parent, suffix=".tmp", prefix=".auth-"
        )
        try:
            with os.fdopen(fd, "w") as f:
                flock(f, LOCK_EX)
                json.dump(data, f, indent=2)
                f.write("\n")
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
            os.rename(tmp_path, self.path)
        except BaseException:
            # Clean up temp file on any failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _mutate(self, fn: Any) -> None:
        """Read-modify-write with locking."""
        data = self._read_raw()
        fn(data)
        self._write_raw(data)

    # ── Public API ────────────────────────────────────────────────────

    def get_active_provider(self) -> str | None:
        """Return the active provider ID, or None if unset."""
        return self._read_raw().get("active_provider")

    def set_active_provider(self, provider_id: str) -> None:
        """Set the active provider."""
        self._mutate(lambda d: d.__setitem__("active_provider", provider_id))

    def get_token(self, provider_id: str) -> str | None:
        """Return the stored auth token for a provider, or None."""
        providers = self._read_raw().get("providers", {})
        entry = providers.get(provider_id, {})
        return entry.get("auth_token")

    def set_token(self, provider_id: str, token: str) -> None:
        """Store an auth token for a provider."""

        def _update(data: dict[str, Any]) -> None:
            providers = data.setdefault("providers", {})
            providers[provider_id] = {"auth_token": token}

        self._mutate(_update)

    def remove_token(self, provider_id: str) -> bool:
        """Remove a provider's stored credentials. Returns True if anything was removed."""
        data = self._read_raw()
        providers = data.get("providers", {})
        if provider_id not in providers:
            return False
        del providers[provider_id]
        # Clear active if we just removed it
        if data.get("active_provider") == provider_id:
            data["active_provider"] = None
        self._write_raw(data)
        return True

    def list_providers(self) -> dict[str, dict[str, str]]:
        """Return {provider_id: {redacted info}} for display."""
        data = self._read_raw()
        active = data.get("active_provider")
        result: dict[str, dict[str, str]] = {}
        for pid, entry in data.get("providers", {}).items():
            token = entry.get("auth_token", "")
            result[pid] = {
                "credential": _redact(token) if token else "(none)",
                "active": "yes" if pid == active else "",
            }
        return result

    def status_dict(self) -> dict[str, Any]:
        """Return a status summary for doctor/auth status commands."""
        data = self._read_raw()
        active = data.get("active_provider")
        providers = data.get("providers", {})
        return {
            "active_provider": active,
            "configured_providers": list(providers.keys()),
            "has_file": self.path.exists(),
            "path": str(self.path),
        }
