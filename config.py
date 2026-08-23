import os
import sys
import json
import ctypes
import logging
from logging.handlers import RotatingFileHandler

# ----------------------------------------------------------------------
# Base directory - handle both script and PyInstaller exe
# ----------------------------------------------------------------------
def get_base_dir():
    """Get the correct base directory for data files"""
    # Check if running as PyInstaller bundle
    if getattr(sys, 'frozen', False):
        # Running as compiled .exe - use AppData
        app_data = os.path.join(os.environ['LOCALAPPDATA'], 'HydraTorrent')
        os.makedirs(app_data, exist_ok=True)
        return app_data
    # Allow explicit override via env var (useful for systemd on Linux LXC)
    if 'HYDRA_BASE' in os.environ:
        return os.environ['HYDRA_BASE']
    # Running as script - use script directory
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()

# ----------------------------------------------------------------------
# Version - single source of truth. Matches the tag of the last published
# GitHub release (checked via `gh release view` / the Releases API), NOT
# whatever a stray print statement elsewhere might claim. Bump this as
# part of cutting each release, alongside the git tag.
# ----------------------------------------------------------------------
VERSION = "1.4.18"
GITHUB_REPO = "DelCtrlAlt33/hydra-torrent"

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
CERTS_DIR = os.path.join(BASE_DIR, 'certs')
FULLCHAIN_PATH = os.path.join(CERTS_DIR, 'fullchain.pem')
PRIVKEY_PATH = os.path.join(CERTS_DIR, 'privkey.pem')
CHAIN_PATH = os.path.join(CERTS_DIR, 'chain.pem')
SHARED_DIR = os.path.join(BASE_DIR, 'shared_files')

# Download directory - configurable via hydra_config.json.
# Set "download_dir_incomplete" to an NFS path (e.g. /mnt/plex/incomplete) to avoid
# saturating the local HDD with random I/O from multi-file torrent season packs.
# When download_dir_incomplete is on the same NFS mount as media_dir_tv/movies,
# the post-download Plex move becomes an instant os.rename() instead of a full copy.
CONFIG_FILE = os.path.join(BASE_DIR, 'hydra_config.json')


def _default_download_dir():
    """The Windows desktop exe defaults outside the install folder (into
    Downloads, same as a browser) so deleting/reinstalling the app never
    risks the torrents themselves - %LOCALAPPDATA%\\HydraTorrent is BASE_DIR
    there, and used to double as the save path too. Script/server
    deployments (Linux, homelab NFS mounts) keep the original BASE_DIR
    default; there's no "Downloads" folder convention there, and BASE_DIR is
    often deliberately an NFS mount already, per the note above.

    Folder is just named 'downloads', not 'downloads_incomplete': nothing
    ever moves a finished torrent OUT of this folder into some other
    'complete' location (that never got built, see the removed
    DOWNLOAD_DIR_COMPLETE constant) - a still-downloading torrent and a
    fully-seeded one both live here the whole time, so the old name was
    actively misleading about what a user would actually find inside it."""
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.expanduser('~'), 'Downloads', 'HydraTorrent')
    return os.path.join(BASE_DIR, 'downloads')


_cfg_dirs = json.load(open(CONFIG_FILE)) if os.path.exists(CONFIG_FILE) else {}
DOWNLOAD_DIR_INCOMPLETE = _cfg_dirs.get('download_dir_incomplete', _default_download_dir())

# Legacy support
DOWNLOAD_DIR = DOWNLOAD_DIR_INCOMPLETE

def _ensure_dir(path, label):
    """Auto-create a required directory, raising a message that actually
    says what to fix instead of a raw PermissionError/OSError traceback -
    this runs at import time before logging is configured, so a plain
    stack trace would be the only thing anyone ever sees. More people hit
    this path now that download/library folders can come from auto-detect
    instead of always being hand-typed and pre-created."""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise RuntimeError(
            f"Can't create {label} at '{path}': {e}. "
            f"Check that this path is correct and Hydra has permission to write there."
        ) from e


_ensure_dir(SHARED_DIR, 'shared files directory')
_ensure_dir(DOWNLOAD_DIR_INCOMPLETE, 'download folder')
# Don't auto-create network share directories (they already exist on TrueNAS)

# ----------------------------------------------------------------------
# Network constants
# ----------------------------------------------------------------------
SERVER_PORT = 5000
PEER_PORT = 6001
NUM_PARALLEL_CONNECTIONS = 4
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB pieces

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
LOG_PATH = os.path.join(BASE_DIR, 'hydra.log')

# StreamHandler's console output defaults to Windows' legacy console
# codepage (cp1252) rather than UTF-8, unlike the file handler below
# (which is explicitly given encoding='utf-8'). A torrent name/file list
# containing an ordinary Unicode character - e.g. an arrow, a smart
# quote, anything outside cp1252 - used to raise UnicodeEncodeError
# *from inside the logging call itself*, which then propagated up
# through preview_torrent_url() and surfaced to the user as "Could not
# fetch metadata: 'charmap' codec can't encode character..." even
# though the real fetch had already succeeded. reconfigure(errors=
# 'replace') makes a log line degrade to '?' for unencodable characters
# instead of crashing whatever request triggered the log call - stream
# can be None in the frozen --windowed build (no real console attached),
# so this is skipped entirely there, matching that build's existing
# behaviour of console output going nowhere either way.
_console_handler = logging.StreamHandler()
if _console_handler.stream is not None and hasattr(_console_handler.stream, 'reconfigure'):
    _console_handler.stream.reconfigure(errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        _console_handler,
        # Console is hidden in the packaged .exe, so without this file handler
        # every background error (e.g. a failed add_torrent) is silently lost.
        RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'),
    ],
)
logger = logging.getLogger('hydra_torrent')


# ----------------------------------------------------------------------
# Console helper (Windows .exe only)
# ----------------------------------------------------------------------
def hide_console():
    if os.name == "nt" and getattr(sys, 'frozen', False):
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd != 0:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
            ctypes.windll.kernel32.CloseHandle(hwnd)


# ----------------------------------------------------------------------
# Config load / save  (standalone, no self)
# ----------------------------------------------------------------------
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(key, value):
    config = load_config()
    config[key] = value
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f)


# Plex/Jellyfin media directories - configurable via hydra_config.json,
# set "media_dir_movies"/"media_dir_tv" (e.g. "/mnt/plex/movies").
# Deliberately blank until configured - these used to default to a hardcoded
# personal NAS UNC path, which meant every user who never set up Plex/Jellyfin
# still had auto-move try (and fail, slowly, against an unreachable SMB host)
# to file every completed video there. media_organizer checks for blank here
# and skips the move instead of attempting it. DOWNLOAD_DIR_OTHER is the same
# idea for non-video downloads (ISOs, music, archives) - see the Downloads
# settings section for both.
_cfg_media = load_config()
MEDIA_DIR_MOVIES  = _cfg_media.get('media_dir_movies',    '')
MEDIA_DIR_TV      = _cfg_media.get('media_dir_tv',        '')
DOWNLOAD_DIR_OTHER = _cfg_media.get('download_dir_other', '')
