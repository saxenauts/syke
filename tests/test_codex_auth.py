"""Tests for syke.llm.codex_auth — Codex credential reading, JWT parsing, token refresh."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from syke.llm.codex_auth import (
    CodexCredentials,
    _jwt_exp,
    read_codex_auth,
    refresh_codex_token,
    ensure_valid_token,
    _update_codex_auth_file,
)


def _make_jwt(payload: dict, header: dict | None = None) -> str:
    hdr = header or {"alg": "RS256", "typ": "JWT"}
    h = base64.urlsafe_b64encode(json.dumps(hdr).encode()).rstrip(b"=").decode()
    p = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{h}.{p}.fake_signature"


def _make_codex_auth_file(
    tmp_path: Path,
    access_token: str = "tok_abc",
    refresh_token: str = "rt_xyz",
    account_id: str = "acct-123",
) -> Path:
    p = tmp_path / "auth.json"
    data = {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": "id_jwt",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "account_id": account_id,
        },
        "last_refresh": "2026-03-01T00:00:00Z",
    }
    p.write_text(json.dumps(data))
    return p


# ── _jwt_exp ──────────────────────────────────────────────────────────────


class TestJwtExp:
    def test_extracts_exp_from_valid_jwt(self):
        exp = int(time.time()) + 3600
        token = _make_jwt({"exp": exp, "sub": "user123"})
        assert _jwt_exp(token) == float(exp)

    def test_returns_zero_for_no_exp(self):
        token = _make_jwt({"sub": "user123"})
        assert _jwt_exp(token) == 0

    @pytest.mark.parametrize("bad_input", ["not-a-jwt", "", "a.b"])
    def test_returns_zero_for_invalid_input(self, bad_input):
        assert _jwt_exp(bad_input) == 0


# ── CodexCredentials ──────────────────────────────────────────────────────


class TestCodexCredentials:
    def test_not_expired_when_future(self):
        creds = CodexCredentials(
            access_token="t",
            refresh_token="r",
            account_id="a",
            expires_at=time.time() + 3600,
        )
        assert not creds.is_expired

    def test_expired_when_past(self):
        creds = CodexCredentials(
            access_token="t",
            refresh_token="r",
            account_id="a",
            expires_at=time.time() - 10,
        )
        assert creds.is_expired

    def test_expired_within_margin(self):
        creds = CodexCredentials(
            access_token="t",
            refresh_token="r",
            account_id="a",
            expires_at=time.time() + 60,  # within 300s margin
        )
        assert creds.is_expired

    def test_unknown_expiry_not_expired(self):
        creds = CodexCredentials(
            access_token="t",
            refresh_token="r",
            account_id="a",
            expires_at=0,
        )
        assert not creds.is_expired


# ── read_codex_auth ───────────────────────────────────────────────────────


class TestReadCodexAuth:
    def test_reads_valid_file(self, tmp_path):
        exp = int(time.time()) + 3600
        token = _make_jwt({"exp": exp})
        p = _make_codex_auth_file(tmp_path, access_token=token)
        creds = read_codex_auth(p)
        assert creds is not None
        assert creds.access_token == token
        assert creds.refresh_token == "rt_xyz"
        assert creds.account_id == "acct-123"
        assert creds.expires_at == float(exp)

    def test_returns_none_for_missing_file(self, tmp_path):
        assert read_codex_auth(tmp_path / "nonexistent.json") is None

    def test_returns_none_for_invalid_json(self, tmp_path):
        p = tmp_path / "auth.json"
        p.write_text("{bad json")
        assert read_codex_auth(p) is None

    def test_returns_none_for_no_access_token(self, tmp_path):
        p = tmp_path / "auth.json"
        data = {"tokens": {"refresh_token": "rt_abc"}}
        p.write_text(json.dumps(data))
        assert read_codex_auth(p) is None

    def test_handles_missing_optional_fields(self, tmp_path):
        p = tmp_path / "auth.json"
        p.write_text(json.dumps({"tokens": {"access_token": "tok_abc"}}))
        creds = read_codex_auth(p)
        assert creds is not None
        assert creds.refresh_token == ""
        assert creds.account_id == ""


# ── refresh_codex_token ───────────────────────────────────────────────────


class TestRefreshCodexToken:
    def test_returns_none_without_refresh_token(self):
        creds = CodexCredentials(
            access_token="t",
            refresh_token="",
            account_id="a",
            expires_at=0,
        )
        assert refresh_codex_token(creds) is None

    @patch("httpx.post")
    def test_refresh_success(self, mock_post):
        new_exp = int(time.time()) + 7200
        new_token = _make_jwt({"exp": new_exp})
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "access_token": new_token,
            "refresh_token": "rt_new",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        creds = CodexCredentials(
            access_token="old",
            refresh_token="rt_old",
            account_id="acct",
            expires_at=0,
        )
        result = refresh_codex_token(creds)
        assert result is not None
        assert result.access_token == new_token
        assert result.refresh_token == "rt_new"
        assert result.account_id == "acct"
        assert result.expires_at == float(new_exp)

    @patch("httpx.post")
    def test_refresh_keeps_old_refresh_if_not_returned(self, mock_post):
        new_token = _make_jwt({"exp": int(time.time()) + 3600})
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": new_token}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        creds = CodexCredentials(
            access_token="old",
            refresh_token="rt_keep",
            account_id="a",
            expires_at=0,
        )
        result = refresh_codex_token(creds)
        assert result is not None
        assert result.refresh_token == "rt_keep"

    @patch("httpx.post")
    def test_refresh_returns_none_on_empty_access(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": ""}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        creds = CodexCredentials(
            access_token="old",
            refresh_token="rt",
            account_id="a",
            expires_at=0,
        )
        assert refresh_codex_token(creds) is None

    @patch("httpx.post")
    def test_refresh_returns_none_on_http_error(self, mock_post):
        mock_post.side_effect = Exception("network error")
        creds = CodexCredentials(
            access_token="old",
            refresh_token="rt",
            account_id="a",
            expires_at=0,
        )
        assert refresh_codex_token(creds) is None


# ── ensure_valid_token ────────────────────────────────────────────────────


class TestEnsureValidToken:
    def test_returns_valid_creds_without_refresh(self, tmp_path):
        exp = int(time.time()) + 3600
        token = _make_jwt({"exp": exp})
        p = _make_codex_auth_file(tmp_path, access_token=token)
        creds = ensure_valid_token(p)
        assert creds is not None
        assert creds.access_token == token

    def test_returns_none_for_missing_file(self, tmp_path):
        assert ensure_valid_token(tmp_path / "nope.json") is None

    @patch("syke.llm.codex_auth.refresh_codex_token")
    def test_refreshes_expired_token(self, mock_refresh, tmp_path):
        exp = int(time.time()) - 1000
        old_token = _make_jwt({"exp": exp})
        p = _make_codex_auth_file(tmp_path, access_token=old_token)

        new_exp = int(time.time()) + 7200
        new_token = _make_jwt({"exp": new_exp})
        mock_refresh.return_value = CodexCredentials(
            access_token=new_token,
            refresh_token="rt_new",
            account_id="acct-123",
            expires_at=float(new_exp),
        )

        creds = ensure_valid_token(p)
        assert creds is not None
        assert creds.access_token == new_token
        mock_refresh.assert_called_once()

    @patch("syke.llm.codex_auth.refresh_codex_token")
    def test_returns_none_when_refresh_fails(self, mock_refresh, tmp_path):
        exp = int(time.time()) - 1000
        old_token = _make_jwt({"exp": exp})
        p = _make_codex_auth_file(tmp_path, access_token=old_token)
        mock_refresh.return_value = None

        assert ensure_valid_token(p) is None


# ── _update_codex_auth_file ───────────────────────────────────────────────


class TestUpdateCodexAuthFile:
    def test_updates_tokens_in_file(self, tmp_path):
        p = _make_codex_auth_file(tmp_path)
        creds = CodexCredentials(
            access_token="new_tok",
            refresh_token="new_rt",
            account_id="acct-123",
            expires_at=0,
        )
        _update_codex_auth_file(creds, p)
        data = json.loads(p.read_text())
        assert data["tokens"]["access_token"] == "new_tok"
        assert data["tokens"]["refresh_token"] == "new_rt"
        assert "last_refresh" in data

    def test_handles_corrupt_file_gracefully(self, tmp_path):
        p = tmp_path / "auth.json"
        p.write_text("not json")
        creds = CodexCredentials(
            access_token="t",
            refresh_token="r",
            account_id="a",
            expires_at=0,
        )
        _update_codex_auth_file(creds, p)  # should not raise
