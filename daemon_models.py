"""
Pydantic v2 models for Hydra Torrent Daemon REST API.
No imports from other hydra modules — keeps this file importable in any context.
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TransferStatus(str, Enum):
    Queued = "Queued"
    Checking = "Checking"
    Downloading = "Downloading"
    Seeding = "Seeding"
    Paused = "Paused"
    Failed = "Failed"
    Complete = "Complete"


# ---------------------------------------------------------------------------
# Transfer models
# ---------------------------------------------------------------------------

class TransferState(BaseModel):
    """Full snapshot of a single torrent's state."""
    name: str
    magnet: Optional[str] = None
    size: int = 0                   # bytes
    bytes_done: int = 0             # bytes downloaded
    progress: float = 0.0          # 0–100
    speed_down: float = 0.0        # bytes/sec
    speed_up: float = 0.0          # bytes/sec
    eta: str = "N/A"
    peers: int = 0
    seeds: int = 0
    pieces: List[bool] = []        # libtorrent bitfield, may be large
    num_pieces: int = 0
    status: str = "Queued"
    paused: bool = False
    vpn_paused: bool = False
    moved_to_plex: bool = False
    plex_path: Optional[str] = None
    error: Optional[str] = None
    start_time: float = 0.0
    ratio: float = 0.0            # upload / download size (all-time, survives restarts)
    total_uploaded: int = 0       # cumulative bytes uploaded (all sessions)


class AddMagnetRequest(BaseModel):
    magnet: str
    save_path: Optional[str] = None  # override download directory


class SearchRequest(BaseModel):
    query: str
    mode: str = "online"            # "online" | "jackett" | "local"
    server: Optional[str] = None   # required when mode="local"


class SearchResult(BaseModel):
    name: str
    size: int = 0
    seeders: int = 0
    leechers: int = 0
    engine: str = ""
    engine_url: str = ""
    magnet: Optional[str] = None
    published: Optional[str] = None


# ---------------------------------------------------------------------------
# Status models
# ---------------------------------------------------------------------------

class VPNStatus(BaseModel):
    connected: bool
    iface: Optional[str] = None
    vpn_ip: Optional[str] = None
    public_ip: Optional[str] = None  # fetched lazily; may be None


class DaemonStatus(BaseModel):
    running: bool
    vpn: VPNStatus
    total_download_rate: float = 0.0   # bytes/sec
    total_upload_rate: float = 0.0     # bytes/sec
    num_torrents: int = 0


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------

class ConfigResponse(BaseModel):
    plex_url: str = ""
    plex_token: str = ""
    jackett_api_key: str = ""
    indexing_server: str = ""
    media_dir_movies: str = ""
    media_dir_tv: str = ""
    auto_move_to_plex: bool = True
    vpn_required: bool = False
    seed_ratio: float = 0.0           # 0 = seed forever; >0 = auto-remove at ratio
    download_rate_limit: int = 0      # bytes/sec; 0 = unlimited
    upload_rate_limit: int = 0        # bytes/sec; 0 = unlimited
    daemon_api_key: str = ""          # read-only; displayed for tray config copy


class ConfigUpdate(BaseModel):
    plex_url: Optional[str] = None
    plex_token: Optional[str] = None
    jackett_api_key: Optional[str] = None
    indexing_server: Optional[str] = None
    media_dir_movies: Optional[str] = None
    media_dir_tv: Optional[str] = None
    auto_move_to_plex: Optional[bool] = None
    vpn_required: Optional[bool] = None
    seed_ratio: Optional[float] = None
    download_rate_limit: Optional[int] = None   # bytes/sec; 0 = unlimited
    upload_rate_limit: Optional[int] = None     # bytes/sec; 0 = unlimited


# ---------------------------------------------------------------------------
# File priority models
# ---------------------------------------------------------------------------

class TorrentFile(BaseModel):
    """Info about a single file inside a torrent."""
    index: int
    path: str
    size: int
    priority: int   # 0 = skip, 1 = normal, 7 = high


class FilePriorityItem(BaseModel):
    index: int
    priority: int   # 0 = skip, 1 = normal, 7 = high


class SetFilePrioritiesRequest(BaseModel):
    files: List[FilePriorityItem]


# ---------------------------------------------------------------------------
# RSS Auto-Download models
# ---------------------------------------------------------------------------

class RssRule(BaseModel):
    id: str
    name: str
    query: str
    quality: str
    season: Optional[int] = None      # None = any season; 1, 2, … = specific season
    episode_mode: str = "pack"        # "pack" | "episodes" | "any"
    start_episode: Optional[int] = None  # Only add episodes >= this number
    enabled: bool = True
    created: float
    last_checked: float = 0.0
    matched_count: int = 0
    matched_titles: List[str] = []


class AddRssRuleRequest(BaseModel):
    name: str
    quality: str = "1080p"
    query: Optional[str] = None
    season: Optional[int] = None      # None = any season
    episode_mode: str = "pack"        # "pack" | "episodes" | "any"
    start_episode: Optional[int] = None  # Only add episodes >= this number (episodes mode only)


class PatchRssRuleRequest(BaseModel):
    enabled: Optional[bool] = None
    quality: Optional[str] = None
