from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

import syke.version_check as version_check
from syke.version_check import CACHE_TTL_SECONDS


class _PypiResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _PypiResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


def _write_cache(cache_file: Path, *, version: str, timestamp: float) -> None:
    cache_file.write_text(json.dumps({"version": version, "timestamp": timestamp}))


@pytest.mark.parametrize(
    "timestamp_offset,expected",
    [
        (CACHE_TTL_SECONDS - 1, "1.2.3"),
        (CACHE_TTL_SECONDS + 1, None),
    ],
)
def test_read_cache_respects_ttl_boundaries(
    tmp_path: Path,
    timestamp_offset: int,
    expected: str | None,
) -> None:
    cache_file = tmp_path / "version_cache.json"
    now = 1_000_000.0
    _write_cache(cache_file, version="1.2.3", timestamp=now - timestamp_offset)

    with (
        patch("syke.version_check.CACHE_PATH", cache_file),
        patch("syke.version_check.time.time", return_value=now),
    ):
        assert version_check._read_cache() == expected


def test_get_latest_version_uses_fresh_cache_without_network(tmp_path: Path) -> None:
    cache_file = tmp_path / "version_cache.json"
    now = 2_000_000.0
    _write_cache(cache_file, version="4.5.6", timestamp=now - (CACHE_TTL_SECONDS - 1))

    with (
        patch("syke.version_check.CACHE_PATH", cache_file),
        patch("syke.version_check.time.time", return_value=now),
        patch("urllib.request.urlopen") as mock_urlopen,
    ):
        assert version_check.get_latest_version() == "4.5.6"

    mock_urlopen.assert_not_called()


def test_get_latest_version_refreshes_stale_cache_from_network(tmp_path: Path) -> None:
    cache_file = tmp_path / "version_cache.json"
    now = 3_000_000.0
    _write_cache(cache_file, version="0.9.0", timestamp=now - (CACHE_TTL_SECONDS + 1))
    response = _PypiResponse(json.dumps({"info": {"version": "1.0.0"}}).encode())

    with (
        patch("syke.version_check.CACHE_PATH", cache_file),
        patch("syke.version_check.SYKE_HOME", tmp_path),
        patch("syke.version_check.time.time", return_value=now),
        patch("urllib.request.urlopen", return_value=response) as mock_urlopen,
    ):
        assert version_check.get_latest_version() == "1.0.0"

    mock_urlopen.assert_called_once()
    assert json.loads(cache_file.read_text())["version"] == "1.0.0"


def test_get_latest_version_handles_malformed_cache_json(tmp_path: Path) -> None:
    cache_file = tmp_path / "version_cache.json"
    cache_file.write_text("{")
    response = _PypiResponse(json.dumps({"info": {"version": "7.8.9"}}).encode())

    with (
        patch("syke.version_check.CACHE_PATH", cache_file),
        patch("syke.version_check.SYKE_HOME", tmp_path),
        patch("urllib.request.urlopen", return_value=response),
    ):
        assert version_check.get_latest_version() == "7.8.9"


def test_get_latest_version_returns_none_on_network_failure_when_cache_unusable(
    tmp_path: Path,
) -> None:
    cache_file = tmp_path / "version_cache.json"
    cache_file.write_text("{")

    with (
        patch("syke.version_check.CACHE_PATH", cache_file),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")),
    ):
        assert version_check.get_latest_version() is None


@pytest.mark.parametrize(
    "payload",
    [
        b"{bad-json",
        json.dumps({"unexpected": "shape"}).encode(),
        json.dumps({"info": {}}).encode(),
    ],
)
def test_get_latest_version_returns_none_on_response_parse_failure(
    tmp_path: Path,
    payload: bytes,
) -> None:
    cache_file = tmp_path / "version_cache.json"
    response = _PypiResponse(payload)

    with (
        patch("syke.version_check.CACHE_PATH", cache_file),
        patch("urllib.request.urlopen", return_value=response),
    ):
        assert version_check.get_latest_version() is None


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("1.2.0", "1.1.9", True),
        ("invalid", "1.0.0", False),
        ("1.0.0", "invalid", False),
        ("", "1.0.0", False),
        ("1.0.0", "", False),
    ],
)
def test_version_gt_handles_invalid_version_strings(
    left: str,
    right: str,
    expected: bool,
) -> None:
    assert version_check._version_gt(left, right) is expected


@pytest.mark.parametrize(
    "latest,installed,expected_available",
    [
        ("invalid", "1.0.0", False),
        ("1.0.0", "invalid", False),
    ],
)
def test_check_update_available_handles_invalid_versions(
    latest: str,
    installed: str,
    expected_available: bool,
) -> None:
    with patch("syke.version_check.get_latest_version", return_value=latest):
        available, reported_latest = version_check.check_update_available(installed)

    assert available is expected_available
    assert reported_latest == latest
