#!/usr/bin/env python3
"""
Hydra Torrent Daemon — headless libtorrent engine + FastAPI REST + WebSocket.

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
import queue
import secrets
import logging
import threading
import warnings
import asyncio
import zipfile
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import List, Optional, Set

import libtorrent as lt
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
    PEER_PORT,
    PRIVKEY_PATH,
    load_config,
    save_config,
    logger,
)
from vpn_guard import VPNGuard
from media_organizer import auto_move_completed_download
from search import search_online_public, search_jackett, search_index_server
from rss_poller import RssPoller
from daemon_models import (
    AddMagnetRequest,
    AddRssRuleRequest,
    ConfigResponse,
    ConfigUpdate,
    DaemonStatus,
    FilePriorityItem,
    PatchRssRuleRequest,
    RssRule,
    SearchRequest,
    SearchResult,
    SetFilePrioritiesRequest,
    TorrentFile,
    TransferState,
    VPNStatus,
)


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
_INTERNAL_FIELDS = frozenset({'handle', 'prev_bytes', 'prev_time'})

# Path to persisted transfer state (mirrors transfer_manager.py)
_TRANSFERS_PATH = os.path.join(BASE_DIR, 'transfers.json')



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
# DaemonStore
# ---------------------------------------------------------------------------

class DaemonStore:
    """
    Thread-safe transfer state dictionary.

    Replaces TransferManager for the daemon context — no tkinter dependencies.
    Internal engine fields (handle, prev_bytes, prev_time) are stripped before
    API snapshots are built.
    """

    def __init__(self) -> None:
        self._data: dict = {}
        self._lock = threading.Lock()
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
                'error': None,
                'start_time': time.time(),
                'intended_pause': False,
                'ratio': 0.0,
                'total_uploaded': 0,
                'prev_bytes': 0,
                'prev_time': time.time(),
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
        """Return API-safe list of transfer dicts (handle/internals stripped)."""
        with self._lock:
            if name is not None:
                entry = self._data.get(name)
                return [self._clean(entry)] if entry else []
            return [self._clean(v) for v in self._data.values()]

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        with self._lock:
            data = {}
            for k, v in self._data.items():
                if v.get('status') != 'Downloading':
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
        except Exception:
            pass


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

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Detect VPN → create session → load persisted state → start monitor."""
        connected, iface, ip = self.vpn_guard.get_status()
        self.vpn_ip = ip if connected else None

        if connected:
            logger.info(f"VPN connected: {iface} ({ip})")
        else:
            logger.warning("No VPN detected at startup — binding to 0.0.0.0 (IP exposed)")

        listen_ip = self.vpn_ip or '0.0.0.0'
        self._create_session(listen_ip)
        self.store.load(_TRANSFERS_PATH)
        self._resume_persisted()
        self.vpn_guard.start(self._on_vpn_change)
        logger.info(f"TorrentEngine ready on {listen_ip}:{PEER_PORT}")

    def shutdown(self) -> None:
        logger.info("TorrentEngine shutting down...")
        self.store.save(_TRANSFERS_PATH)
        self.vpn_guard.stop()
        logger.info("TorrentEngine stopped.")

    # ── Session ───────────────────────────────────────────────────────────────

    def _create_session(self, listen_ip: str) -> None:
        warnings.filterwarnings("ignore", category=DeprecationWarning, module="libtorrent")
        cfg = load_config()
        self.ses = lt.session({
            'listen_interfaces': f'{listen_ip}:{PEER_PORT}',
            'enable_dht': True,
            'enable_lsd': False,       # No LAN broadcast
            'enable_upnp': True,       # Auto-forward port on home router for inbound connections
            'enable_natpmp': True,     # Same — NAT-PMP fallback
            'anonymous_mode': False,   # Allow full tracker/peer communication
            'connections_limit': 200,
            'download_rate_limit': int(cfg.get('download_rate_limit', 0) or 0),
            'upload_rate_limit': int(cfg.get('upload_rate_limit', 0) or 0),
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
            logger.info(f"Rate limits updated — DL: {download_limit} B/s, UL: {upload_limit} B/s")

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

    # ── VPN kill switch ────────────────────────────────────────────────────────

    def _on_vpn_change(self, connected: bool, iface: Optional[str], ip: Optional[str]) -> None:
        if not connected:
            logger.warning("VPN DISCONNECTED — kill switch activated")
            try:
                self.ses.apply_settings({'listen_interfaces': f'127.0.0.1:{PEER_PORT}'})
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
        else:
            logger.info(f"VPN reconnected ({ip}) — kill switch deactivated, rebinding session")
            self.vpn_ip = ip
            try:
                self.ses.apply_settings({'listen_interfaces': f'{ip}:{PEER_PORT}'})
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
            raise RuntimeError("libtorrent session not ready yet — try again shortly")

        if save_path is None:
            save_path = DOWNLOAD_DIR_INCOMPLETE

        params = {
            'url': magnet_uri,
            'save_path': save_path,
            'storage_mode': lt.storage_mode_t(2),
        }
        handle = lt.add_magnet_uri(self.ses, magnet_uri, params)
        logger.info("Added magnet → fetching metadata...")

        start_wait = time.time()
        while not handle.has_metadata():
            if time.time() - start_wait > 120:
                try:
                    self.ses.remove_torrent(handle)
                except Exception:
                    pass
                raise TimeoutError("Metadata timeout — no peers responded in 120 s")
            time.sleep(0.5)

        ti = handle.get_torrent_info()
        total_size = ti.total_size()
        real_name = ti.name() or "unnamed_torrent"
        logger.info(f"Metadata OK → '{real_name}' ({total_size:,} bytes)")

        for tracker in _TRACKERS:
            handle.add_tracker({'url': tracker})
        handle.force_reannounce()

        self.store.add(real_name, magnet_uri, total_size, handle)
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
            raise RuntimeError("libtorrent session not ready yet — try again shortly")

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

    # ── Download monitor ──────────────────────────────────────────────────────

    def _monitor_download(self, name: str, handle, total_size: int) -> None:
        """2-second poll loop — updates store stats, detects completion."""
        _state_map = {
            0: 'Queued', 1: 'Checking', 2: 'DL Metadata',
            3: 'Downloading', 4: 'Finished', 5: 'Seeding',
            6: 'Allocating', 7: 'Resuming',
        }

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
            speed_down = delta_bytes / delta_time if delta_time > 0 else 0.0

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

            if s.progress >= 1.0 or s.is_seeding or s.is_finished:
                logger.info(f"Download complete: '{name}'")
                self.store.update(name, status='Seeding', progress=100.0, eta='Seeding')
                threading.Thread(
                    target=self._post_download,
                    args=(name, handle),
                    daemon=True,
                    name=f"plex-{name[:20]}",
                ).start()
                self._monitor_seeding(name, handle)
                break

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

        for file_info in files:
            file_path = os.path.join(DOWNLOAD_DIR_INCOMPLETE, file_info.path)
            if not os.path.exists(file_path):
                continue

            success, dest_path, error = auto_move_completed_download(
                os.path.basename(file_path), file_path, torrent_name=name
            )
            if success:
                plex_dir = os.path.dirname(dest_path)
                self.store.update(name, moved_to_plex=True, plex_path=plex_dir)
                logger.info(f"Moved to Plex: {dest_path}")
            elif error and "Not a video file" not in error:
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

            current_time = time.time()
            prev_bytes = t.get('prev_bytes', 0)
            prev_time = t.get('prev_time', current_time)
            delta_bytes = s.total_upload - prev_bytes
            delta_time = current_time - prev_time
            speed_up = delta_bytes / delta_time if delta_time > 0 else float(s.upload_rate)

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
                )

            # Auto-remove when seed ratio target is reached
            seed_ratio = float(load_config().get('seed_ratio', 0) or 0)
            if seed_ratio > 0 and ratio >= seed_ratio:
                logger.info(
                    f"'{name}' reached seed ratio {ratio:.2f} "
                    f"(target {seed_ratio}) — auto-removing"
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
            logger.warning(f"Cannot resume '{name}' — VPN not connected")

    def remove(self, name: str, delete_files: bool = False) -> None:
        handle = self.store.get_handle(name)
        if handle and self.ses:
            try:
                flags = lt.options_t.delete_files if delete_files else 0
                self.ses.remove_torrent(handle, flags)
            except Exception as e:
                logger.warning(f"remove_torrent error for '{name}': {e}")
        self.store.remove(name)

    # ── Resume persisted ──────────────────────────────────────────────────────

    def _resume_persisted(self) -> None:
        """Re-attach libtorrent handles for seeding torrents from transfers.json."""
        names = self.store.all_names()
        for name in names:
            t = self.store.get(name)
            if not t or t.get('status') != 'Seeding' or not t.get('magnet'):
                continue

            save_path = t.get('plex_path') or DOWNLOAD_DIR_INCOMPLETE
            params = {
                'url': t['magnet'],
                'save_path': save_path,
                'storage_mode': lt.storage_mode_t(2),
            }
            try:
                handle = lt.add_magnet_uri(self.ses, t['magnet'], params)
                if t.get('intended_pause', False):
                    handle.pause()
                else:
                    handle.resume()
                self.store.update(name,
                    handle=handle,
                    prev_bytes=0,
                    prev_time=time.time(),
                )
                threading.Thread(
                    target=self._monitor_seeding,
                    args=(name, handle),
                    daemon=True,
                    name=f"seed-{name[:24]}",
                ).start()
                logger.info(f"Resumed seeding: '{name}'")
            except Exception as e:
                logger.error(f"Failed to resume '{name}': {e}")


# RssPoller is defined in rss_poller.py (shared with peer.pyw)


# ---------------------------------------------------------------------------
# Global instances
# ---------------------------------------------------------------------------

store = DaemonStore()
engine = TorrentEngine(store)
rss_poller = RssPoller(engine.add_magnet)
_ws_clients: Set[WebSocket] = set()


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — engine blocks on VPN detection so run in a thread
    threading.Thread(target=engine.start, daemon=True, name="engine-start").start()
    rss_poller.start()
    broadcaster_task = asyncio.create_task(_ws_broadcaster())
    logger.info(f"Hydra Daemon starting — API key: {API_KEY}")
    yield
    # Shutdown
    broadcaster_task.cancel()
    rss_poller.stop()
    engine.shutdown()


app = FastAPI(
    title="Hydra Torrent Daemon",
    version="0.1",
    description="Headless libtorrent engine with REST API and WebSocket streaming.",
    lifespan=lifespan,
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
        await websocket.close(code=4002, reason="Auth timeout — send {\"auth\":\"<key>\"} within 5 s")
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

@app.get("/status", dependencies=[Depends(_require_api_key)])
def get_status() -> DaemonStatus:
    """Overall daemon status including VPN and aggregate transfer stats."""
    vpn_connected, vpn_iface, vpn_ip = engine.vpn_guard.get_status()
    ses_status = engine.get_session_status()
    return DaemonStatus(
        running=engine.ses is not None,
        vpn=VPNStatus(
            connected=vpn_connected,
            iface=vpn_iface,
            vpn_ip=vpn_ip,
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
        raise HTTPException(status_code=400, detail="Invalid magnet link — must start with 'magnet:?'")

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
        raise HTTPException(status_code=409, detail="Torrent handle not available — metadata may still be fetching")

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
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    file_path = os.path.join(static_dir, os.path.basename(filename))
    if not os.path.isfile(file_path) or file_path == os.path.join(static_dir, 'index.html'):
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
    return HTMLResponse(content=html)


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
    'plex_url', 'plex_token', 'jackett_api_key',
    'indexing_server', 'media_dir_movies', 'media_dir_tv', 'auto_move_to_plex',
    'seed_ratio', 'download_rate_limit', 'upload_rate_limit',
})


@app.get("/config", dependencies=[Depends(_require_api_key)])
def get_config() -> ConfigResponse:
    """Return the current daemon configuration (editable fields + API key)."""
    cfg = load_config()
    return ConfigResponse(
        plex_url=cfg.get('plex_url', ''),
        plex_token=cfg.get('plex_token', ''),
        jackett_api_key=cfg.get('jackett_api_key', ''),
        indexing_server=cfg.get('indexing_server', ''),
        media_dir_movies=cfg.get('media_dir_movies', ''),
        media_dir_tv=cfg.get('media_dir_tv', ''),
        auto_move_to_plex=bool(cfg.get('auto_move_to_plex', True)),
        seed_ratio=float(cfg.get('seed_ratio', 0) or 0),
        download_rate_limit=int(cfg.get('download_rate_limit', 0) or 0),
        upload_rate_limit=int(cfg.get('upload_rate_limit', 0) or 0),
        daemon_api_key=API_KEY,
    )


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
    return {"saved": list(updates.keys())}


@app.post("/search", dependencies=[Depends(_require_api_key)])
def do_search(req: SearchRequest) -> List[SearchResult]:
    """
    Search for torrents.
    mode: "online" (public scrapers), "jackett" (Jackett API), "local" (index server)
    """
    try:
        if req.mode == "jackett":
            cfg = load_config()
            jackett_url = cfg.get('indexing_server') or cfg.get('jackett_url', 'http://127.0.0.1:9117')
            api_key = cfg.get('jackett_api_key', '')
            raw = search_jackett(jackett_url, req.query, api_key)
        elif req.mode == "local":
            server = req.server or 'localhost'
            raw = search_index_server(server, req.query)
        else:
            raw = search_online_public(req.query)
    except Exception as e:
        logger.error(f"Search error ({req.mode}): {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
            published=r.get('published'),
        ))
    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = load_config()
    host = cfg.get('daemon_host', '127.0.0.1')
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
    print(f"Hydra Daemon v0.1")
    print(f"Listening on:  {scheme}://{host}:{port}")
    print(f"API key:       {API_KEY}")
    print(f"API docs:      {scheme}://{host}:{port}/docs")
    if use_ssl:
        print(f"SSL cert:      {FULLCHAIN_PATH}")
    print()

    uvicorn.run(
        "hydra_daemon:app",
        host=host, port=port,
        reload=False, log_level="info",
        **ssl_kwargs,
    )
