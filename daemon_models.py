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
