"""
Update-availability check against GitHub Releases. Pure function, same
shape as health.py's reachability checks — no daemon/GUI references.

This is deliberately "Phase 1" only: tell the user they're behind and
link to the release, nothing more. It does not download or install
anything. See PITCH.md / project notes for why the full silent
auto-update ("Phase 2") is intentionally not built yet — the added
complexity (safely replacing a running Windows executable, code signing,
avoiding false-positive AV flags) isn't worth it before Hydra has an
external user base to justify it.
"""
import re

import requests


def _parse_version(v: str):
    """
    'v1.0.4' / '1.0.4' -> (1, 0, 4). Non-numeric trailing parts (e.g. a
    '-beta' suffix) are ignored rather than raising, so a release tagged
    'v1.1.0-rc1' still compares sanely against '1.0.4'.
    """
    v = v.strip().lstrip('vV')
    parts = re.findall(r'\d+', v)
    return tuple(int(p) for p in parts) if parts else (0,)


def check_for_update(current_version: str, repo: str, timeout: float = 5.0) -> dict:
    """
    Returns {
        'update_available': bool,
        'current_version': str,
        'latest_version': str | None,
        'release_url': str | None,
        'error': str | None,
    }
    Never raises — a failed check (offline, GitHub down, rate-limited)
    just means "couldn't tell," not a daemon startup failure.
    """
    result = {
        "update_available": False,
        "current_version": current_version,
        "latest_version": None,
        "release_url": None,
        "error": None,
    }
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        if resp.status_code == 404:
            # No releases published yet — not an error, just nothing to compare.
            return result
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result

        data = resp.json()
        latest_tag = data.get("tag_name", "")
        result["latest_version"] = latest_tag
        result["release_url"] = data.get("html_url")

        if _parse_version(latest_tag) > _parse_version(current_version):
            result["update_available"] = True
        return result

    except requests.exceptions.Timeout:
        result["error"] = "timeout"
        return result
    except requests.exceptions.ConnectionError:
        result["error"] = "connection refused"
        return result
    except Exception as e:
        result["error"] = str(e)
        return result
