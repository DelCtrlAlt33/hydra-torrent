import os
import sys
import json
import ctypes
import logging

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
    else:
        # Running as script - use script directory
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
CERTS_DIR = os.path.join(BASE_DIR, 'certs')
FULLCHAIN_PATH = os.path.join(CERTS_DIR, 'fullchain.pem')
PRIVKEY_PATH = os.path.join(CERTS_DIR, 'privkey.pem')
CHAIN_PATH = os.path.join(CERTS_DIR, 'chain.pem')
SHARED_DIR = os.path.join(BASE_DIR, 'shared_files')

# Download directories (local - fast and reliable for libtorrent)
DOWNLOAD_DIR_INCOMPLETE = os.path.join(BASE_DIR, 'downloads_incomplete')
DOWNLOAD_DIR_COMPLETE = os.path.join(BASE_DIR, 'downloads_complete')

# Plex media directories - same location as your Linux laptop NFS mount
# Windows (SMB): \\192.168.20.4\Plex = TrueNAS:/mnt/MainPool/Plex
# Linux (NFS): /mnt/plex = TrueNAS:/mnt/MainPool/Plex
# Plex sees: /mnt/media = TrueNAS:/mnt/MainPool/Plex (via NFS)
MEDIA_DIR_MOVIES = r'\\192.168.20.4\Plex\movies'
MEDIA_DIR_TV = r'\\192.168.20.4\Plex\tv'

# Legacy support
DOWNLOAD_DIR = DOWNLOAD_DIR_INCOMPLETE

CONFIG_FILE = os.path.join(BASE_DIR, 'hydra_config.json')

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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
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
