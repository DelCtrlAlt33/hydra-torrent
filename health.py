"""
Reachability checks for Hydra's optional integrations (Jackett, Plex, Jellyfin).

Pure network calls, no GUI/daemon references - same "pure function" shape
as search.py's search_jackett(). Backs the /health endpoint, which exists
specifically so a broken Jackett/Plex/Jellyfin connection is visible in the
UI the moment it happens, instead of failing silently. Bobarr's own open
issues ("downloading wrong content", "completed downloads not moving")
are exactly the symptom of an integration quietly breaking with no
diagnostic signal anywhere for the user to see.
"""
import requests


def _result(configured, reachable, error=None):
    return {"configured": configured, "reachable": reachable, "error": error}


def check_jackett(url: str, api_key: str, timeout: float = 5.0) -> dict:
    """
    Hit Jackett's Torznab 'caps' endpoint - cheap, read-only, works even
    with zero indexers configured, so it verifies "Jackett is up and this
    API key is accepted" without depending on any specific indexer.

    Returns {'configured': bool, 'reachable': bool, 'error': str|None}.
    'configured' is False (not an error) when no URL is set - Jackett is
    an optional integration per the README.
    """
    if not url:
        return _result(False, False)

    try:
        resp = requests.get(
            f"{url.rstrip('/')}/api/v2.0/indexers/all/results/torznab/",
            params={"t": "caps", "apikey": api_key},
            timeout=timeout,
        )
        if resp.status_code == 200:
            return _result(True, True)
        if resp.status_code in (401, 403):
            return _result(True, False, "invalid API key")
        return _result(True, False, f"HTTP {resp.status_code}")
    except requests.exceptions.Timeout:
        return _result(True, False, "timeout")
    except requests.exceptions.ConnectionError:
        return _result(True, False, "connection refused")
    except Exception as e:
        return _result(True, False, str(e))


def check_plex(url: str, token: str, timeout: float = 5.0) -> dict:
    """
    Hit Plex's /library/sections - read-only, requires a valid token, so a
    200 confirms both "Plex is reachable" and "this token actually works,"
    the same distinction media_organizer.notify_plex() already draws
    between a connection failure and an invalid-token 401.

    Returns {'configured': bool, 'reachable': bool, 'error': str|None}.
    """
    if not url or not token:
        return _result(False, False)

    try:
        resp = requests.get(
            f"{url.rstrip('/')}/library/sections",
            params={"X-Plex-Token": token},
            timeout=timeout,
        )
        if resp.status_code == 200:
            return _result(True, True)
        if resp.status_code == 401:
            return _result(True, False, "invalid token")
        return _result(True, False, f"HTTP {resp.status_code}")
    except requests.exceptions.Timeout:
        return _result(True, False, "timeout")
    except requests.exceptions.ConnectionError:
        return _result(True, False, "connection refused (is Plex running?)")
    except Exception as e:
        return _result(True, False, str(e))


def check_jellyfin(url: str, api_key: str, timeout: float = 5.0) -> dict:
    """
    Hit Jellyfin's /System/Info - read-only, requires a valid API key, same
    "reachable vs. authenticated" distinction as check_plex().

    Returns {'configured': bool, 'reachable': bool, 'error': str|None}.
    """
    if not url or not api_key:
        return _result(False, False)

    try:
        resp = requests.get(
            f"{url.rstrip('/')}/System/Info",
            headers={"X-Emby-Token": api_key},
            timeout=timeout,
        )
        if resp.status_code == 200:
            return _result(True, True)
        if resp.status_code in (401, 403):
            return _result(True, False, "invalid API key")
        return _result(True, False, f"HTTP {resp.status_code}")
    except requests.exceptions.Timeout:
        return _result(True, False, "timeout")
    except requests.exceptions.ConnectionError:
        return _result(True, False, "connection refused (is Jellyfin running?)")
    except Exception as e:
        return _result(True, False, str(e))
