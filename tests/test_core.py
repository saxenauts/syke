from __future__ import annotations

import getpass
import json
import os
import time
import urllib.error
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import syke.config as config_module
import syke.version_check as version_module
from syke.config import clean_claude_env
from syke.time import (
    day_part,
    format_for_human,
    format_for_llm,
    require_utc,
    resolve_user_tz,
    to_local,
)
from syke.version_check import CACHE_TTL_SECONDS


class _PypiResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload: bytes = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _PypiResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


# --- Config ---
@pytest.mark.parametrize(
    "env_value,expected_suffix",
    [
        ("/tmp/syke-custom-data", None),
        (None, Path.home() / ".syke" / "data"),
    ],
)
def test_default_data_dir_resolves_env_override_or_home(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
    expected_suffix: Path | None,
) -> None:
    if env_value is None:
        monkeypatch.delenv("SYKE_DATA_DIR", raising=False)
        assert config_module._resolve_data_dir() == expected_suffix
        return

    monkeypatch.setenv("SYKE_DATA_DIR", env_value)
    assert config_module._resolve_data_dir() == Path(env_value).resolve()


@pytest.mark.parametrize(
    "env_value,expected",
    [
        ("custom-user", "custom-user"),
        (None, getpass.getuser()),
    ],
)
def test_default_user_uses_env_or_system_username(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
    expected: str,
) -> None:
    if env_value is None:
        monkeypatch.delenv("SYKE_USER", raising=False)
    else:
        monkeypatch.setenv("SYKE_USER", env_value)

    resolved = os.getenv("SYKE_USER", "") or getpass.getuser()
    assert resolved == expected
    assert len(resolved) > 0


# --- Clean Claude env ---
@pytest.mark.parametrize(
    "marker_key,marker_value",
    [
        ("CLAUDECODE", "1"),
    ],
)
def test_clean_claude_env_strips_and_restores_markers_while_preserving_unrelated(
    monkeypatch: pytest.MonkeyPatch,
    marker_key: str,
    marker_value: str,
) -> None:
    monkeypatch.setenv(marker_key, marker_value)
    monkeypatch.setenv("HOME", "/home/test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "".join(["sk", "-ant-test"]))

    with clean_claude_env():
        assert os.environ.get(marker_key) is None
        assert os.environ.get("HOME") == "/home/test"
        assert os.environ.get("ANTHROPIC_API_KEY") is None

    assert os.environ.get(marker_key) == marker_value
    assert os.environ.get("ANTHROPIC_API_KEY") == "".join(["sk", "-ant-test"])


def test_clean_claude_env_strips_auth_leak_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "leaked-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "".join(["sk", "-ant-leaked"]))
    monkeypatch.setenv("HOME", "/home/test")

    with clean_claude_env():
        assert os.environ.get("ANTHROPIC_AUTH_TOKEN") is None
        assert os.environ.get("ANTHROPIC_API_KEY") is None
        assert os.environ.get("HOME") == "/home/test"

    assert os.environ.get("ANTHROPIC_AUTH_TOKEN") == "leaked-token"
    assert os.environ.get("ANTHROPIC_API_KEY") == "".join(["sk", "-ant-leaked"])


def test_clean_claude_env_restores_markers_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "leaked-token")

    with pytest.raises(ValueError, match="boom"):
        with clean_claude_env():
            assert os.environ.get("CLAUDECODE") is None
            assert os.environ.get("ANTHROPIC_AUTH_TOKEN") is None
            raise ValueError("boom")

    assert os.environ.get("CLAUDECODE") == "1"
    assert os.environ.get("ANTHROPIC_AUTH_TOKEN") == "leaked-token"


# --- Timezone ---
@pytest.mark.parametrize(
    "env_value,expected_key,forbidden_key",
    [
        ("America/New_York", "America/New_York", None),
        ("", None, None),
    ],
)
def test_resolve_user_tz_honors_env_auto_and_invalid_fallback(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected_key: str | None,
    forbidden_key: str | None,
) -> None:
    monkeypatch.setenv("SYKE_TIMEZONE", env_value)
    tz = resolve_user_tz()

    assert isinstance(tz, tzinfo)
    if expected_key is not None:
        assert getattr(tz, "key", None) == expected_key
    if forbidden_key is not None:
        assert getattr(tz, "key", None) != forbidden_key


# --- Time formatting ---
@pytest.mark.parametrize(
    "hour,expected",
    [
        (0, "night"),
        (10, "morning"),
    ],
)
def test_day_part_returns_expected_bucket_for_hour(hour: int, expected: str) -> None:
    assert day_part(hour) == expected


def test_to_local_and_require_utc_handle_strings_naive_and_aware_datetimes() -> None:
    ny_tz = ZoneInfo("America/New_York")
    local_from_str = to_local("2026-06-15T12:00:00Z", user_tz=ny_tz)
    local_from_aware = to_local(
        datetime(2026, 6, 15, 12, 0, tzinfo=UTC),
        user_tz=ny_tz,
    )
    normalized_naive = require_utc(datetime(2026, 2, 27, 12, 0, 0))
    normalized_aware = require_utc(datetime(2026, 2, 27, 12, 0, 0, tzinfo=ZoneInfo("Asia/Tokyo")))

    assert local_from_str.tzinfo == ny_tz
    assert local_from_aware.tzinfo == ny_tz
    assert local_from_str.hour == 8
    assert local_from_aware.hour == 8
    assert normalized_naive.tzinfo == UTC
    assert normalized_naive.hour == 12
    assert normalized_aware.tzinfo == UTC
    assert normalized_aware.hour == 3


def test_format_for_llm_includes_utc_anchor_and_correct_day_part() -> None:
    out = format_for_llm(
        datetime(2026, 2, 27, 6, 0, 0, tzinfo=UTC),
        user_tz=ZoneInfo("America/Los_Angeles"),
    )
    assert "(" in out and "Z)" in out
    assert "evening" in out
    assert "early morning" not in out


def test_format_for_human_labels_today_and_yesterday() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    out_today = format_for_human(now, user_tz=UTC)
    out_yesterday = format_for_human(now - timedelta(days=1), user_tz=UTC)

    assert out_today == f"today {now.strftime('%H:%M:%S')}"
    assert out_yesterday == f"yesterday {(now - timedelta(days=1)).strftime('%H:%M:%S')}"


# --- DST ---
@pytest.mark.parametrize(
    "utc_dt,expected_hour,expected_minute,expected_utc_anchor",
    [
        (datetime(2026, 3, 8, 7, 30, 0, tzinfo=UTC), 3, 30, "07:30Z"),
    ],
)
def test_dst_transitions_keep_correct_local_time_and_utc_anchor(
    utc_dt: datetime,
    expected_hour: int,
    expected_minute: int,
    expected_utc_anchor: str,
) -> None:
    tz = ZoneInfo("America/New_York")
    local = to_local(utc_dt, user_tz=tz)
    llm_out = format_for_llm(utc_dt, user_tz=tz)

    assert local.tzinfo == tz
    assert local.hour == expected_hour
    assert local.minute == expected_minute
    assert expected_utc_anchor in llm_out


# --- Version check ---
@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("1.0.1", "1.0.0", True),
        ("1.0.0", "1.0.1", False),
        ("not-a-version", "1.0.0", False),
        ("0.4.0rc1", "0.3.0", True),
    ],
)
def test_version_gt_handles_stable_prerelease_and_invalid_versions(
    left: str,
    right: str,
    expected: bool,
) -> None:
    assert version_module._version_gt(left, right) is expected


@pytest.mark.parametrize(
    "cache_payload,expected",
    [
        ({"version": "1.2.3", "timestamp": time.time()}, "1.2.3"),
        ({"version": "1.0.0", "timestamp": time.time() - CACHE_TTL_SECONDS - 1}, None),
    ],
)
def test_read_cache_returns_value_only_when_fresh(
    tmp_path: Path,
    cache_payload: dict[str, str | float],
    expected: str | None,
) -> None:
    cache_file = tmp_path / "version_cache.json"
    _ = cache_file.write_text(json.dumps(cache_payload))

    with patch("syke.version_check.CACHE_PATH", cache_file):
        assert version_module._read_cache() == expected


@pytest.mark.parametrize("mode", ["fetch_and_cache", "network_fail"])
def test_get_latest_version_handles_cache_fetch_and_network_failure(
    tmp_path: Path,
    mode: str,
) -> None:
    from syke.version_check import get_latest_version

    cache_file = tmp_path / "version_cache.json"

    if mode == "fetch_and_cache":
        mock_response = _PypiResponse(json.dumps({"info": {"version": "3.1.4"}}).encode())

        with (
            patch("syke.version_check.CACHE_PATH", cache_file),
            patch("syke.version_check.SYKE_HOME", tmp_path),
            patch("urllib.request.urlopen", return_value=mock_response),
        ):
            assert get_latest_version() == "3.1.4"

        assert '"version": "3.1.4"' in cache_file.read_text()
        return

    with (
        patch("syke.version_check.CACHE_PATH", cache_file),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("network down")),
    ):
        assert get_latest_version() is None


def test_check_update_available_returns_expected_state() -> None:
    from syke.version_check import check_update_available

    with patch("syke.version_check.get_latest_version", return_value="99.0.0"):
        available, value = check_update_available("0.1.0")

    assert available is True
    assert value == "99.0.0"


def test_cached_update_available_uses_local_cache_only(tmp_path: Path) -> None:
    from syke.version_check import cached_update_available

    cache_file = tmp_path / "version_cache.json"
    _ = cache_file.write_text(json.dumps({"version": "99.0.0", "timestamp": time.time()}))

    with patch("syke.version_check.CACHE_PATH", cache_file):
        available, latest = cached_update_available("0.1.0")

    assert available is True
    assert latest == "99.0.0"
