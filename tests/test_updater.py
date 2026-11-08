"""
Tests for updater.py - the Phase 1 "tell them, don't touch anything"
update check. Deliberately dumb on purpose: no downloading, no installing,
just an honest comparison against GitHub's latest published release.
"""
from unittest.mock import Mock, patch

import pytest

from updater import check_for_update, _parse_version


# ---------------------------------------------------------------------------
# _parse_version
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("v1.0.4", (1, 0, 4)),
    ("1.0.4", (1, 0, 4)),
    ("V2.10.0", (2, 10, 0)),
    ("v1.1.0-rc1", (1, 1, 0, 1)),   # trailing qualifier digits still parse sanely
    ("", (0,)),
    ("not-a-version", (0,)),
])
def test_parse_version(raw, expected):
    assert _parse_version(raw) == expected


def test_parse_version_ordering_is_numeric_not_lexicographic():
    # "1.9.0" must be LESS than "1.10.0" - a naive string compare gets
    # this backwards ("1.10.0" < "1.9.0" lexicographically).
    assert _parse_version("1.9.0") < _parse_version("1.10.0")


# ---------------------------------------------------------------------------
# check_for_update
# ---------------------------------------------------------------------------

def _release_response(tag_name, status_code=200):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = {
        "tag_name": tag_name,
        "html_url": f"https://github.com/DelCtrlAlt33/hydra-torrent/releases/tag/{tag_name}",
    }
    return resp


def test_update_available_when_latest_is_newer():
    with patch("updater.requests.get", return_value=_release_response("v1.0.5")):
        result = check_for_update("1.0.4", "DelCtrlAlt33/hydra-torrent")

    assert result["update_available"] is True
    assert result["latest_version"] == "v1.0.5"
    assert result["release_url"].endswith("/v1.0.5")
    assert result["error"] is None


def test_checks_github_releases_api():
    # The check must hit GitHub's releases API for the given owner/name.
    with patch("updater.requests.get", return_value=_release_response("v1.0.4")) as get:
        check_for_update("1.0.4", "DelCtrlAlt33/hydra-torrent")

    called_url = get.call_args[0][0]
    assert called_url == "https://api.github.com/repos/DelCtrlAlt33/hydra-torrent/releases/latest"
    assert "codeberg" not in called_url


def test_no_update_when_current_is_latest():
    with patch("updater.requests.get", return_value=_release_response("v1.0.4")):
        result = check_for_update("1.0.4", "DelCtrlAlt33/hydra-torrent")

    assert result["update_available"] is False


def test_no_update_when_running_a_version_ahead_of_latest_release():
    # This is genuinely the daemon's actual state most of the time - local
    # changes ahead of the last tagged release must never show as "behind."
    with patch("updater.requests.get", return_value=_release_response("v1.0.4")):
        result = check_for_update("1.1.0", "DelCtrlAlt33/hydra-torrent")

    assert result["update_available"] is False


def test_no_releases_published_yet_is_not_an_error():
    with patch("updater.requests.get", return_value=_release_response("", status_code=404)):
        result = check_for_update("1.0.4", "DelCtrlAlt33/hydra-torrent")

    assert result["update_available"] is False
    assert result["error"] is None


def test_reports_other_http_errors():
    with patch("updater.requests.get", return_value=_release_response("", status_code=500)):
        result = check_for_update("1.0.4", "DelCtrlAlt33/hydra-torrent")

    assert result["update_available"] is False
    assert result["error"] == "HTTP 500"


def test_reports_timeout_distinctly():
    import requests
    with patch("updater.requests.get", side_effect=requests.exceptions.Timeout()):
        result = check_for_update("1.0.4", "DelCtrlAlt33/hydra-torrent")

    assert result["error"] == "timeout"
    assert result["update_available"] is False


def test_reports_connection_refused_distinctly():
    import requests
    with patch("updater.requests.get", side_effect=requests.exceptions.ConnectionError()):
        result = check_for_update("1.0.4", "DelCtrlAlt33/hydra-torrent")

    assert result["error"] == "connection refused"


def test_never_raises_on_unexpected_error():
    # A daemon startup path (or the UI's on-load check) must never crash
    # because GitHub's API returned something unexpected.
    with patch("updater.requests.get", side_effect=RuntimeError("weird")):
        result = check_for_update("1.0.4", "DelCtrlAlt33/hydra-torrent")

    assert result["update_available"] is False
    assert "weird" in result["error"]


def test_current_version_always_echoed_back():
    with patch("updater.requests.get", return_value=_release_response("v1.0.4")):
        result = check_for_update("1.0.4", "DelCtrlAlt33/hydra-torrent")
    assert result["current_version"] == "1.0.4"
