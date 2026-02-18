"""Tests for syke/version_check.py."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock


def test_version_gt_basic():
    from syke.version_check import _version_gt
    assert _version_gt("1.0.1", "1.0.0") is True
    assert _version_gt("2.0.0", "1.9.9") is True
    assert _version_gt("1.0.0", "1.0.1") is False
    assert _version_gt("1.0.0", "1.0.0") is False


def test_version_gt_invalid():
    from syke.version_check import _version_gt
    assert _version_gt("not-a-version", "1.0.0") is False
    assert _version_gt("1.0.0", "not-a-version") is False


def test_read_cache_missing(tmp_path):
    from syke.version_check import _read_cache, CACHE_PATH
    with patch("syke.version_check.CACHE_PATH", tmp_path / "version_cache.json"):
        assert _read_cache() is None


def test_read_cache_hit(tmp_path):
    from syke.version_check import _read_cache, CACHE_TTL_SECONDS
    cache_file = tmp_path / "version_cache.json"
    cache_file.write_text(json.dumps({"version": "1.2.3", "timestamp": time.time()}))
    with patch("syke.version_check.CACHE_PATH", cache_file):
        assert _read_cache() == "1.2.3"


def test_read_cache_expired(tmp_path):
    from syke.version_check import _read_cache, CACHE_TTL_SECONDS
    cache_file = tmp_path / "version_cache.json"
    # Write cache with timestamp older than TTL
    old_ts = time.time() - CACHE_TTL_SECONDS - 1
    cache_file.write_text(json.dumps({"version": "1.0.0", "timestamp": old_ts}))
    with patch("syke.version_check.CACHE_PATH", cache_file):
        assert _read_cache() is None


def test_get_latest_version_uses_cache(tmp_path):
    """get_latest_version returns cached value without hitting network."""
    from syke.version_check import get_latest_version
    cache_file = tmp_path / "version_cache.json"
    cache_file.write_text(json.dumps({"version": "9.9.9", "timestamp": time.time()}))
    with patch("syke.version_check.CACHE_PATH", cache_file), \
         patch("urllib.request.urlopen") as mock_urlopen:
        result = get_latest_version()
        assert result == "9.9.9"
        mock_urlopen.assert_not_called()


def test_get_latest_version_fetches_pypi(tmp_path):
    """get_latest_version hits PyPI when cache is empty."""
    from syke.version_check import get_latest_version
    cache_file = tmp_path / "version_cache.json"

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"info": {"version": "3.1.4"}}).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("syke.version_check.CACHE_PATH", cache_file), \
         patch("syke.version_check.SYKE_HOME", tmp_path), \
         patch("urllib.request.urlopen", return_value=mock_response):
        result = get_latest_version()
        assert result == "3.1.4"
        # Cache should now be written
        assert cache_file.exists()
        cached = json.loads(cache_file.read_text())
        assert cached["version"] == "3.1.4"


def test_get_latest_version_network_failure(tmp_path):
    """get_latest_version returns None on network failure."""
    from syke.version_check import get_latest_version
    import urllib.error
    cache_file = tmp_path / "version_cache.json"
    with patch("syke.version_check.CACHE_PATH", cache_file), \
         patch("urllib.request.urlopen", side_effect=urllib.error.URLError("network down")):
        result = get_latest_version()
        assert result is None


def test_check_update_available_when_newer(tmp_path):
    from syke.version_check import check_update_available
    cache_file = tmp_path / "version_cache.json"
    cache_file.write_text(json.dumps({"version": "99.0.0", "timestamp": time.time()}))
    with patch("syke.version_check.CACHE_PATH", cache_file):
        available, latest = check_update_available("0.1.0")
        assert available is True
        assert latest == "99.0.0"


def test_check_update_available_when_current(tmp_path):
    from syke.version_check import check_update_available
    cache_file = tmp_path / "version_cache.json"
    cache_file.write_text(json.dumps({"version": "0.2.9", "timestamp": time.time()}))
    with patch("syke.version_check.CACHE_PATH", cache_file):
        available, latest = check_update_available("0.2.9")
        assert available is False
        assert latest == "0.2.9"


def test_check_update_available_no_network(tmp_path):
    from syke.version_check import check_update_available
    import urllib.error
    cache_file = tmp_path / "version_cache.json"
    with patch("syke.version_check.CACHE_PATH", cache_file), \
         patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        available, latest = check_update_available("0.2.9")
        assert available is False
        assert latest is None


def test_version_gt_pep440_rc():
    """_version_gt handles rc pre-release strings without raising.

    The primary fix: PyPI returning "0.4.0rc1" as latest no longer silently fails.
    Both "0.4.0" and "0.4.0rc1" extract the same release segment (0,4,0),
    so cross-segment comparisons (e.g. installed=0.3.0, latest=0.4.0rc1) work correctly.
    Known limitation: 0.4.0 vs 0.4.0rc1 compare as equal (both parse to (0,4,0)).
    """
    from syke.version_check import _version_gt
    # The main fix: PyPI returning a pre-release no longer silently fails
    assert _version_gt("0.4.0rc1", "0.3.0") is True   # rc1 parses to (0,4,0) > (0,3,0)
    assert _version_gt("0.4.0", "0.3.9rc1") is True    # 0.3.9rc1 parses to (0,3,9) < (0,4,0)
    # Known limitation: same release segment, equal → False
    assert _version_gt("0.4.0", "0.4.0rc1") is False   # both (0,4,0), equal
    assert _version_gt("0.4.0rc1", "0.4.0") is False   # both (0,4,0), equal


def test_version_gt_pep440_dev():
    """_version_gt handles dev pre-release strings without raising.

    The primary fix: PyPI returning "0.4.0.dev1" as latest no longer silently fails.
    Known limitation: 0.4.0 vs 0.4.0.dev1 compare as equal (both parse to (0,4,0)).
    """
    from syke.version_check import _version_gt
    # The main fix: cross-segment comparisons work
    assert _version_gt("0.4.0.dev1", "0.3.0") is True  # dev1 parses to (0,4,0) > (0,3,0)
    assert _version_gt("0.4.0", "0.3.0.dev5") is True  # 0.3.0.dev5 parses to (0,3,0) < (0,4,0)
    # Known limitation: same release segment, equal → False
    assert _version_gt("0.4.0", "0.4.0.dev1") is False  # both (0,4,0), equal


def test_version_gt_pep440_post():
    """Post-release: release segment only — 0.4.0.post1 and 0.4.0 both parse to (0,4,0), equal → False.

    This is a known limitation of the release-segment-only approach. The test documents it.
    """
    from syke.version_check import _version_gt
    # Both parse to (0, 4, 0) — equal, so neither is greater
    assert _version_gt("0.4.0.post1", "0.4.0") is False
    assert _version_gt("0.4.0", "0.4.0.post1") is False


def test_cached_update_available_no_cache(tmp_path):
    """cached_update_available returns (False, None) when no cache file exists."""
    from syke.version_check import cached_update_available
    with patch("syke.version_check.CACHE_PATH", tmp_path / "version_cache.json"):
        available, latest = cached_update_available("0.1.0")
        assert available is False
        assert latest is None


def test_cached_update_available_when_newer(tmp_path):
    """cached_update_available returns (True, latest) when cache has a newer version."""
    from syke.version_check import cached_update_available
    cache_file = tmp_path / "version_cache.json"
    cache_file.write_text(json.dumps({"version": "99.0.0", "timestamp": time.time()}))
    with patch("syke.version_check.CACHE_PATH", cache_file):
        available, latest = cached_update_available("0.1.0")
        assert available is True
        assert latest == "99.0.0"
