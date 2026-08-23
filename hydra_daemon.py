#!/usr/bin/env python3
"""
Hydra Torrent Daemon - headless libtorrent engine + FastAPI REST + WebSocket.

Runs independently of peer.pyw (no tkinter required).

Usage:
    python hydra_daemon.py

Bind address / port:
    Set "daemon_host" and "daemon_port" in hydra_config.json.
    Default: 127.0.0.1:8765
    For LAN access: set "daemon_host": "0.0.0.0"

Auth:
    Every request requires the header  X-API-Key: <key>
    The key is auto-generated on first run and stored in hydra_config.json
    under the "daemon_api_key" field.

WebSocket (/ws):
    After connecting, send {"auth": "<key>"} within 5 seconds.
    The server then streams transfer snapshots at ≤10 updates/sec.
"""

import io
import os
import sys
import json
import time
import base64
import platform
import queue
import shutil
import secrets
import logging
import threading
import warnings
import asyncio
import zipfile

from ssrf_guard import ensure_safe_fetch_url as _ensure_safe_fetch_url, trusted_host_from_url as _trusted_host_from_url
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import List, Optional, Set

import libtorrent as lt
import maxminddb
import uvicorn
from fastapi import (
    BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, Security,
    UploadFile, WebSocket, WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.security.api_key import APIKeyHeader
from starlette.status import HTTP_403_FORBIDDEN

from config import (
    BASE_DIR,
    CONFIG_FILE,
    DOWNLOAD_DIR_INCOMPLETE,
    FULLCHAIN_PATH,
    GITHUB_REPO,
    LOG_PATH,
    MEDIA_DIR_MOVIES,
    MEDIA_DIR_TV,
    PEER_PORT,
    PRIVKEY_PATH,
    VERSION,
    load_config,
    save_config,
    logger,
)
from vpn_guard import VPNGuard, get_public_ip as _fetch_public_ip
from media_organizer import auto_move_completed_download
from search import search_jackett, search_index_server
from health import check_jackett, check_plex, check_jellyfin
from updater import check_for_update
from rss_poller import RssPoller
from daemon_models import (
    AddMagnetRequest,
    AddTorrentUrlRequest,
    AddRssRuleRequest,
    CommitPreviewRequest,
    ConfigResponse,
    ConfigUpdate,
    DaemonStatus,
    FilePriorityItem,
    HealthStatus,
    IntegrationHealth,
    PatchRssRuleRequest,
    PeerInfo,
    PreviewTorrentRequest,
    UpdateStatus,
    RssRule,
    SearchRequest,
    SearchResult,
    SetFilePrioritiesRequest,
    TorrentFile,
    TransferState,
    VPNStatus,
)


# lt.peer_info.connection_type values -> display label
_PEER_CONN_TYPE_LABELS = {
    lt.peer_info.standard_bittorrent: 'BT',
    lt.peer_info.web_seed: 'Web Seed',
    lt.peer_info.http_seed: 'HTTP Seed',
}


def _resource_path(name: str) -> str:
    """Resolve a bundled resource (works in both script and PyInstaller onefile)."""
    try:
        base = sys._MEIPASS  # type: ignore[attr-defined]
    except AttributeError:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


# ---------------------------------------------------------------------------
# Tracker list (mirrors peer.pyw)
# ---------------------------------------------------------------------------

_TRACKERS: List[str] = [
    "https://tracker.gbitt.info:443/announce",
    "https://tracker.tamersunion.org:443/announce",
    "http://tracker.opentrackr.org:1337/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://bt1.archive.org:6969/announce",
]

# Fields kept internally but never sent over the API
_INTERNAL_FIELDS = frozenset({'handle', 'prev_bytes', 'prev_time', 'hidden'})

# Path to persisted transfer state (mirrors transfer_manager.py)
_TRANSFERS_PATH = os.path.join(BASE_DIR, 'transfers.json')

# Fast-resume state (libtorrent's per-torrent piece-verification data),
# saved on graceful shutdown so a restart doesn't re-hash every in-progress
# torrent from scratch. Separate from transfers.json (which is UI/API state
# and gets broadcast over the WebSocket) - this is engine-internal only.
_RESUME_DATA_PATH = os.path.join(BASE_DIR, 'resume_data.json')



# ---------------------------------------------------------------------------
# API key auth
# ---------------------------------------------------------------------------

def _get_or_create_api_key() -> str:
    """Read daemon_api_key from config; generate and save one if absent."""
    cfg = load_config()
    key = cfg.get('daemon_api_key')
    if not key:
        key = secrets.token_urlsafe(32)
        save_config('daemon_api_key', key)
        logger.info(f"New daemon API key generated and saved to hydra_config.json")
    return key


API_KEY: str = _get_or_create_api_key()
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)


def _require_api_key(key: str = Security(_api_key_header)) -> str:
    if key != API_KEY:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Invalid API key")
    return key


# ---------------------------------------------------------------------------
# Desktop window show hook
# ---------------------------------------------------------------------------
# hydra_app.py registers _open_callback here once its pywebview window
# exists. Nothing to do in server/headless mode - stays None, and
# /window/show below just no-ops. See the single-instance handling near
# the top of hydra_app.py for why this exists: once the window's been
# hidden to tray, only the process that owns it can actually make it
# reappear (DWM cloaking can't be undone cross-process), so a second,
# short-lived launch needs to ask this already-running process to do it
# from the inside instead of poking the window handle from outside.
_show_window_callback: Optional[callable] = None


def set_show_window_callback(cb) -> None:
    global _show_window_callback
    _show_window_callback = cb


# ---------------------------------------------------------------------------
# DaemonStore
# ---------------------------------------------------------------------------

class DaemonStore:
    """
    Thread-safe transfer state dictionary.

    Replaces TransferManager for the daemon context - no tkinter dependencies.
    Internal engine fields (handle, prev_bytes, prev_time) are stripped before
    API snapshots are built.
    """

    def __init__(self, path: str = None) -> None:
        self._data: dict = {}
        self._lock = threading.Lock()
        self.path = path
        self.ws_queue: queue.SimpleQueue = queue.SimpleQueue()

    # ── Mutations ────────────────────────────────────────────────────────────

    def add(self, name: str, magnet: str, size: int, handle) -> None:
        with self._lock:
            self._data[name] = {
                'name': name,
                'magnet': magnet,
                'size': size,
                'handle': handle,
                'bytes_done': 0,
                'progress': 0.0,
                'speed_down': 0.0,
                'speed_up': 0.0,
                'eta': 'Calculating...',
                'peers': 0,
                'seeds': 0,
                'pieces': [],
                'num_pieces': 0,
                'status': 'Downloading',
                'paused': False,
                'vpn_paused': False,
                'moved_to_plex': False,
                'plex_path': None,
                'moved_files': [],
                'error': None,
                'start_time': time.time(),
                'intended_pause': False,
                'ratio': 0.0,
                'total_uploaded': 0,
                'prev_bytes': 0,
                'prev_time': time.time(),
                'file_priorities_set': False,
            }
        self._push_snapshot()

    def update(self, name: str, **kwargs) -> None:
        with self._lock:
            if name in self._data:
                self._data[name].update(kwargs)
        self._push_snapshot()

    def remove(self, name: str) -> None:
        with self._lock:
            self._data.pop(name, None)
        self._push_snapshot()

    def rename(self, old_name: str, new_name: str) -> None:
        with self._lock:
            entry = self._data.pop(old_name, None)
            if entry:
                entry['name'] = new_name
                self._data[new_name] = entry
        self._push_snapshot()

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[dict]:
        with self._lock:
            entry = self._data.get(name)
            return dict(entry) if entry else None

    def get_handle(self, name: str):
        with self._lock:
            entry = self._data.get(name)
            return entry['handle'] if entry and 'handle' in entry else None

    def all_names(self) -> List[str]:
        with self._lock:
            return list(self._data.keys())

    def snapshot(self, name: Optional[str] = None) -> list:
        """
        Return API-safe list of transfer dicts (handle/internals stripped).
        A single-name lookup always finds it regardless of 'hidden' (used
        by the add-torrent preview dialog while polling for metadata); the
        bulk listing excludes hidden entries, since a torrent that's only
        being previewed and hasn't been committed yet isn't a real transfer.
        """
        with self._lock:
            if name is not None:
                entry = self._data.get(name)
                return [self._clean(entry)] if entry else []
            return [self._clean(v) for v in self._data.values() if not v.get('hidden')]

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        _resumable = {'Seeding', 'Downloading', 'Paused', 'Waiting for VPN', 'Fetching metadata...'}
        with self._lock:
            data = {}
            for k, v in self._data.items():
                # A hidden (preview, not yet committed) entry must never survive
                # a restart: if the daemon restarted mid-preview, _resume_persisted
                # would otherwise reattach and reveal a torrent the user never
                # actually confirmed adding.
                if v.get('status') in _resumable and not v.get('hidden'):
                    data[k] = self._clean(v)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved {len(data)} transfer(s) to {path}")
        except Exception as e:
            logger.error(f"DaemonStore.save failed: {e}")

    def load(self, path: str) -> None:
        if not os.path.exists(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            with self._lock:
                for k, v in data.items():
                    v.setdefault('prev_bytes', 0)
                    v.setdefault('prev_time', time.time())
                    v.setdefault('intended_pause', False)
                    v.setdefault('vpn_paused', False)
                    v.setdefault('moved_to_plex', False)
                    v.setdefault('plex_path', None)
                    v.setdefault('moved_files', [])
                    v.setdefault('error', None)
                    v.setdefault('ratio', 0.0)
                    v.setdefault('total_uploaded', 0)
                    self._data[k] = v
            logger.info(f"Loaded {len(data)} transfer(s) from {path}")
        except Exception as e:
            logger.error(f"DaemonStore.load failed: {e}")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _clean(self, entry: dict) -> dict:
        return {k: v for k, v in entry.items() if k not in _INTERNAL_FIELDS}

    def _push_snapshot(self) -> None:
        try:
            self.ws_queue.put_nowait(self.snapshot())
            if self.path:
                try:
                    self.save(self.path)
                except Exception:
                    pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Distinguishing a real Plex/Jellyfin-organized move from a fallback one
# ---------------------------------------------------------------------------

def _is_under_library_root(dest_dir: str) -> bool:
    """
    True only if dest_dir genuinely landed inside MEDIA_DIR_MOVIES or
    MEDIA_DIR_TV - i.e. this was a real, library-organized move, not the
    general-downloads fallback auto_move_completed_download() also
    reports success for (no library configured, path didn't exist, or
    not a video file). Those two cases return the exact same 3-value
    (success, dest_path, error) shape, so the caller can't tell them
    apart without this check - confirmed live: without it, the UI
    unconditionally labeled every successful move as "-> Plex: <path>",
    which is a false claim whenever the fallback path was actually used.
    """
    real_dest = os.path.realpath(dest_dir)
    return any(
        root and real_dest.startswith(os.path.realpath(root) + os.sep)
        for root in (MEDIA_DIR_MOVIES, MEDIA_DIR_TV)
    )


# ---------------------------------------------------------------------------
# Trusted-fetch hosts for the SSRF guard
# ---------------------------------------------------------------------------

def _indexer_trusted_hosts() -> set:
    """
    The user's own configured Jackett server, as a set ssrf_guard's
    trusted_hosts accepts. Every search-result "fetch this torrent" URL
    comes from Jackett itself (see preview_torrent_url / add_torrent_from_url),
    so it's the same already-trusted-input case search.py's search_jackett()
    already gets a pass on - not an arbitrary URL, just usually a private
    LAN address, which is the normal, expected case for a self-hosted setup.
    """
    host = _trusted_host_from_url(load_config().get('indexing_server', ''))
    return {host} if host else set()


# ---------------------------------------------------------------------------
# TorrentEngine
# ---------------------------------------------------------------------------

class TorrentEngine:
    """
    Headless libtorrent engine extracted from peer.pyw.

    All status_text.insert() → logger.*
    All self.root.after()    → direct calls (no main-thread requirement)
    """

    def __init__(self, store: DaemonStore) -> None:
        self.store = store
        self.ses: Optional[lt.session] = None
        self.vpn_guard = VPNGuard()
        self.vpn_ip: Optional[str] = None
        self.geo_reader = None
        try:
            self.geo_reader = maxminddb.open_database(_resource_path('geoip-country.mmdb'))
        except Exception as e:
            logger.warning(f"GeoIP DB load failed: {e} - peer country flags disabled")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Detect VPN → create session → load persisted state → start monitor."""
        connected, iface, ip = self.vpn_guard.get_status()
        self.vpn_ip = ip if connected else None

        vpn_required = load_config().get('vpn_required', False)
        if connected:
            logger.info(f"VPN connected: {iface} ({ip})")
        elif vpn_required:
            logger.warning("No VPN detected at startup - kill switch active, binding to 127.0.0.1")
        else:
            logger.info("No VPN detected at startup - kill switch off, binding to 0.0.0.0")

        # Kill switch: bind to loopback so no peers can connect without VPN
        listen_ip = self.vpn_ip or ('127.0.0.1' if vpn_required else '0.0.0.0')
        self._create_session(listen_ip)
        self.store.load(_TRANSFERS_PATH)

        # Mark all non-intentionally-paused transfers as vpn_paused at startup
        if vpn_required and not connected:
            for name in self.store.all_names():
                t = self.store.get(name)
                if t and not t.get('intended_pause', False):
                    self.store.update(name, vpn_paused=True, paused=True)

        self._resume_persisted()
        self.vpn_guard.start(self._on_vpn_change)
        logger.info(f"TorrentEngine ready on {listen_ip}:{PEER_PORT}")

    def _refresh_public_ip_sync(self) -> None:
        """Blocking fetch of public IP - run in a thread after VPN reconnects."""
        global _public_ip, _public_ip_ts
        try:
            time.sleep(2)  # brief delay for VPN tunnel to stabilise
            ip = _fetch_public_ip(source_ip=self.vpn_ip)
            if ip:
                _public_ip = ip
                _public_ip_ts = time.time()
        except Exception:
            pass

    def shutdown(self) -> None:
        logger.info("TorrentEngine shutting down...")
        self._save_resume_data()
        self.store.save(_TRANSFERS_PATH)
        self.vpn_guard.stop()
        logger.info("TorrentEngine stopped.")

    # ── Session ───────────────────────────────────────────────────────────────

    def _create_session(self, listen_ip: str) -> None:
        warnings.filterwarnings("ignore", category=DeprecationWarning, module="libtorrent")
        cfg = load_config()
        self.ses = lt.session({
            'listen_interfaces': f'{listen_ip}:{PEER_PORT}',
            'outgoing_interfaces': listen_ip,
            'enable_dht': True,
            'enable_lsd': False,       # No LAN broadcast
            'enable_upnp': False,      # Disabled - useless behind VPN, leaks real IP to router
            'enable_natpmp': False,    # Disabled - same reason
            'anonymous_mode': False,   # Allow full tracker/peer communication
            'connections_limit': 200,
            'download_rate_limit': int(cfg.get('download_rate_limit', 0) or 0),
            'upload_rate_limit': int(cfg.get('upload_rate_limit', 0) or 0),
            # Identify Hydra to the swarm instead of hiding behind libtorrent's
            # default fingerprint - lets the peer list recognize fellow Hydra
            # users instead of showing every peer as an anonymous IP.
            'peer_fingerprint': f'-HY{VERSION.replace(".", "")[:4].ljust(4, "0")}-',
            'user_agent': f'HydraTorrent/{VERSION}',
            'handshake_client_version': f'HydraTorrent/{VERSION}',
            'always_send_user_agent': True,
            # storage_notification is what save_resume_data_alert belongs
            # to - it's NOT in libtorrent's default alert_mask, so without
            # this the alert is silently dropped and save_resume_data()
            # looks like it works but resume data is never actually
            # collected. error_notification kept too so a failed save still
            # surfaces instead of just timing out silently.
            'alert_mask': int(lt.alert.category_t.storage_notification | lt.alert.category_t.error_notification),
        })
        self.ses.add_dht_router("router.utorrent.com", 6881)
        self.ses.add_dht_router("router.bittorrent.com", 6881)
        self.ses.add_dht_router("dht.transmissionbt.com", 6881)

    def apply_rate_limits(self, download_limit: int, upload_limit: int) -> None:
        """Live-apply bandwidth limits to the running session (0 = unlimited)."""
        if self.ses:
            self.ses.apply_settings({
                'download_rate_limit': download_limit,
                'upload_rate_limit': upload_limit,
            })
            logger.info(f"Rate limits updated: DL {download_limit} B/s, UL {upload_limit} B/s")

    def get_session_status(self) -> dict:
        if not self.ses:
            return {}
        try:
            s = self.ses.status()
            return {
                'download_rate': s.download_rate,
                'upload_rate': s.upload_rate,
                'num_peers': s.num_peers,
                'total_download': s.total_download,
                'total_upload': s.total_upload,
            }
        except Exception:
            return {}

    def _country_for_ip(self, ip: str):
        """Offline GeoIP lookup. Returns (country_name, iso_code), ('Unknown', '') on any failure."""
        if not self.geo_reader or not ip:
            return ('Unknown', '')
        try:
            result = self.geo_reader.get(ip)
            if result and 'country' in result:
                name = result['country'].get('names', {}).get('en', 'Unknown')
                code = result['country'].get('iso_code', '')
                return (name, code)
        except Exception:
            pass
        return ('Unknown', '')

    def get_peer_info(self, name: str) -> list:
        """
        Live peer list for one transfer: IP, country, client, per-peer speed,
        and whether that peer is also running Hydra (its client string will
        contain "Hydra" since we set our own user_agent/handshake version to
        that, and so does every other Hydra install).
        """
        handle = self.store.get_handle(name)
        if handle is None:
            return []
        try:
            peers = handle.get_peer_info()
        except Exception:
            return []

        result = []
        for p in peers:
            ip = p.ip[0] if p.ip else ''
            port = p.ip[1] if p.ip else 0
            country, code = self._country_for_ip(ip)
            raw_client = p.client
            if isinstance(raw_client, bytes):
                raw_client = raw_client.decode('utf-8', errors='replace')
            client = raw_client or 'Unknown'
            conn_type = _PEER_CONN_TYPE_LABELS.get(p.connection_type, 'BT')
            result.append({
                'ip': ip,
                'port': port,
                'country': country,
                'country_code': code,
                'client': client,
                'connection_type': conn_type,
                'down_speed': float(p.down_speed),
                'up_speed': float(p.up_speed),
                'total_downloaded': int(p.total_download),
                'total_uploaded': int(p.total_upload),
                'progress': round(p.progress * 100, 1),
                'is_hydra': 'hydra' in client.lower(),
            })
        result.sort(key=lambda x: -(x['down_speed'] + x['up_speed']))
        return result

    # ── VPN kill switch ────────────────────────────────────────────────────────

    def _on_vpn_change(self, connected: bool, iface: Optional[str], ip: Optional[str]) -> None:
        global _public_ip
        if not connected:
            if not load_config().get('vpn_required', False):
                logger.info("VPN disconnected - kill switch is off, rebinding to 0.0.0.0")
                self.vpn_ip = None
                _public_ip = None
                try:
                    self.ses.apply_settings({'listen_interfaces': f'0.0.0.0:{PEER_PORT}', 'outgoing_interfaces': ''})
                except Exception as e:
                    logger.error(f"Failed to rebind on VPN drop: {e}")
                return
            logger.warning("VPN DISCONNECTED - kill switch activated")
            try:
                self.ses.apply_settings({'listen_interfaces': f'127.0.0.1:{PEER_PORT}', 'outgoing_interfaces': '127.0.0.1'})
            except Exception as e:
                logger.error(f"Failed to rebind session on VPN drop: {e}")

            for name in self.store.all_names():
                t = self.store.get(name)
                handle = self.store.get_handle(name)
                if handle and t and not t.get('intended_pause', False):
                    try:
                        handle.pause()
                        self.store.update(name, vpn_paused=True, paused=True)
                    except Exception:
                        pass

            self.vpn_ip = None
            _public_ip = None
        else:
            logger.info(f"VPN reconnected ({ip}) - kill switch deactivated, rebinding session")
            self.vpn_ip = ip
            _public_ip = None
            threading.Thread(target=self._refresh_public_ip_sync, daemon=True).start()
            try:
                self.ses.apply_settings({'listen_interfaces': f'{ip}:{PEER_PORT}', 'outgoing_interfaces': ip})
            except Exception as e:
                logger.error(f"Failed to rebind session on VPN reconnect: {e}")

            for name in self.store.all_names():
                t = self.store.get(name)
                handle = self.store.get_handle(name)
                if handle and t and t.get('vpn_paused', False):
                    try:
                        handle.resume()
                        self.store.update(name, vpn_paused=False, paused=False)
                    except Exception:
                        pass

    # ── Add torrent ───────────────────────────────────────────────────────────

    def add_magnet(self, magnet_uri: str, save_path: Optional[str] = None) -> str:
        """
        Add a magnet link and wait for metadata (≤120 s).

        Returns the real torrent name.
        Raises TimeoutError if metadata is not received within 120 seconds.
        Raises RuntimeError if the libtorrent session is not yet ready.
        """
        if self.ses is None:
            raise RuntimeError("libtorrent session not ready yet - try again shortly")

        if save_path is None:
            save_path = DOWNLOAD_DIR_INCOMPLETE

        vpn_required = load_config().get('vpn_required', False)

        params = {
            'url': magnet_uri,
            'save_path': save_path,
            'storage_mode': lt.storage_mode_t(2),
        }
        handle = lt.add_magnet_uri(self.ses, magnet_uri, params)

        # Extract display name from magnet URI for immediate display
        import urllib.parse
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(magnet_uri).query)
        placeholder = (parsed.get('dn', [''])[0]
                       or parsed.get('xt', [''])[0].split(':')[-1][:16]
                       or 'unknown_torrent')

        # If kill switch is on and VPN is down, add paused immediately
        if vpn_required and not self.vpn_ip:
            handle.pause()
            self.store.add(placeholder, magnet_uri, 0, handle)
            self.store.update(placeholder, vpn_paused=True, paused=True, status='Waiting for VPN')
            logger.warning(f"'{placeholder}' added paused - kill switch on, no VPN")
            threading.Thread(
                target=self._monitor_download,
                args=(placeholder, handle, 0),
                daemon=True,
                name=f"dl-{placeholder[:24]}",
            ).start()
            return placeholder

        # Add to store immediately so it shows in the UI
        self.store.add(placeholder, magnet_uri, 0, handle)
        self.store.update(placeholder, status='Fetching metadata...')
        logger.info(f"Added magnet '{placeholder}' → fetching metadata...")

        start_wait = time.time()
        while not handle.has_metadata():
            if time.time() - start_wait > 600:  # 10 minute timeout instead of 2 minutes
                # Mark as timeout instead of deleting
                self.store.update(placeholder, status='Metadata timeout', error='No peers responded after 10 minutes')
                logger.warning(f"'{placeholder}' metadata timeout after 10 minutes")
                return placeholder  # Return anyway so UI shows it
            time.sleep(0.5)

        ti = handle.get_torrent_info()
        total_size = ti.total_size()
        real_name = ti.name() or "unnamed_torrent"
        logger.info(f"Metadata OK → '{real_name}' ({total_size:,} bytes)")

        for tracker in _TRACKERS:
            handle.add_tracker({'url': tracker})
        handle.force_reannounce()

        # Update the placeholder entry with real metadata
        if real_name != placeholder:
            self.store.rename(placeholder, real_name)
        self.store.update(real_name, size=total_size, status='Downloading')

        threading.Thread(
            target=self._monitor_download,
            args=(real_name, handle, total_size),
            daemon=True,
            name=f"dl-{real_name[:24]}",
        ).start()
        return real_name

    def add_torrent_file(self, data: bytes, filename: str) -> str:
        """
        Add a torrent from raw .torrent file bytes.

        Returns the real torrent name.
        Raises RuntimeError if the libtorrent session is not yet ready.
        """
        if self.ses is None:
            raise RuntimeError("libtorrent session not ready yet - try again shortly")

        try:
            ti = lt.torrent_info(data)
        except Exception:
            # Older libtorrent versions require explicit bdecode first
            ti = lt.torrent_info(lt.bdecode(data))

        real_name = ti.name() or os.path.splitext(filename)[0] or "unnamed_torrent"
        total_size = ti.total_size()
        logger.info(f"Torrent file → '{real_name}' ({total_size:,} bytes)")

        params = lt.add_torrent_params()
        params.ti = ti
        params.save_path = DOWNLOAD_DIR_INCOMPLETE
        handle = self.ses.add_torrent(params)

        for tracker in _TRACKERS:
            handle.add_tracker({'url': tracker})
        handle.force_reannounce()

        self.store.add(real_name, '', total_size, handle)
        threading.Thread(
            target=self._monitor_download,
            args=(real_name, handle, total_size),
            daemon=True,
            name=f"dl-{real_name[:24]}",
        ).start()
        return real_name

    # ── Add-torrent preview (metadata + file picker before committing) ─────────
    #
    # Powers the Search-tab "double-click a result" dialog: the torrent is
    # added paused and marked hidden, so it never shows up in the Transfers
    # list or gets persisted across a restart, but the frontend can still
    # poll it by name and call the existing files endpoints against it to
    # show real size/file-list metadata and let the user pick what to grab.
    # commit_preview() turns it into a real transfer; DELETE (same endpoint
    # as any other transfer) discards it with nothing ever downloaded.

    def _unique_store_name(self, base: str) -> str:
        name = base
        n = 2
        while self.store.get(name) is not None:
            name = f"{base} ({n})"
            n += 1
        return name

    def preview_magnet(self, magnet_uri: str, save_path: Optional[str] = None) -> str:
        if self.ses is None:
            raise RuntimeError("libtorrent session not ready yet - try again shortly")
        if save_path is None:
            save_path = DOWNLOAD_DIR_INCOMPLETE

        # handle.pause() called right after add used to be relied on to
        # keep a preview from downloading real data, but it doesn't
        # reliably hold - the torrent isn't fully registered in the
        # session yet at that exact call, and once metadata arrives
        # auto_managed silently overrides the pause and downloading
        # starts anyway (confirmed live: real file bytes on disk within
        # seconds of opening a preview, before ever clicking commit).
        # default_dont_download is the correct tool for this: peers
        # still connect and metadata still exchanges normally (fast -
        # it's a small handshake, not the file itself), but every file's
        # priority defaults to 0 so the piece picker never requests
        # actual data. commit_preview is what turns real priorities on.
        params = lt.parse_magnet_uri(magnet_uri)
        params.save_path = save_path
        params.storage_mode = lt.storage_mode_t(2)
        params.flags = (lt.torrent_flags.default_flags
                         | lt.torrent_flags.default_dont_download) & ~lt.torrent_flags.paused
        handle = self.ses.add_torrent(params)

        import urllib.parse
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(magnet_uri).query)
        placeholder = self._unique_store_name(
            parsed.get('dn', [''])[0]
            or parsed.get('xt', [''])[0].split(':')[-1][:16]
            or 'unknown_torrent'
        )

        self.store.add(placeholder, magnet_uri, 0, handle)
        self.store.update(placeholder, hidden=True, paused=True,
                           status='Fetching metadata...', save_path=save_path)
        logger.info(f"Preview '{placeholder}' → fetching metadata...")

        def _wait() -> None:
            start = time.time()
            while not handle.has_metadata():
                if self.store.get(placeholder) is None:
                    return  # cancelled while waiting
                if time.time() - start > 90:
                    self.store.update(placeholder, status='Metadata timeout',
                                       error='No peers responded')
                    return
                time.sleep(0.5)
            if self.store.get(placeholder) is None:
                return  # cancelled right as metadata arrived
            ti = handle.get_torrent_info()
            self.store.update(placeholder, size=ti.total_size(), status='Metadata ready')
            logger.info(f"Preview '{placeholder}' metadata ready ({ti.total_size():,} bytes)")

        threading.Thread(target=_wait, daemon=True, name=f"preview-{placeholder[:24]}").start()
        return placeholder

    def preview_torrent_url(self, url: str, save_path: Optional[str] = None) -> str:
        import requests as _requests

        trusted = _indexer_trusted_hosts()
        _ensure_safe_fetch_url(url, trusted)
        resp = _requests.get(url, timeout=30, allow_redirects=False)
        hops = 0
        while resp.is_redirect and hops < 6:
            loc = resp.headers.get('Location', '')
            if loc.startswith('magnet:'):
                return self.preview_magnet(loc, save_path)
            _ensure_safe_fetch_url(loc, trusted)
            resp = _requests.get(loc, timeout=30, allow_redirects=False)
            hops += 1
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to fetch torrent URL: HTTP {resp.status_code}")

        data = resp.content
        if data[:7] == b'magnet:':
            return self.preview_magnet(data.decode('utf-8', 'ignore').strip(), save_path)

        if self.ses is None:
            raise RuntimeError("libtorrent session not ready yet - try again shortly")
        try:
            ti = lt.torrent_info(data)
        except Exception:
            ti = lt.torrent_info(lt.bdecode(data))

        if save_path is None:
            save_path = DOWNLOAD_DIR_INCOMPLETE

        params = lt.add_torrent_params()
        params.ti = ti
        params.save_path = save_path
        params.storage_mode = lt.storage_mode_t(2)
        # See preview_magnet's comment - pause() after add doesn't reliably
        # hold, default_dont_download is what actually prevents real data
        # from downloading during preview.
        params.flags = (lt.torrent_flags.default_flags
                         | lt.torrent_flags.default_dont_download) & ~lt.torrent_flags.paused
        handle = self.ses.add_torrent(params)

        real_name = self._unique_store_name(ti.name() or "unnamed_torrent")
        self.store.add(real_name, None, ti.total_size(), handle)
        self.store.update(real_name, hidden=True, paused=True,
                           status='Metadata ready', save_path=save_path)
        logger.info(f"Preview '{real_name}' metadata ready from URL ({ti.total_size():,} bytes)")
        return real_name

    def commit_preview(self, name: str, save_path: Optional[str] = None) -> str:
        """Turn a previewed (hidden, paused) torrent into a real, visible,
        downloading transfer. File priorities are expected to already be
        set via POST /transfers/{name}/files before this is called."""
        entry = self.store.get(name)
        if entry is None:
            raise KeyError(name)
        handle = entry.get('handle')
        if handle is None or not handle.has_metadata():
            raise RuntimeError("metadata not ready yet")

        # Every file sits at priority 0 for the whole preview (that's
        # what stops real data downloading before commit - see
        # preview_magnet). The frontend syncs the user's actual file
        # selection here via POST /files right before calling this, but
        # that's a separate call this one shouldn't have to trust blindly
        # - if it was skipped, or never happened for any reason, every
        # file staying at 0 would otherwise make libtorrent consider
        # this "instantly finished" with nothing ever downloaded,
        # silently. If literally nothing was ever explicitly set (the
        # common case - most people never touch file selection), default
        # to downloading everything instead.
        #
        # This checks our own file_priorities_set flag rather than
        # re-reading handle.file_priorities() back from libtorrent -
        # prioritize_files() is a fire-and-forget async call into the
        # session engine, and re-querying it here used to race in
        # practice: the frontend's /files call would return successfully,
        # but libtorrent hadn't necessarily applied the change internally
        # yet by the time this ran a moment later, so it looked
        # indistinguishable from "nothing was ever set" and silently
        # turned every file back on - confirmed live, a user's real file
        # exclusions got overridden this way. Our own flag update is
        # synchronous and race-free; libtorrent's isn't.
        if not entry.get('file_priorities_set'):
            try:
                priorities = list(handle.file_priorities())
                if priorities and not any(priorities):
                    handle.prioritize_files([1] * len(priorities))
            except Exception:
                pass

        if save_path and save_path != entry.get('save_path'):
            try:
                handle.move_storage(save_path)
            except Exception as e:
                logger.warning(f"move_storage failed for '{name}': {e}")

        ti = handle.get_torrent_info()
        real_name = ti.name() or name
        total_size = ti.total_size()

        for tracker in _TRACKERS:
            handle.add_tracker({'url': tracker})
        handle.force_reannounce()

        if real_name != name:
            real_name = self._unique_store_name(real_name) if self.store.get(real_name) else real_name
            self.store.rename(name, real_name)

        vpn_required = load_config().get('vpn_required', False)
        if vpn_required and not self.vpn_ip:
            handle.pause()
            self.store.update(real_name, hidden=False, size=total_size,
                               vpn_paused=True, paused=True, status='Waiting for VPN')
            logger.info(f"Committed preview '{real_name}' - paused, kill switch on, no VPN")
        else:
            handle.resume()
            self.store.update(real_name, hidden=False, size=total_size,
                               paused=False, status='Downloading')
            logger.info(f"Committed preview → downloading '{real_name}'")

        threading.Thread(
            target=self._monitor_download,
            args=(real_name, handle, total_size),
            daemon=True,
            name=f"dl-{real_name[:24]}",
        ).start()
        return real_name

    # ── Download monitor ──────────────────────────────────────────────────────

    def _monitor_download(self, name: str, handle, total_size: int) -> None:
        """2-second poll loop - updates store stats, detects completion."""
        _state_map = {
            0: 'Queued', 1: 'Checking', 2: 'DL Metadata',
            3: 'Downloading', 4: 'Finished', 5: 'Seeding',
            6: 'Allocating', 7: 'Resuming',
        }

        first_pass = True
        while True:
            t = self.store.get(name)
            if t is None:
                break

            try:
                s = handle.status()
            except Exception as e:
                logger.error(f"Handle error for '{name}': {e}")
                self.store.update(name, status='Failed', error=str(e))
                break

            if s.error:
                err_msg = str(s.error)
                logger.error(f"Torrent error for '{name}': {err_msg}")
                self.store.update(name, status='Failed', error=err_msg)
                try:
                    self.ses.remove_torrent(handle)
                except Exception:
                    pass
                break

            # Honour intended pause
            if t.get('intended_pause', False):
                if not s.paused:
                    handle.pause()
                self.store.update(name,
                    status='Paused', paused=True,
                    speed_down=0.0, speed_up=0.0, eta='Paused',
                    pieces=list(s.pieces) if s.pieces else [],
                    num_pieces=s.num_pieces,
                )
                time.sleep(2)
                continue
            else:
                if s.paused and not t.get('vpn_paused', False):
                    handle.resume()

            # Stats
            current_time = time.time()
            prev_bytes = t.get('prev_bytes', 0)
            prev_time = t.get('prev_time', current_time)
            delta_bytes = s.total_done - prev_bytes
            delta_time = current_time - prev_time
            # A too-small delta_time turns even a modest delta_bytes into a
            # physically-impossible speed reading - confirmed live: spikes
            # of 100+ MB/s from a swarm whose real peers never individually
            # exceeded a few KB/s. Below this floor, just keep reporting
            # the last known speed instead of computing a fresh, spurious
            # one from an interval too short to mean anything.
            if delta_time >= 0.5:
                speed_down = delta_bytes / delta_time
            else:
                speed_down = t.get('speed_down', 0.0)

            eta = 'N/A'
            if speed_down > 0 and total_size > s.total_done:
                eta_sec = (total_size - s.total_done) / speed_down
                eta = f"{int(eta_sec // 60)}m {int(eta_sec % 60)}s"

            progress = s.progress * 100
            status = _state_map.get(s.state, 'Unknown')
            if s.is_seeding or s.is_finished:
                status = 'Seeding'

            self.store.update(name,
                bytes_done=s.total_done,
                progress=progress,
                speed_down=speed_down,
                speed_up=float(s.upload_rate),
                eta=eta,
                peers=s.num_peers,
                seeds=s.num_seeds,
                pieces=list(s.pieces) if s.pieces else [],
                num_pieces=s.num_pieces,
                status=status,
                paused=bool(s.paused),
                prev_bytes=s.total_done,
                prev_time=current_time,
            )

            # Never trust a completion signal on the very first pass:
            # commit_preview()'s own fallback prioritize_files() call (for
            # "file selection was never touched, default to everything")
            # is itself fire-and-forget async into libtorrent - it may not
            # have landed internally yet by the time this thread's first
            # iteration runs a moment later. A torrent whose real data
            # already sits on disk matching real hashes can also
            # legitimately read total_done==0 on this exact first pass,
            # simply because the on-disk recheck hasn't finished yet -
            # so first_pass has to skip the whole completion decision
            # (not just the total_done check below), success and failure
            # alike, and let a real 2-second cycle pass before either one
            # is trusted.
            if (s.progress >= 1.0 or s.is_seeding or s.is_finished) and not first_pass:
                # libtorrent's is_finished/is_seeding are true whenever
                # nothing is left that's wanted - that includes the
                # degenerate case where every file ended up at priority 0
                # (e.g. file_priorities_set got flipped true - see
                # set_file_priorities - without any file actually landing
                # on a nonzero priority). Confirmed live: a fresh torrent
                # with real peers and real metadata "completed" in
                # milliseconds with s.total_done at 0 and every entry in
                # s.pieces false, because nothing was ever actually
                # selected to download and libtorrent correctly considers
                # that finished. Trusting is_finished alone there turned
                # "nothing was downloaded" into a lying "100% complete" -
                # total_done is part of the same status() snapshot every
                # other decision in this loop already trusts (no extra,
                # separately-racy libtorrent call needed), and a torrent
                # that's genuinely finished always has total_done > 0.
                if s.total_done <= 0:
                    logger.error(f"'{name}' reported finished with zero files selected - nothing was downloaded")
                    self.store.update(
                        name, status='Failed',
                        error='No files were selected for download',
                    )
                    try:
                        self.ses.remove_torrent(handle)
                    except Exception:
                        pass
                    break

                logger.info(f"Download complete: '{name}'")
                # bytes_done/speed_down are only ever written by the loop
                # above (from a real handle.status() snapshot each pass) -
                # _monitor_seeding(), which takes over right after this,
                # never touches them at all (see its own update calls -
                # upload stats only). Left alone, they freeze forever at
                # whatever they happened to read on this exact final
                # iteration - confirmed live: a completed, actively-
                # seeding torrent kept showing "100% progress" next to a
                # bytes_done reading far below the real file size, because
                # s.total_done can genuinely lag s.progress/is_seeding by
                # a beat inside libtorrent's own status update cycle, and
                # nothing ever corrected it afterward. The torrent is
                # complete at this point by definition, so bytes_done and
                # speed_down have exactly one correct value each now.
                self.store.update(
                    name, status='Seeding', progress=100.0, eta='Seeding',
                    bytes_done=total_size, speed_down=0.0,
                )
                threading.Thread(
                    target=self._post_download,
                    args=(name, handle),
                    daemon=True,
                    name=f"plex-{name[:20]}",
                ).start()
                self._monitor_seeding(name, handle)
                break

            first_pass = False
            time.sleep(2)

    # ── Post-download Plex move ───────────────────────────────────────────────

    def _post_download(self, name: str, handle) -> None:
        """Move completed files to Plex/media folders when auto-move is enabled."""
        cfg = load_config()
        if not cfg.get('auto_move_to_plex', True):
            return

        try:
            ti = handle.get_torrent_info()
            files = ti.files()
        except Exception as e:
            logger.error(f"Cannot get torrent info for Plex move of '{name}': {e}")
            return

        # media_organizer moves files with a plain filesystem move, not
        # handle.move_storage() - libtorrent's own bookkeeping still
        # thinks everything lives at the original download path, so its
        # delete_files flag becomes a no-op against the real (moved)
        # location. Tracking the exact destination of every file we move
        # is what lets remove()'s delete-files path actually clean up
        # for real, instead of reporting success while quietly leaving
        # the media behind.
        moved_files = list(self.store.get(name).get('moved_files') or [])
        # A torrent's own file list is attacker-controlled (a malicious
        # .torrent/magnet can declare a path like '../../../etc/passwd').
        # libtorrent sanitizes this itself before writing to disk, but
        # /static/{filename} already sets the precedent elsewhere in this
        # file of not taking that on faith - realpath + an explicit
        # "still under the download dir" check costs nothing and doesn't
        # rely on a guarantee from code Hydra doesn't own.
        # The actual save path can be a per-torrent choice (network share,
        # SAVE AT override, move_storage() after commit) that has nothing
        # to do with the module-level DOWNLOAD_DIR_INCOMPLETE default -
        # confirmed live: a torrent saved to a configured network share
        # never got picked up here because this used to join file paths
        # against DOWNLOAD_DIR_INCOMPLETE regardless of where the torrent
        # actually lived, so os.path.exists() below always failed and the
        # move silently never happened. handle.status().save_path is
        # libtorrent's own live answer, not a value Hydra has to keep in
        # sync itself.
        try:
            actual_save_path = handle.status().save_path
        except Exception:
            actual_save_path = DOWNLOAD_DIR_INCOMPLETE
        incomplete_dir = os.path.realpath(actual_save_path)
        for file_info in files:
            file_path = os.path.realpath(os.path.join(actual_save_path, file_info.path))
            if not file_path.startswith(incomplete_dir + os.sep):
                logger.warning(f"Skipping file outside download dir for '{name}': {file_info.path!r}")
                continue
            if not os.path.exists(file_path):
                continue

            success, dest_path, error = auto_move_completed_download(
                os.path.basename(file_path), file_path, torrent_name=name
            )
            if success:
                plex_dir = os.path.dirname(dest_path)
                moved_files.append(dest_path)
                really_organized = _is_under_library_root(plex_dir)
                self.store.update(
                    name, moved_to_plex=really_organized, plex_path=plex_dir, moved_files=moved_files,
                )
                logger.info(f"{'Moved to Plex' if really_organized else 'Moved'}: {dest_path}")
            elif error and error not in ("Not a video file", "Library path not configured"):
                logger.warning(f"Could not move '{name}': {error}")

    # ── Seeding monitor ────────────────────────────────────────────────────────

    def _monitor_seeding(self, name: str, handle) -> None:
        """Lightweight upload-stats monitor while a torrent is seeding.

        Tracks cumulative upload across daemon restarts via total_uploaded offset.
        Checks seed_ratio from config each loop; auto-removes when target is hit.
        """
        _state_map = {
            0: 'Queued', 1: 'Checking', 2: 'DL Metadata',
            3: 'Downloading', 4: 'Finished', 5: 'Seeding',
            6: 'Allocating', 7: 'Resuming',
        }

        # Snapshot state at seeding start
        t0 = self.store.get(name)
        total_size = t0.get('size', 0) if t0 else 0
        # Bytes already uploaded in previous daemon sessions (persisted in transfers.json)
        upload_offset = t0.get('total_uploaded', 0) if t0 else 0

        while True:
            t = self.store.get(name)
            if t is None:
                break

            try:
                s = handle.status()
            except Exception:
                break

            if s.error:
                # _monitor_download already catches this (disk full,
                # permission denied, a file deleted out from under it,
                # etc.) - the seeding loop never checked at all, so the
                # exact same failure post-completion (e.g. someone deletes
                # the files, or the disk goes read-only, while still
                # seeding) previously just kept reporting stats forever
                # instead of surfacing anything.
                err_msg = str(s.error)
                logger.error(f"Torrent error for '{name}' while seeding: {err_msg}")
                self.store.update(name, status='Failed', error=err_msg)
                try:
                    self.ses.remove_torrent(handle)
                except Exception:
                    pass
                break

            current_time = time.time()
            prev_bytes = t.get('prev_bytes', 0)
            prev_time = t.get('prev_time', current_time)
            delta_bytes = s.total_upload - prev_bytes
            delta_time = current_time - prev_time
            # Same guard as _monitor_download's speed_down - a too-small
            # delta_time produces a spurious spike, not a real reading.
            if delta_time >= 0.5:
                speed_up = delta_bytes / delta_time
            else:
                speed_up = t.get('speed_up', float(s.upload_rate))

            # Cumulative upload = previous sessions + this session
            all_time_upload = upload_offset + s.total_upload
            ratio = all_time_upload / total_size if total_size > 0 else 0.0

            if t.get('intended_pause', False):
                if not s.paused:
                    handle.pause()
                self.store.update(name,
                    status='Paused', paused=True, speed_up=0.0,
                    ratio=ratio, total_uploaded=all_time_upload,
                    prev_bytes=s.total_upload, prev_time=current_time,
                    # Defense-in-depth alongside the one-time fix where
                    # seeding starts: a seeding torrent is complete by
                    # definition, every single iteration, not just the
                    # first one - keep asserting that rather than ever
                    # leaving it to whatever an earlier snapshot said.
                    bytes_done=total_size, speed_down=0.0,
                )
            else:
                if s.paused and not t.get('vpn_paused', False):
                    handle.resume()
                self.store.update(name,
                    status=_state_map.get(s.state, 'Seeding'),
                    speed_up=speed_up,
                    peers=s.num_peers,
                    seeds=s.num_seeds,
                    paused=bool(s.paused),
                    ratio=ratio,
                    total_uploaded=all_time_upload,
                    prev_bytes=s.total_upload,
                    prev_time=current_time,
                    bytes_done=total_size, speed_down=0.0,
                )

            # Auto-remove when seed ratio target is reached
            seed_ratio = float(load_config().get('seed_ratio', 0) or 0)
            if seed_ratio > 0 and ratio >= seed_ratio:
                logger.info(
                    f"'{name}' reached seed ratio {ratio:.2f} "
                    f"(target {seed_ratio}) - auto-removing"
                )
                self.remove(name, delete_files=False)
                break

            time.sleep(5)

    # ── Pause / resume / remove ───────────────────────────────────────────────

    def pause(self, name: str) -> None:
        handle = self.store.get_handle(name)
        if handle:
            self.store.update(name, intended_pause=True)
            try:
                handle.pause()
            except Exception:
                pass
            self.store.update(name, status='Paused', paused=True)

    def resume(self, name: str) -> None:
        handle = self.store.get_handle(name)
        if not handle:
            return
        self.store.update(name, intended_pause=False, vpn_paused=False)
        if self.vpn_ip:
            try:
                handle.resume()
            except Exception:
                pass
            self.store.update(name, paused=False)
        else:
            logger.warning(f"Cannot resume '{name}' - VPN not connected")

    def remove(self, name: str, delete_files: bool = False) -> None:
        entry = self.store.get(name)
        handle = self.store.get_handle(name)
        if handle and self.ses:
            try:
                # Still tell libtorrent to delete too - harmless no-op if
                # the files already moved away from its original save
                # path (the common case once auto-move is on), but a
                # real cleanup for anything that finished without ever
                # being moved.
                flags = lt.options_t.delete_files if delete_files else 0
                self.ses.remove_torrent(handle, flags)
            except Exception as e:
                logger.warning(f"remove_torrent error for '{name}': {e}")

        if delete_files and entry:
            for moved_path in entry.get('moved_files') or []:
                try:
                    if os.path.isfile(moved_path):
                        os.remove(moved_path)
                        # Clean up the now-possibly-empty folder around it
                        # (a movie's own dedicated folder), but never a
                        # non-empty one - a TV show's "Season 01" folder
                        # is shared across every other episode already
                        # downloaded into it, and os.rmdir() simply fails
                        # harmlessly if anything else is still in there.
                        try:
                            os.rmdir(os.path.dirname(moved_path))
                        except OSError:
                            pass
                except OSError as e:
                    logger.warning(f"Could not delete moved file '{moved_path}' for '{name}': {e}")

        self.store.remove(name)

    # ── Fast-resume data ─────────────────────────────────────────────────────

    def _save_resume_data(self) -> None:
        """
        Ask libtorrent for fast-resume state (piece-verification progress,
        not the UI/API state in transfers.json) for every live torrent, and
        persist it to resume_data.json before shutting down. Without this,
        every daemon restart re-hashes every piece of every in-progress
        torrent from scratch, because libtorrent has no other way to know
        what's already been verified - the exact failure class behind other
        clients' worst startup-time complaints.

        save_resume_data()'s output deliberately never includes the torrent's
        metadata (file list/piece hashes), only piece-verification state -
        confirmed directly against this exact libtorrent version, not
        assumed. Since every torrent here was added via magnet link, that
        metadata was never available at add-time either, so on restore
        there'd be nothing to verify pieces AGAINST without it - libtorrent
        would have to re-fetch it from peers before doing anything, same as
        a brand new magnet add, defeating most of the point. So this also
        separately caches each torrent's metadata (once the handle actually
        has it) alongside its resume data.

        save_resume_data() itself is async - libtorrent posts the result as
        an alert rather than returning it directly - so this requests it for
        every handle, then drains pop_alerts() in a short polling loop
        (bounded to a few seconds, not indefinitely) collecting whatever
        comes back before giving up and writing out what it has.
        """
        if not self.ses:
            return

        pending: dict = {}
        for name in self.store.all_names():
            handle = self.store.get_handle(name)
            if not handle or not handle.is_valid():
                continue
            try:
                handle.save_resume_data()
                pending[name] = handle
            except Exception:
                pass

        if not pending:
            return

        results: dict = {}
        deadline = time.time() + 5.0
        while pending and time.time() < deadline:
            for alert in self.ses.pop_alerts():
                name = None
                if isinstance(alert, (lt.save_resume_data_alert, lt.save_resume_data_failed_alert)):
                    name = next((n for n, h in pending.items() if h == alert.handle), None)
                if name is None:
                    continue
                if isinstance(alert, lt.save_resume_data_alert):
                    try:
                        entry = {'resume': lt.write_resume_data_buf(alert.params)}
                        handle = pending[name]
                        if handle.has_metadata():
                            # info_section() is the raw info-dict only; wrap
                            # it back into a {'info': ...} structure so
                            # torrent_info's constructor accepts it on the
                            # way back in (read_resume_data never restores
                            # this part - see the docstring above).
                            info_bytes = bytes(handle.torrent_file().info_section())
                            entry['metadata'] = lt.bencode({b'info': lt.bdecode(info_bytes)})
                        results[name] = entry
                    except Exception as e:
                        logger.warning(f"Could not encode resume data for '{name}': {e}")
                pending.pop(name, None)
            if pending:
                time.sleep(0.1)

        try:
            encoded = {
                n: {k: base64.b64encode(v).decode('ascii') for k, v in entry.items()}
                for n, entry in results.items()
            }
            with open(_RESUME_DATA_PATH, 'w', encoding='utf-8') as f:
                json.dump(encoded, f)
            logger.info(f"Saved resume data for {len(results)}/{len(pending) + len(results)} torrent(s)")
        except Exception as e:
            logger.error(f"Could not write resume data: {e}")

    def _load_resume_data(self) -> dict:
        if not os.path.exists(_RESUME_DATA_PATH):
            return {}
        try:
            with open(_RESUME_DATA_PATH, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            return {
                n: {k: base64.b64decode(v) for k, v in entry.items()}
                for n, entry in raw.items()
            }
        except Exception as e:
            logger.error(f"Could not read resume data: {e}")
            return {}

    # ── Resume persisted ──────────────────────────────────────────────────────

    def _resume_persisted(self) -> None:
        """
        Re-attach libtorrent handles for persisted torrents from transfers.json.
        Covers both seeding and still-downloading (or downloading-but-paused)
        transfers - anything short of 'Failed' gets a real handle back, not
        just seeds. Without this, a transfer that was mid-download (or paused
        mid-download) when the daemon last restarted is left with no handle
        at all: pause/resume/files/peers silently do nothing forever, since
        there's no libtorrent object left to act on.

        Uses saved fast-resume data (see _save_resume_data) when available,
        so this doesn't re-hash every piece from scratch on every restart -
        except for a torrent that's been auto-moved to Plex/Jellyfin, whose
        resume data still points at the old (pre-move) path: media_organizer
        moves files with a plain filesystem move, not handle.move_storage(),
        so libtorrent's own resume state is stale for those and re-hashing
        against the real (new) location is the only correct option anyway.
        """
        kill_switch_active = load_config().get('vpn_required', False) and not self.vpn_ip
        resume_data = self._load_resume_data()
        names = self.store.all_names()
        for name in names:
            t = self.store.get(name)
            if not t or not t.get('magnet') or t.get('status') == 'Failed':
                continue

            save_path = t.get('plex_path') or DOWNLOAD_DIR_INCOMPLETE
            handle = None
            entry = resume_data.get(name)
            # Only worth attempting with both pieces present - resume data
            # alone can't do anything (no file list to verify pieces
            # against), and a torrent that hadn't received metadata yet by
            # the last shutdown gets nothing from re-trying that here either.
            if entry and 'resume' in entry and 'metadata' in entry and not t.get('moved_to_plex'):
                try:
                    params = lt.read_resume_data(entry['resume'])
                    # Already wrapped into a {'info': ...} structure at save
                    # time (see _save_resume_data) - torrent_info's
                    # constructor takes it as-is.
                    params.ti = lt.torrent_info(entry['metadata'])
                    # read_resume_data() never restores trackers from a
                    # magnet-only add, so pull them back in from the magnet
                    # URI itself - without this the torrent has verified
                    # pieces but no way to reach peers for the rest.
                    params.trackers = lt.parse_magnet_uri(t['magnet']).trackers
                    handle = self.ses.add_torrent(params)
                except Exception as e:
                    # A corrupt/incompatible resume blob (e.g. a libtorrent
                    # version bump changed the format) shouldn't sink the
                    # whole torrent - fall back to the always-works magnet
                    # re-add below instead, same as before this existed.
                    logger.warning(f"Resume data unusable for '{name}', falling back to magnet re-add: {e}")

            try:
                if handle is None:
                    magnet_params = {
                        'url': t['magnet'],
                        'save_path': save_path,
                        'storage_mode': lt.storage_mode_t(2),
                    }
                    handle = lt.add_magnet_uri(self.ses, t['magnet'], magnet_params)
                if t.get('intended_pause', False) or kill_switch_active:
                    handle.pause()
                else:
                    handle.resume()
                self.store.update(name,
                    handle=handle,
                    prev_bytes=0,
                    prev_time=time.time(),
                )
                if t.get('status') == 'Seeding':
                    threading.Thread(
                        target=self._monitor_seeding,
                        args=(name, handle),
                        daemon=True,
                        name=f"seed-{name[:24]}",
                    ).start()
                    logger.info(f"Resumed seeding: '{name}'")
                else:
                    threading.Thread(
                        target=self._monitor_download,
                        args=(name, handle, t.get('size', 0)),
                        daemon=True,
                        name=f"dl-{name[:24]}",
                    ).start()
                    logger.info(f"Resumed download: '{name}'")
            except Exception as e:
                logger.error(f"Failed to resume '{name}': {e}")


# RssPoller is defined in rss_poller.py (shared with peer.pyw)


# ---------------------------------------------------------------------------
# Global instances
# ---------------------------------------------------------------------------

store = DaemonStore(path=_TRANSFERS_PATH)
engine = TorrentEngine(store)
rss_poller = RssPoller(engine.add_magnet)
_ws_clients: Set[WebSocket] = set()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Public IP cache
# ---------------------------------------------------------------------------

_public_ip: str | None = None
_public_ip_ts: float = 0.0
_PUBLIC_IP_TTL = 300  # refresh every 5 minutes


async def _public_ip_refresher() -> None:
    """Background task: fetch public IP every 5 minutes, bound to VPN interface."""
    global _public_ip, _public_ip_ts
    loop = asyncio.get_event_loop()
    # Wait for engine to finish starting (VPN detection) before first fetch
    for _ in range(30):
        if engine and engine.vpn_ip is not None:
            break
        await asyncio.sleep(1)
    while True:
        try:
            vpn_ip = engine.vpn_ip if engine else None
            ip = await loop.run_in_executor(None, lambda: _fetch_public_ip(source_ip=vpn_ip))
            if ip:
                _public_ip = ip
                _public_ip_ts = time.time()
        except Exception:
            pass
        await asyncio.sleep(_PUBLIC_IP_TTL)


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup - engine blocks on VPN detection so run in a thread
    threading.Thread(target=engine.start, daemon=True, name="engine-start").start()
    rss_poller.start()
    broadcaster_task = asyncio.create_task(_ws_broadcaster())
    public_ip_task = asyncio.create_task(_public_ip_refresher())
    logger.info("Hydra Daemon starting - API key loaded (see hydra_config.json, not logged here)")
    yield
    # Shutdown
    broadcaster_task.cancel()
    public_ip_task.cancel()
    rss_poller.stop()
    engine.shutdown()


app = FastAPI(
    title="Hydra Torrent Daemon",
    version="0.1",
    description="Headless libtorrent engine with REST API and WebSocket streaming.",
    lifespan=lifespan,
    # No reason to hand an unauthenticated request the full endpoint/schema
    # map for free - this daemon is meant to be reached over LAN/VPN only.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


# ---------------------------------------------------------------------------
# Rate limiting middleware  (60 req / 60 s sliding window per IP)
# ---------------------------------------------------------------------------

_rate_window: dict = defaultdict(list)
RATE_LIMIT = 300      # max requests per window (tray + web UI + GF tray = ~80/min, 300 gives plenty of headroom)
RATE_WINDOW = 60.0    # window size in seconds


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    # Evict timestamps outside the window
    _rate_window[ip] = [t for t in _rate_window[ip] if now - t < RATE_WINDOW]
    if len(_rate_window[ip]) >= RATE_LIMIT:
        return JSONResponse(
            {"detail": "Rate limit exceeded"},
            status_code=429,
            headers={"Retry-After": "60"},
        )
    _rate_window[ip].append(now)
    return await call_next(request)


# ---------------------------------------------------------------------------
# WebSocket broadcaster  (sync world → async world bridge)
# ---------------------------------------------------------------------------

async def _ws_broadcaster() -> None:
    """
    Drains store.ws_queue and broadcasts the latest snapshot to all clients.
    Capped at ≤10 broadcasts per second by the 0.1 s sleep.
    Sends a heartbeat snapshot every 20 s when idle to prevent NAT from
    dropping the connection on quiet (no active downloads) sessions.
    """
    last_send = 0.0
    while True:
        try:
            # Drain all queued snapshots; only the latest one matters
            snapshot = None
            while True:
                try:
                    snapshot = store.ws_queue.get_nowait()
                except queue.Empty:
                    break

            # Heartbeat: re-send current state if nothing has been sent in 20 s
            now = asyncio.get_event_loop().time()
            if snapshot is None and _ws_clients and now - last_send >= 20.0:
                snapshot = store.snapshot()

            if snapshot is not None and _ws_clients:
                last_send = now
                dead: Set[WebSocket] = set()
                for ws in list(_ws_clients):
                    try:
                        await ws.send_json(snapshot)
                    except Exception:
                        dead.add(ws)
                _ws_clients.difference_update(dead)
        except Exception:
            pass
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    Real-time transfer snapshot stream.

    Protocol:
    1. Client connects.
    2. Client sends {"auth": "<api-key>"} within 5 seconds.
    3. Server sends the current snapshot immediately, then streams updates.
    """
    await websocket.accept()

    # Auth handshake
    try:
        auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
        if auth_msg.get("auth") != API_KEY:
            await websocket.close(code=4001, reason="Invalid API key")
            return
    except asyncio.TimeoutError:
        await websocket.close(code=4002, reason="Auth timeout - send {\"auth\":\"<key>\"} within 5 s")
        return
    except Exception:
        await websocket.close(code=4003, reason="Auth failed")
        return

    _ws_clients.add(websocket)
    # Push current state immediately so the client doesn't have to wait
    try:
        await websocket.send_json(store.snapshot())
    except Exception:
        _ws_clients.discard(websocket)
        return

    try:
        # Keep connection alive; client may send pings/messages which we ignore
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.post("/window/show", dependencies=[Depends(_require_api_key)])
def show_window() -> dict:
    """Ask this process's own desktop window to un-hide itself.

    Called by a second, short-lived HydraTorrent.exe launch (e.g. the
    user double-clicking the desktop shortcut again while Hydra's
    already running, hidden in the tray) - see hydra_app.py's
    single-instance handling for why this has to happen from inside
    the owning process rather than via a raw window-handle restore.
    No-ops in server/headless mode, where no callback is ever
    registered.
    """
    if _show_window_callback is not None:
        _show_window_callback()
        return {"shown": True}
    return {"shown": False}


@app.get("/status", dependencies=[Depends(_require_api_key)])
def get_status() -> DaemonStatus:
    """Overall daemon status including VPN and aggregate transfer stats."""
    vpn_connected, vpn_iface, vpn_ip = engine.vpn_guard.get_status()
    ses_status = engine.get_session_status()
    home_country_code = None
    if vpn_connected and _public_ip:
        home_country_code = engine._country_for_ip(_public_ip)[1] or None
    return DaemonStatus(
        running=engine.ses is not None,
        vpn=VPNStatus(
            connected=vpn_connected,
            iface=vpn_iface,
            vpn_ip=vpn_ip,
            public_ip=_public_ip if vpn_connected else None,
            country_code=home_country_code,
        ),
        total_download_rate=float(ses_status.get('download_rate', 0)),
        total_upload_rate=float(ses_status.get('upload_rate', 0)),
        num_torrents=len(store.snapshot()),
    )


@app.get("/vpn", dependencies=[Depends(_require_api_key)])
def get_vpn() -> VPNStatus:
    """Current VPN status."""
    connected, iface, ip = engine.vpn_guard.get_status()
    return VPNStatus(connected=connected, iface=iface, vpn_ip=ip)


@app.get("/health", dependencies=[Depends(_require_api_key)])
def get_health() -> HealthStatus:
    """
    Live reachability of everything that can silently break without a
    user noticing: VPN, Jackett, Plex, Jellyfin. Each optional integration
    reports 'configured: false' rather than an error when unused, so the
    UI can distinguish "not set up" from "set up but broken."
    """
    cfg = load_config()
    vpn_connected, vpn_iface, vpn_ip = engine.vpn_guard.get_status()

    jackett = check_jackett(cfg.get('indexing_server', ''), cfg.get('jackett_api_key', ''))
    plex = check_plex(cfg.get('plex_url', ''), cfg.get('plex_token', ''))
    jellyfin = check_jellyfin(cfg.get('jellyfin_url', ''), cfg.get('jellyfin_api_key', ''))

    return HealthStatus(
        vpn=VPNStatus(connected=vpn_connected, iface=vpn_iface, vpn_ip=vpn_ip),
        jackett=IntegrationHealth(**jackett),
        plex=IntegrationHealth(**plex),
        jellyfin=IntegrationHealth(**jellyfin),
    )


@app.get("/update-check", dependencies=[Depends(_require_api_key)])
def get_update_check() -> UpdateStatus:
    """
    Phase 1 update check - tells the UI whether a newer release exists on
    GitHub. Does not download or install anything (see updater.py).
    """
    result = check_for_update(VERSION, GITHUB_REPO)
    return UpdateStatus(**result)


@app.get("/transfers", dependencies=[Depends(_require_api_key)])
def list_transfers() -> List[TransferState]:
    """List all transfers."""
    return [TransferState(**t) for t in store.snapshot()]


@app.get("/transfers/{name}", dependencies=[Depends(_require_api_key)])
def get_transfer(name: str) -> TransferState:
    """Get a single transfer by name."""
    snaps = store.snapshot(name)
    if not snaps:
        raise HTTPException(status_code=404, detail=f"Transfer '{name}' not found")
    return TransferState(**snaps[0])


@app.post("/transfers", status_code=202, dependencies=[Depends(_require_api_key)])
def add_transfer(req: AddMagnetRequest, background_tasks: BackgroundTasks) -> dict:
    """
    Add a magnet link.  Returns 202 immediately.
    Metadata fetch (up to 120 s) runs in the background.
    Monitor progress via GET /transfers or the WebSocket.
    """
    if not req.magnet.startswith("magnet:?"):
        raise HTTPException(status_code=400, detail="Invalid magnet link - must start with 'magnet:?'")

    def _add() -> None:
        try:
            name = engine.add_magnet(req.magnet, req.save_path)
            logger.info(f"Torrent added via API: '{name}'")
        except TimeoutError as e:
            logger.error(f"Magnet metadata timeout: {e}")
        except Exception as e:
            logger.error(f"Failed to add magnet: {e}")

    background_tasks.add_task(_add)
    return {"accepted": True}


@app.post("/transfers/file", status_code=202, dependencies=[Depends(_require_api_key)])
async def upload_torrent_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)) -> dict:
    """
    Add a torrent from a .torrent file upload (multipart/form-data).
    Returns 202 immediately; the torrent is added in the background.
    """
    if not (file.filename or '').lower().endswith('.torrent'):
        raise HTTPException(status_code=400, detail="Only .torrent files are accepted")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB)")

    fname = file.filename or 'upload.torrent'

    def _add() -> None:
        try:
            name = engine.add_torrent_file(data, fname)
            logger.info(f"Torrent file added via API: '{name}'")
        except Exception as e:
            logger.error(f"Failed to add torrent file '{fname}': {e}")

    background_tasks.add_task(_add)
    return {"accepted": True}


@app.post("/transfers/url", status_code=202, dependencies=[Depends(_require_api_key)])
def add_torrent_from_url(req: AddTorrentUrlRequest, background_tasks: BackgroundTasks) -> dict:
    """
    Fetch a remote .torrent file URL and add it. Used for Jackett results that
    provide a proxy download link instead of a magnet URI.
    Returns 202 immediately; download runs in background.
    """
    import requests as _requests

    def _add() -> None:
        try:
            # Do NOT auto-follow redirects: Jackett proxy links commonly 302 to a
            # magnet: URI, which requests cannot fetch (no adapter for magnet:) and
            # would silently fail. Follow http(s) hops manually; hand magnet: to add_magnet.
            trusted = _indexer_trusted_hosts()
            _ensure_safe_fetch_url(req.url, trusted)
            resp = _requests.get(req.url, timeout=30, allow_redirects=False)
            hops = 0
            while resp.is_redirect and hops < 6:
                loc = resp.headers.get('Location', '')
                if loc.startswith('magnet:'):
                    name = engine.add_magnet(loc)
                    logger.info(f"Torrent URL resolved to magnet, added: '{name}'")
                    return
                _ensure_safe_fetch_url(loc, trusted)
                resp = _requests.get(loc, timeout=30, allow_redirects=False)
                hops += 1
            if resp.status_code != 200:
                logger.error(f"Failed to fetch torrent URL {req.url}: HTTP {resp.status_code}")
                return
            data = resp.content
            if data[:7] == b'magnet:':
                name = engine.add_magnet(data.decode('utf-8', 'ignore').strip())
                logger.info(f"Torrent URL body was a magnet, added: '{name}'")
                return
            fname = req.url.split('/')[-1].split('?')[0] or 'download.torrent'
            if not fname.lower().endswith('.torrent'):
                fname += '.torrent'
            name = engine.add_torrent_file(data, fname)
            logger.info(f"Torrent URL added via API: '{name}'")
        except Exception as e:
            logger.error(f"Failed to add torrent from URL '{req.url}': {e}")

    background_tasks.add_task(_add)
    return {"accepted": True}


@app.post("/transfers/preview", dependencies=[Depends(_require_api_key)])
def preview_transfer(req: PreviewTorrentRequest) -> dict:
    """
    Add a torrent paused and hidden from the transfer list, for the
    Search-tab add dialog: lets the UI show real size/file-list metadata
    and let the user pick files and a save location before anything
    actually starts downloading. Returns immediately with a name to poll
    GET /transfers/{name} against (status becomes 'Metadata ready' once
    the file list is available). Nothing here is visible in the Transfers
    list or persisted across a restart until POST .../commit is called;
    DELETE /transfers/{name} discards it same as any other transfer, safe
    since nothing was ever downloaded.
    """
    if not req.magnet and not req.url:
        raise HTTPException(status_code=400, detail="magnet or url is required")
    try:
        if req.magnet:
            if not req.magnet.startswith("magnet:?"):
                raise HTTPException(status_code=400, detail="Invalid magnet link - must start with 'magnet:?'")
            name = engine.preview_magnet(req.magnet, req.save_path)
        else:
            name = engine.preview_torrent_url(req.url, req.save_path)
    except HTTPException:
        raise
    except ValueError as e:
        # ValueError here is always one of our own deliberately-worded
        # messages (SSRF guard rejection, etc.) - safe to show verbatim.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Unlike ValueError above, this branch catches genuinely
        # unexpected failures - anything from libtorrent, the network
        # stack, or elsewhere. str(e) on one of these can be an ugly,
        # user-hostile internals leak (confirmed live: a torrent whose
        # name/file list contained a Unicode arrow character surfaced
        # "'charmap' codec can't encode character..." directly in the
        # add-torrent dialog - a real Windows console-encoding artifact,
        # not something a user should ever have to see or make sense
        # of). Log the real exception server-side where it's still
        # fully debuggable (hydra.log, Report-a-Bug's diagnostic log),
        # show a clean message in the UI.
        logger.error(f"preview_transfer failed: {e}")
        raise HTTPException(status_code=500, detail="Could not fetch metadata for this torrent - see hydra.log for details")
    return {"name": name}


@app.post("/transfers/{name}/commit", dependencies=[Depends(_require_api_key)])
def commit_transfer(name: str, req: CommitPreviewRequest) -> dict:
    """Turn a previewed torrent (see POST /transfers/preview) into a real,
    visible, downloading transfer."""
    if store.get(name) is None:
        raise HTTPException(status_code=404, detail=f"Transfer '{name}' not found")
    try:
        real_name = engine.commit_preview(name, req.save_path)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Transfer '{name}' not found")
    return {"name": real_name}


def _default_save_path() -> str:
    """
    The real default for a new download's save location: a sibling
    "incomplete" folder next to the configured Plex/Jellyfin library, if
    that library is actually configured AND genuinely reachable from
    here - same mount means the completion move becomes an instant
    rename instead of a slow cross-filesystem copy. DOWNLOAD_DIR_INCOMPLETE
    (a plain local folder) is the fallback, not the default: it's meant
    to catch the "Plex isn't set up, or its path isn't reachable from
    this machine" cases, not to be where every download lands regardless
    of a real, working media library being configured - confirmed live,
    that's exactly what it was doing before this fix.

    A filesystem path can still exist even when the media server itself
    is down or misconfigured (e.g. a wrong Plex URL doesn't take the
    underlying NFS/SMB share with it) - confirmed live, typing a wrong
    Plex IP left this still defaulting to the Plex-adjacent folder,
    which isn't what a user expects once they know Plex itself is
    unreachable. If a media server is actually configured, its own
    reachability gates this too, not just the raw filesystem check - a
    short timeout so a slow/down server doesn't stall opening the
    add-torrent dialog; if it's not configured at all, there's no
    server to check and the plain filesystem check stands alone.
    """
    # Read straight from the config file, not the MEDIA_DIR_MOVIES/
    # MEDIA_DIR_TV/DOWNLOAD_DIR_INCOMPLETE module-level constants -
    # those are frozen once at daemon startup (see config.py), so a
    # PATCH /config change to any of them wouldn't be reflected here
    # until the whole app restarted. Confirmed live: changed these via
    # the API, the file on disk was correct, but the running daemon
    # kept computing the default from the stale in-memory value anyway.
    cfg = load_config()
    plex_url = cfg.get('plex_url', '')
    plex_token = cfg.get('plex_token', '')
    jellyfin_url = cfg.get('jellyfin_url', '')
    jellyfin_key = cfg.get('jellyfin_api_key', '')
    media_dir_movies = cfg.get('media_dir_movies', '')
    media_dir_tv = cfg.get('media_dir_tv', '')
    download_dir_incomplete = cfg.get('download_dir_incomplete') or DOWNLOAD_DIR_INCOMPLETE

    server_configured = bool(plex_url or jellyfin_url)
    server_reachable = False
    if plex_url:
        try:
            server_reachable = server_reachable or check_plex(plex_url, plex_token, timeout=2.0)['reachable']
        except Exception:
            pass
    if jellyfin_url:
        try:
            server_reachable = server_reachable or check_jellyfin(jellyfin_url, jellyfin_key, timeout=2.0)['reachable']
        except Exception:
            pass
    if server_configured and not server_reachable:
        return download_dir_incomplete

    for library_root in (media_dir_movies, media_dir_tv):
        if not library_root:
            continue
        parent = os.path.dirname(library_root.rstrip('\\/'))
        if not parent:
            continue
        candidate = os.path.join(parent, 'incomplete')
        if os.path.isdir(candidate):
            return candidate
    return download_dir_incomplete


@app.get("/disk-space", dependencies=[Depends(_require_api_key)])
def get_disk_space(path: Optional[str] = None) -> dict:
    """
    Free/total bytes for a save-location path, defaulting to the real
    Plex-adjacent incomplete folder when one is configured and reachable
    (see _default_save_path), falling back to Hydra's plain local
    download directory otherwise. Walks up to the nearest existing
    parent directory if the exact path doesn't exist yet (e.g. the user
    is still typing it).
    """
    default_path = _default_save_path()
    check_path = path or default_path
    seen = set()
    while check_path and not os.path.isdir(check_path) and check_path not in seen:
        seen.add(check_path)
        parent = os.path.dirname(check_path.rstrip('\\/'))
        check_path = parent if parent and parent != check_path else default_path
    try:
        usage = shutil.disk_usage(check_path)
        return {"path": path or default_path, "free": usage.free, "total": usage.total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/check-path", dependencies=[Depends(_require_api_key)])
def check_path(path: str) -> dict:
    """Does this exact folder exist and is it a directory, from Hydra's own
    point of view. Used to verify Movies/TV/download-folder fields in
    Settings - especially the ones auto-filled from Plex/Jellyfin, since
    a path a media server reports (e.g. from inside its own Docker
    container) is not guaranteed to be a real path on the host Hydra
    actually runs on."""
    return {"path": path, "exists": bool(path) and os.path.isdir(path)}


@app.get("/plex/libraries", dependencies=[Depends(_require_api_key)])
def plex_libraries() -> dict:
    """List the current Plex server's libraries (name, type, path) using
    the Plex URL/token already saved in Settings - powers the 'Detect
    from Plex' button so nobody has to hand-type a library folder path
    they may not even know off the top of their head.

    The path Plex reports is Plex's own view of it, which for a
    Dockerized Plex (extremely common - LinuxServer.io images, TrueNAS
    Scale apps, Unraid, Synology) is the path *inside its container*,
    not necessarily the real path on the host Hydra runs on. Returned
    as-is; the frontend checks it against /check-path and explains the
    mismatch if there is one, rather than silently trusting it."""
    import requests as _requests
    cfg = load_config()
    url = (cfg.get('plex_url') or '').rstrip('/')
    token = cfg.get('plex_token') or ''
    if not url or not token:
        raise HTTPException(status_code=400, detail="Plex URL and token must be set in Settings first")

    try:
        resp = _requests.get(
            f"{url}/library/sections",
            params={"X-Plex-Token": token},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        sections = resp.json().get('MediaContainer', {}).get('Directory', [])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Couldn't reach Plex: {e}")

    libraries = []
    for s in sections:
        locations = s.get('Location') or []
        if not locations:
            continue
        libraries.append({
            'name': s.get('title', ''),
            'type': s.get('type', ''),  # 'movie', 'show', etc.
            'path': locations[0].get('path', ''),
        })
    return {"libraries": libraries}


@app.get("/jellyfin/libraries", dependencies=[Depends(_require_api_key)])
def jellyfin_libraries() -> dict:
    """Same as /plex/libraries but for Jellyfin - lists libraries via
    /Library/VirtualFolders using the Jellyfin URL/API key already saved
    in Settings. Same Docker-path caveat applies."""
    import requests as _requests
    cfg = load_config()
    url = (cfg.get('jellyfin_url') or '').rstrip('/')
    api_key = cfg.get('jellyfin_api_key') or ''
    if not url or not api_key:
        raise HTTPException(status_code=400, detail="Jellyfin URL and API key must be set in Settings first")

    try:
        resp = _requests.get(
            f"{url}/Library/VirtualFolders",
            headers={"X-Emby-Token": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        folders = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Couldn't reach Jellyfin: {e}")

    libraries = []
    for f in folders if isinstance(folders, list) else []:
        locations = f.get('Locations') or []
        if not locations:
            continue
        libraries.append({
            'name': f.get('Name', ''),
            'type': (f.get('CollectionType') or '').lower(),  # 'movies', 'tvshows', etc.
            'path': locations[0],
        })
    return {"libraries": libraries}


@app.post("/transfers/{name}/pause", dependencies=[Depends(_require_api_key)])
def pause_transfer(name: str) -> dict:
    """Pause a transfer."""
    if store.get(name) is None:
        raise HTTPException(status_code=404, detail=f"Transfer '{name}' not found")
    engine.pause(name)
    return {"ok": True}


@app.post("/transfers/{name}/resume", dependencies=[Depends(_require_api_key)])
def resume_transfer(name: str) -> dict:
    """Resume a paused transfer."""
    if store.get(name) is None:
        raise HTTPException(status_code=404, detail=f"Transfer '{name}' not found")
    if load_config().get('vpn_required', False) and not engine.vpn_ip:
        raise HTTPException(status_code=409, detail="VPN kill switch is on but no VPN is connected")
    engine.resume(name)
    return {"ok": True}


@app.delete("/transfers/{name}", dependencies=[Depends(_require_api_key)])
def delete_transfer(name: str, delete_files: bool = False) -> dict:
    """
    Remove a transfer.
    Pass ?delete_files=true to also wipe the downloaded files from disk.
    """
    if store.get(name) is None:
        raise HTTPException(status_code=404, detail=f"Transfer '{name}' not found")
    engine.remove(name, delete_files)
    return {"ok": True}


# ---------------------------------------------------------------------------
# File priority endpoints
# ---------------------------------------------------------------------------

@app.get("/transfers/{name}/files", dependencies=[Depends(_require_api_key)])
def get_transfer_files(name: str) -> List[TorrentFile]:
    """
    List all files in a torrent with their current download priority.
    priority: 0 = skip, 1 = normal, 7 = high
    Returns 409 if metadata is not yet available (magnet still resolving).
    """
    if store.get(name) is None:
        raise HTTPException(status_code=404, detail=f"Transfer '{name}' not found")

    handle = store.get_handle(name)
    if handle is None:
        raise HTTPException(status_code=409, detail="Torrent handle not available - metadata may still be fetching")

    try:
        ti = handle.get_torrent_info()
    except Exception as e:
        raise HTTPException(status_code=409, detail=f"Torrent info not available: {e}")

    files_obj = ti.files()
    num_files = files_obj.num_files()

    try:
        priorities = list(handle.file_priorities())
    except Exception:
        priorities = [1] * num_files

    result = []
    for i in range(num_files):
        result.append(TorrentFile(
            index=i,
            path=files_obj.file_path(i),
            size=int(files_obj.file_size(i)),
            priority=priorities[i] if i < len(priorities) else 1,
        ))

    return result


@app.get("/transfers/{name}/peers", dependencies=[Depends(_require_api_key)])
def get_transfer_peers(name: str) -> List[PeerInfo]:
    """
    List currently connected peers for a transfer: IP, country, client,
    per-peer speed, and whether they're also running Hydra.
    """
    if store.get(name) is None:
        raise HTTPException(status_code=404, detail=f"Transfer '{name}' not found")
    return [PeerInfo(**p) for p in engine.get_peer_info(name)]


@app.post("/transfers/{name}/files", dependencies=[Depends(_require_api_key)])
def set_file_priorities(name: str, req: SetFilePrioritiesRequest) -> dict:
    """
    Set download priority for one or more files within a torrent.
    Send a list of {index, priority} pairs.  Priority 0 = skip, 1 = normal, 7 = high.
    """
    if store.get(name) is None:
        raise HTTPException(status_code=404, detail=f"Transfer '{name}' not found")

    handle = store.get_handle(name)
    if handle is None:
        raise HTTPException(status_code=409, detail="Torrent handle not available")

    try:
        current_priorities = list(handle.file_priorities())
    except Exception:
        current_priorities = []

    for item in req.files:
        idx = item.index
        prio = max(0, min(7, item.priority))
        while len(current_priorities) <= idx:
            current_priorities.append(1)
        current_priorities[idx] = prio

    try:
        handle.prioritize_files(current_priorities)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set priorities: {e}")

    # commit_preview() needs to know "did the frontend ever explicitly
    # set file selection for this torrent" - see its own comment for why
    # this can't just re-read handle.file_priorities() back from
    # libtorrent (prioritize_files() is a fire-and-forget async call
    # into the session's internal engine; nothing guarantees the change
    # has actually landed by the time a caller reads it back a moment
    # later, and re-querying it raced in practice - confirmed live: a
    # user's real file exclusions were silently overridden back to
    # "download everything" because the immediately-following commit
    # read priorities back before this call's effect had landed).
    store.update(name, file_priorities_set=True)

    return {"ok": True, "updated": len(req.files)}


# ---------------------------------------------------------------------------
# RSS Auto-Download endpoints
# ---------------------------------------------------------------------------

@app.get("/rss/rules", dependencies=[Depends(_require_api_key)])
def list_rss_rules() -> List[RssRule]:
    """List all RSS auto-download rules (matched_titles stripped from list view)."""
    rules = rss_poller.get_all()
    return [
        RssRule(**{k: v for k, v in r.items() if k != 'matched_titles'}, matched_titles=[])
        for r in rules
    ]


@app.get("/rss/rules/{rule_id}", dependencies=[Depends(_require_api_key)])
def get_rss_rule(rule_id: str) -> RssRule:
    """Get a single rule, including its full matched_titles history (the
    list view strips that field to keep the list payload small)."""
    rule = rss_poller.get_one(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return RssRule(**rule)


@app.post("/rss/rules", status_code=201, dependencies=[Depends(_require_api_key)])
def add_rss_rule(req: AddRssRuleRequest) -> RssRule:
    """Create a new RSS watch rule. Triggers an immediate background check."""
    rule = rss_poller.add_rule(req.name, req.quality, req.query,
                               season=req.season, episode_mode=req.episode_mode,
                               start_episode=req.start_episode)
    return RssRule(**rule)


@app.patch("/rss/rules/{rule_id}", dependencies=[Depends(_require_api_key)])
def patch_rss_rule(rule_id: str, req: PatchRssRuleRequest) -> RssRule:
    """Enable/disable or change quality for an existing rule."""
    rule = rss_poller.patch_rule(rule_id, enabled=req.enabled, quality=req.quality)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return RssRule(**rule)


@app.delete("/rss/rules/{rule_id}", status_code=204, dependencies=[Depends(_require_api_key)])
def delete_rss_rule(rule_id: str) -> None:
    """Delete an RSS watch rule."""
    if not rss_poller.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")


@app.post("/rss/rules/{rule_id}/check-now", status_code=202, dependencies=[Depends(_require_api_key)])
def check_rule_now(rule_id: str) -> dict:
    """Trigger an immediate Jackett check for this rule in the background."""
    if not rss_poller.get_one(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    rss_poller.check_now(rule_id)
    return {"accepted": True}


@app.get("/static/{filename}", include_in_schema=False)
def serve_static(filename: str) -> FileResponse:
    """Serve static assets (logo, etc.)."""
    static_dir = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'))
    # os.path.basename() already strips any directory component from
    # filename (on both '/' and '\' separators), but CodeQL's path-injection
    # check doesn't credit that alone as a full sanitizer - and it's right
    # not to take it on faith. realpath + an explicit "still under
    # static_dir" check afterward is the actually-verifiable version: no
    # matter what filename resolves to, if it didn't land inside static_dir,
    # refuse it outright instead of trusting the join.
    file_path = os.path.realpath(os.path.join(static_dir, os.path.basename(filename)))
    if (not file_path.startswith(static_dir + os.sep)
            or not os.path.isfile(file_path)
            or file_path == os.path.join(static_dir, 'index.html')):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(file_path)


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def web_ui() -> HTMLResponse:
    """Serve the single-page Web UI with the API key embedded."""
    ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'index.html')
    try:
        with open(ui_path, encoding='utf-8') as f:
            html = f.read().replace('__HYDRA_API_KEY__', API_KEY)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Web UI not found</h1><p>static/index.html is missing.</p>", status_code=404)
    return HTMLResponse(content=html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/download/tray", include_in_schema=False)
def download_tray() -> StreamingResponse:
    """Serve hydra_tray.exe + pre-configured hydra_config.json as a ready-to-run zip."""
    exe_path = os.path.join(BASE_DIR, 'hydra_tray.exe')
    if not os.path.exists(exe_path):
        return JSONResponse(
            {"error": "hydra_tray.exe not found on server. Compile with PyInstaller and place in /opt/hydra/."},
            status_code=404,
        )
    tray_config = json.dumps({"daemon_api_key": API_KEY}, indent=2)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe_path, 'HydraTray/hydra_tray.exe')
        zf.writestr('HydraTray/hydra_config.json', tray_config)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="HydraTray.zip"'},
    )


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------

_EDITABLE_CONFIG_KEYS = frozenset({
    'plex_url', 'plex_token', 'jellyfin_url', 'jellyfin_api_key', 'jackett_api_key',
    'indexing_server', 'download_dir_incomplete', 'media_dir_movies', 'media_dir_tv',
    'download_dir_other', 'auto_move_to_plex', 'vpn_required', 'seed_ratio',
    'download_rate_limit', 'upload_rate_limit',
})


@app.get("/config", dependencies=[Depends(_require_api_key)])
def get_config() -> ConfigResponse:
    """Return the current daemon configuration (editable fields + API key)."""
    cfg = load_config()
    return ConfigResponse(
        plex_url=cfg.get('plex_url', ''),
        plex_token=cfg.get('plex_token', ''),
        jellyfin_url=cfg.get('jellyfin_url', ''),
        jellyfin_api_key=cfg.get('jellyfin_api_key', ''),
        jackett_api_key=cfg.get('jackett_api_key', ''),
        indexing_server=cfg.get('indexing_server', ''),
        # Falls back to the resolved default (not blank) so the field shows
        # where downloads actually land even before anyone customizes it.
        download_dir_incomplete=cfg.get('download_dir_incomplete') or DOWNLOAD_DIR_INCOMPLETE,
        media_dir_movies=cfg.get('media_dir_movies', ''),
        media_dir_tv=cfg.get('media_dir_tv', ''),
        download_dir_other=cfg.get('download_dir_other', ''),
        auto_move_to_plex=bool(cfg.get('auto_move_to_plex', True)),
        vpn_required=bool(cfg.get('vpn_required', False)),
        seed_ratio=float(cfg.get('seed_ratio', 0) or 0),
        download_rate_limit=int(cfg.get('download_rate_limit', 0) or 0),
        upload_rate_limit=int(cfg.get('upload_rate_limit', 0) or 0),
        daemon_api_key=API_KEY,
        platform=platform.platform(),
    )


@app.get("/logs/tail", dependencies=[Depends(_require_api_key)])
def get_logs_tail(lines: int = 200) -> dict:
    """
    Last N lines of the daemon log - powers the Settings "Copy diagnostic
    log" button for bug reports. Redacts every currently-configured secret
    (API key, Plex/Jellyfin/Jackett tokens) by exact value before
    returning, not by pattern-matching what a leak might look like - a
    grep across every logger.* call in this codebase turned up no line
    that logs a credential value directly, but that's a property of what's
    written today, not a guarantee for whatever gets added next. Exact-value
    redaction catches a future accidental leak of a *known* secret
    regardless of the log line's wording; it can't catch a secret it
    doesn't know about (a third-party token embedded in some external
    response body, say), so this is defense in depth, not a substitute for
    keeping logger.* calls credential-free in the first place.
    """
    lines = max(1, min(lines, 1000))
    if not os.path.exists(LOG_PATH):
        return {"lines": []}
    try:
        with open(LOG_PATH, 'r', encoding='utf-8', errors='replace') as f:
            tail = f.readlines()[-lines:]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not read log: {e}")

    cfg = load_config()
    secrets = [
        API_KEY,
        cfg.get('plex_token'),
        cfg.get('jellyfin_api_key'),
        cfg.get('jackett_api_key'),
    ]
    text = ''.join(tail)
    for s in secrets:
        if s:
            text = text.replace(s, '[REDACTED]')
    return {"lines": text.splitlines()}


@app.patch("/config", dependencies=[Depends(_require_api_key)])
def update_config(req: ConfigUpdate) -> dict:
    """Persist a partial config update.  Only editable keys are written."""
    cfg = load_config()
    updates = {k: v for k, v in req.model_dump(exclude_none=True).items()
               if k in _EDITABLE_CONFIG_KEYS}
    if not updates:
        return {"saved": []}
    cfg.update(updates)
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write config: {e}")
    logger.info(f"Config updated via API: {list(updates.keys())}")
    # Live-apply rate limits if they were changed
    if 'download_rate_limit' in updates or 'upload_rate_limit' in updates:
        engine.apply_rate_limits(
            int(cfg.get('download_rate_limit', 0) or 0),
            int(cfg.get('upload_rate_limit', 0) or 0),
        )
    # Live-apply VPN kill switch - if just turned on and VPN is off, pause all
    if 'vpn_required' in updates and updates['vpn_required'] and not engine.vpn_ip:
        logger.warning("VPN kill switch enabled with no VPN - pausing all transfers")
        engine.ses.apply_settings({'listen_interfaces': f'127.0.0.1:{PEER_PORT}', 'outgoing_interfaces': '127.0.0.1'})
        for name in store.all_names():
            t = store.get(name)
            handle = store.get_handle(name)
            if handle and t and not t.get('intended_pause', False):
                try:
                    handle.pause()
                    store.update(name, vpn_paused=True, paused=True)
                except Exception:
                    pass
    return {"saved": list(updates.keys())}


@app.post("/search", dependencies=[Depends(_require_api_key)])
def do_search(req: SearchRequest) -> List[SearchResult]:
    """
    Search for torrents.
    mode: "jackett" (Jackett API, an indexer the user configured themselves)
          or "local" (a Hydra index server the user configured themselves).

    There used to be a third mode, "online", that scraped named public
    indexers (1337x, Nyaa, The Pirate Bay) directly - removed 2026-08-20.
    Sonarr/Radarr/Jackett have operated safely for years specifically
    because they contain no built-in knowledge of any particular indexer;
    every source is something the user configures themselves, at arm's
    length. Hydra matching that architecture isn't just the safer legal
    posture, it's the actually-correct one for a general-purpose tool.
    """
    if req.mode not in ("jackett", "local"):
        raise HTTPException(status_code=400, detail=f"Unknown search mode '{req.mode}' - use 'jackett' or 'local'")
    try:
        if req.mode == "jackett":
            cfg = load_config()
            jackett_url = cfg.get('indexing_server') or cfg.get('jackett_url', 'http://127.0.0.1:9117')
            api_key = cfg.get('jackett_api_key', '')
            raw = search_jackett(jackett_url, req.query, api_key)
        else:
            server = req.server or 'localhost'
            raw = search_index_server(server, req.query)
    except HTTPException:
        raise
    except Exception as e:
        # Same reasoning as preview_transfer's except Exception branch:
        # raw search results carry arbitrary, remote-controlled title
        # text - don't hand str(e) straight to the UI, log it instead.
        logger.error(f"Search error ({req.mode}): {e}")
        raise HTTPException(status_code=500, detail=f"Search failed ({req.mode}) - see hydra.log for details")

    results = []
    for r in raw:
        results.append(SearchResult(
            name=r.get('filename') or r.get('name', ''),
            size=r.get('size', 0),
            seeders=r.get('seeders', 0),
            leechers=r.get('leechers', 0),
            engine=r.get('source') or r.get('engine', ''),
            engine_url=r.get('engine_url', ''),
            magnet=r.get('magnet'),
            torrent_url=r.get('torrent_url'),
            published=r.get('published'),
        ))
    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = load_config()
    host = cfg.get('daemon_host', '0.0.0.0')
    port = int(cfg.get('daemon_port', 8765))
    use_ssl = cfg.get('daemon_use_ssl', True)

    ssl_kwargs: dict = {}
    if use_ssl:
        from certs import ensure_certificates
        ensure_certificates()
        ssl_kwargs = {
            'ssl_keyfile':  str(PRIVKEY_PATH),
            'ssl_certfile': str(FULLCHAIN_PATH),
        }

    scheme = 'https' if use_ssl else 'http'
    print(f"Hydra Daemon v{VERSION}")
    print(f"Listening on:  {scheme}://{host}:{port}")
    # The API key gates every endpoint on this daemon - it used to be
    # printed here in clear text, which meant it also landed in
    # journalctl (StandardOutput=journal on the systemd deployment) and
    # in hydra.log, both of which get shared for debugging and backed up
    # far more casually than hydra_config.json itself. Point at the
    # config file instead of ever putting the real value in a log stream.
    print(f"API key:       (see {CONFIG_FILE})")
    if use_ssl:
        print(f"SSL cert:      {FULLCHAIN_PATH}")
    print()

    uvicorn.run(
        "hydra_daemon:app",
        host=host, port=port,
        reload=False, log_level="info",
        **ssl_kwargs,
    )
