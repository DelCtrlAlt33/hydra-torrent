"""
Tests for health.py - the reachability checks behind /health. These are
what let the UI show "Jackett: unreachable" the moment it happens instead
of a user just seeing search silently return nothing, or a completed
download silently never triggering a Plex scan.
"""
from unittest.mock import Mock, patch

import pytest

from health import check_jackett, check_plex, check_jellyfin


def _response(status_code=200, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.text = text
    return resp


# What Jackett's Torznab t=indexers really returns (checked against a live
# Jackett): a valid key gets an <indexers> list; a wrong or missing key gets an
# XML error, still with HTTP 200.
JACKETT_OK = '<?xml version="1.0"?><indexers><indexer id="x" configured="true"/></indexers>'
JACKETT_BAD_KEY = '<?xml version="1.0"?><error code="100" description="Invalid API Key" />'


# ---------------------------------------------------------------------------
# check_jackett
# ---------------------------------------------------------------------------

def test_jackett_not_configured_when_no_url():
    result = check_jackett("", "somekey")
    assert result == {"configured": False, "reachable": False, "error": None}


def test_jackett_not_configured_when_no_key():
    # A cleared key must read "Not configured", never "Connected".
    result = check_jackett("http://127.0.0.1:9117", "")
    assert result == {"configured": False, "reachable": False, "error": None}


def test_jackett_reachable_when_the_key_is_accepted():
    with patch("health.requests.get", return_value=_response(200, JACKETT_OK)):
        result = check_jackett("http://127.0.0.1:9117", "validkey")
    assert result == {"configured": True, "reachable": True, "error": None}


def test_jackett_wrong_key_is_not_connected_even_though_http_is_200():
    # The real bug: Jackett reports a bad key as XML inside an HTTP 200.
    with patch("health.requests.get", return_value=_response(200, JACKETT_BAD_KEY)):
        result = check_jackett("http://127.0.0.1:9117", "wrongkey")
    assert result["configured"] is True
    assert result["reachable"] is False
    assert result["error"] == "invalid API key"


def test_jackett_other_xml_error_shows_its_own_message():
    body = '<error code="900" description="Something else broke" />'
    with patch("health.requests.get", return_value=_response(200, body)):
        result = check_jackett("http://127.0.0.1:9117", "key")
    assert result["reachable"] is False
    assert result["error"] == "Something else broke"


def test_jackett_non_jackett_page_is_not_connected():
    # e.g. the address points at a login page or some other web app
    with patch("health.requests.get", return_value=_response(200, "<html>Please sign in</html>")):
        result = check_jackett("http://127.0.0.1:9117", "key")
    assert result["reachable"] is False
    assert "unexpected response" in result["error"]


@pytest.mark.parametrize("status_code", [401, 403])
def test_jackett_reports_invalid_api_key(status_code):
    with patch("health.requests.get", return_value=_response(status_code)):
        result = check_jackett("http://127.0.0.1:9117", "wrongkey")
    assert result["configured"] is True
    assert result["reachable"] is False
    assert result["error"] == "invalid API key"


def test_jackett_reports_other_http_errors():
    with patch("health.requests.get", return_value=_response(500)):
        result = check_jackett("http://127.0.0.1:9117", "key")
    assert result["reachable"] is False
    assert result["error"] == "HTTP 500"


def test_jackett_reports_timeout_distinctly():
    import requests
    with patch("health.requests.get", side_effect=requests.exceptions.Timeout()):
        result = check_jackett("http://127.0.0.1:9117", "key")
    assert result["configured"] is True
    assert result["reachable"] is False
    assert result["error"] == "timeout"


def test_jackett_reports_connection_refused_distinctly():
    import requests
    with patch("health.requests.get", side_effect=requests.exceptions.ConnectionError()):
        result = check_jackett("http://127.0.0.1:9117", "key")
    assert result["error"] == "connection refused"


def test_jackett_never_raises_on_unexpected_error():
    with patch("health.requests.get", side_effect=RuntimeError("something weird")):
        result = check_jackett("http://127.0.0.1:9117", "key")
    assert result["reachable"] is False
    assert "something weird" in result["error"]


def test_jackett_check_uses_the_indexer_list_not_caps_and_not_a_real_search():
    # Cheap and side-effect-free like caps, but unlike caps (which Jackett
    # answers for any key) it actually validates the key. It must not run a
    # real search, which fans out to every indexer and takes ~17s.
    mock_get = Mock(return_value=_response(200, JACKETT_OK))
    with patch("health.requests.get", mock_get):
        check_jackett("http://127.0.0.1:9117", "key")

    args, kwargs = mock_get.call_args
    assert "results/torznab" in args[0]
    assert kwargs["params"]["t"] == "indexers"
    assert kwargs["params"]["apikey"] == "key"


# ---------------------------------------------------------------------------
# check_plex
# ---------------------------------------------------------------------------

def test_plex_not_configured_when_url_or_token_missing():
    assert check_plex("", "token")["configured"] is False
    assert check_plex("http://192.168.1.5:32400", "")["configured"] is False


def test_plex_reachable_on_200():
    with patch("health.requests.get", return_value=_response(200)):
        result = check_plex("http://192.168.1.5:32400", "validtoken")
    assert result == {"configured": True, "reachable": True, "error": None}


def test_plex_reports_invalid_token():
    with patch("health.requests.get", return_value=_response(401)):
        result = check_plex("http://192.168.1.5:32400", "badtoken")
    assert result["error"] == "invalid token"


def test_plex_reports_connection_refused_with_helpful_hint():
    import requests
    with patch("health.requests.get", side_effect=requests.exceptions.ConnectionError()):
        result = check_plex("http://192.168.1.5:32400", "token")
    assert "is Plex running" in result["error"]


def test_plex_check_hits_library_sections_readonly():
    mock_get = Mock(return_value=_response(200))
    with patch("health.requests.get", mock_get):
        check_plex("http://192.168.1.5:32400", "mytoken")

    args, kwargs = mock_get.call_args
    assert args[0].endswith("/library/sections")
    assert kwargs["params"]["X-Plex-Token"] == "mytoken"


# ---------------------------------------------------------------------------
# check_jellyfin
# ---------------------------------------------------------------------------

def test_jellyfin_not_configured_when_url_or_key_missing():
    assert check_jellyfin("", "key")["configured"] is False
    assert check_jellyfin("http://192.168.1.5:8096", "")["configured"] is False


def test_jellyfin_reachable_on_200():
    with patch("health.requests.get", return_value=_response(200)):
        result = check_jellyfin("http://192.168.1.5:8096", "validkey")
    assert result == {"configured": True, "reachable": True, "error": None}


def test_jellyfin_reports_invalid_api_key():
    with patch("health.requests.get", return_value=_response(401)):
        result = check_jellyfin("http://192.168.1.5:8096", "badkey")
    assert result["error"] == "invalid API key"


def test_jellyfin_reports_connection_refused_with_helpful_hint():
    import requests
    with patch("health.requests.get", side_effect=requests.exceptions.ConnectionError()):
        result = check_jellyfin("http://192.168.1.5:8096", "key")
    assert "is Jellyfin running" in result["error"]


def test_jellyfin_check_hits_system_info_readonly_with_token_header():
    mock_get = Mock(return_value=_response(200))
    with patch("health.requests.get", mock_get):
        check_jellyfin("http://192.168.1.5:8096", "mykey")

    args, kwargs = mock_get.call_args
    assert args[0].endswith("/System/Info")
    assert kwargs["headers"]["X-Emby-Token"] == "mykey"
