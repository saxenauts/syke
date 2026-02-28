from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("1.0.1", "1.0.0", True),
        ("1.0.0", "1.0.1", False),
        ("not-a-version", "1.0.0", False),
        ("0.4.0rc1", "0.3.0", True),
        ("0.4.0", "0.4.0rc1", False),
        ("0.4.0.dev1", "0.3.0", True),
        ("0.4.0", "0.4.0.dev1", False),
    ],
)
def test_version_gt_cases(left: str, right: str, expected: bool) -> None:
    from syke.version_check import _version_gt

    assert _version_gt(left, right) is expected


@pytest.mark.parametrize(
    "cache_content,expect_none,expected_version",
    [
        (lambda ttl: {"version": "1.2.3", "timestamp": time.time()}, False, "1.2.3"),
        (
            lambda ttl: {"version": "1.0.0", "timestamp": time.time() - ttl - 1},
            True,
            None,
        ),
    ],
)
def test_read_cache_states(
    tmp_path, cache_content, expect_none: bool, expected_version: str | None
) -> None:
    from syke.version_check import _read_cache, CACHE_TTL_SECONDS

    cache_file = tmp_path / "version_cache.json"
    if cache_content is not None:
        payload = cache_content(CACHE_TTL_SECONDS)
        cache_file.write_text(json.dumps(payload))

    with patch("syke.version_check.CACHE_PATH", cache_file):
        result = _read_cache()

    if expect_none:
        assert result is None
    else:
        assert result == expected_version


def test_get_latest_version_uses_cache_without_network(tmp_path) -> None:
    from syke.version_check import get_latest_version

    cache_file = tmp_path / "version_cache.json"
    cache_file.write_text(json.dumps({"version": "9.9.9", "timestamp": time.time()}))
    with (
        patch("syke.version_check.CACHE_PATH", cache_file),
        patch("urllib.request.urlopen") as mock_urlopen,
    ):
        result = get_latest_version()
    assert result == "9.9.9"
    mock_urlopen.assert_not_called()


def test_get_latest_version_fetches_and_caches(tmp_path) -> None:
    from syke.version_check import get_latest_version

    cache_file = tmp_path / "version_cache.json"
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(
        {"info": {"version": "3.1.4"}}
    ).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with (
        patch("syke.version_check.CACHE_PATH", cache_file),
        patch("syke.version_check.SYKE_HOME", tmp_path),
        patch("urllib.request.urlopen", return_value=mock_response),
    ):
        result = get_latest_version()

    assert result == "3.1.4"
    cached = json.loads(cache_file.read_text())
    assert cached["version"] == "3.1.4"


def test_get_latest_version_network_failure_returns_none(tmp_path) -> None:
    from syke.version_check import get_latest_version
    import urllib.error

    cache_file = tmp_path / "version_cache.json"
    with (
        patch("syke.version_check.CACHE_PATH", cache_file),
        patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("network down")
        ),
    ):
        assert get_latest_version() is None


@pytest.mark.parametrize(
    "latest,installed,expected_available,expected_latest",
    [
        ("99.0.0", "0.1.0", True, "99.0.0"),
        (None, "0.2.9", False, None),
    ],
)
def test_check_update_available_states(
    latest, installed: str, expected_available: bool, expected_latest: str | None
) -> None:
    from syke.version_check import check_update_available

    with patch("syke.version_check.get_latest_version", return_value=latest):
        available, value = check_update_available(installed)

    assert available is expected_available
    assert value == expected_latest


@pytest.mark.parametrize(
    "cache_payload,installed,expected_available,expected_latest",
    [
        (None, "0.1.0", False, None),
        ({"version": "99.0.0", "timestamp": time.time()}, "0.1.0", True, "99.0.0"),
    ],
)
def test_cached_update_available(
    cache_payload,
    installed: str,
    expected_available: bool,
    expected_latest: str | None,
    tmp_path,
) -> None:
    from syke.version_check import cached_update_available

    cache_file = tmp_path / "version_cache.json"
    if cache_payload is not None:
        cache_file.write_text(json.dumps(cache_payload))

    with patch("syke.version_check.CACHE_PATH", cache_file):
        available, latest = cached_update_available(installed)

    assert available is expected_available
    assert latest == expected_latest
