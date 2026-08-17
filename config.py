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
# Version — single source of truth. Matches the tag of the last published
# GitHub release (checked via `gh release view` / the Releases API), NOT
# whatever a stray print statement elsewhere might claim. Bump this as
# part of cutting each release, alongside the git tag.
# ----------------------------------------------------------------------
VERSION = "1.3.1"
GITHUB_REPO = "DelCtrlAlt33/hydra-torrent"

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
CERTS_DIR = os.path.join(BASE_DIR, 'certs')
FULLCHAIN_PATH = os.path.join(CERTS_DIR, 'fullchain.pem')
PRIVKEY_PATH = os.path.join(CERTS_DIR, 'privkey.pem')
CHAIN_PATH = os.path.join(CERTS_DIR, 'chain.pem')
SHARED_DIR = os.path.join(BASE_DIR, 'shared_files')

# Download directories — configurable via hydra_config.json
# Set "download_dir_incomplete" to an NFS path (e.g. /mnt/plex/incomplete) to avoid
# saturating the local HDD with random I/O from multi-file torrent season packs.
# When download_dir_incomplete is on the same NFS mount as media_dir_tv/movies,
# the post-download Plex move becomes an instant os.rename() instead of a full copy.
CONFIG_FILE = os.path.join(BASE_DIR, 'hydra_config.json')

_cfg_dirs = json.load(open(CONFIG_FILE)) if os.path.exists(CONFIG_FILE) else {}
DOWNLOAD_DIR_INCOMPLETE = _cfg_dirs.get(
    'download_dir_incomplete', os.path.join(BASE_DIR, 'downloads_incomplete'))
DOWNLOAD_DIR_COMPLETE = os.path.join(BASE_DIR, 'downloads_complete')

# Legacy support
DOWNLOAD_DIR = DOWNLOAD_DIR_INCOMPLETE

os.makedirs(SHARED_DIR, exist_ok=True)
os.makedirs(DOWNLOAD_DIR_INCOMPLETE, exist_ok=True)
os.makedirs(DOWNLOAD_DIR_COMPLETE, exist_ok=True)
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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
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


# Plex media directories — configurable via hydra_config.json
# Windows default: SMB share on TrueNAS
# Linux LXC:  set "media_dir_movies": "/mnt/plex/movies" in hydra_config.json
_cfg_media = load_config()
MEDIA_DIR_MOVIES  = _cfg_media.get('media_dir_movies',    r'\\192.168.20.4\Plex\movies')
MEDIA_DIR_TV      = _cfg_media.get('media_dir_tv',        r'\\192.168.20.4\Plex\tv')
DOWNLOAD_DIR_OTHER = _cfg_media.get('download_dir_other', '')
