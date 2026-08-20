"""
Pytest configuration for Hydra Torrent.

`config.py` has import-time side effects: it creates directories, sets up a
rotating file-log handler, and reads hydra_config.json from BASE_DIR. Every
other hydra module either imports config directly or imports something that
does. Left alone, running the test suite would create real directories and
log files inside the project folder, and could pick up a developer's live
hydra_config.json (which, on this machine, holds real Plex/Jackett API keys).

config.py already supports redirecting BASE_DIR via the HYDRA_BASE env var
(added for the systemd/Linux LXC deployment) - we reuse that same hook here.
It must be set before ANY hydra module is imported, so this happens at
conftest module level (pytest loads conftest.py before collecting test
files), not inside a fixture.
"""
import json
import os
import sys
import tempfile

_TEST_BASE = tempfile.mkdtemp(prefix="hydra_test_")
os.environ["HYDRA_BASE"] = _TEST_BASE

# config.py falls back to a hardcoded real network share
# (\\192.168.20.4\Plex\...) when no hydra_config.json is present. Without
# this, every media_organizer test that builds a destination path would
# try to os.makedirs() onto that real NAS share and fail (or worse,
# succeed against production infrastructure). Seed a config that keeps
# every path local to the isolated test directory instead.
with open(os.path.join(_TEST_BASE, "hydra_config.json"), "w", encoding="utf-8") as _f:
    json.dump({
        "media_dir_movies": os.path.join(_TEST_BASE, "Movies"),
        "media_dir_tv": os.path.join(_TEST_BASE, "TV"),
    }, _f)

# Make the project root importable (tests/ sits one level below it).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture
def hydra_base_dir():
    """The isolated HYDRA_BASE directory used for this test session."""
    return _TEST_BASE
