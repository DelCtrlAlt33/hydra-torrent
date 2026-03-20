#!/usr/bin/env python3
"""
Hydra Torrent System Tray

Remote monitor for the Hydra daemon running on R710 LXC (192.168.20.33).
- Hides the console window immediately on launch
- Shows a Hydra icon in the Windows system tray with a colour-coded status dot
- Polls GET /status every 5 s to update the icon and VPN menu text
- Provides a right-click menu: VPN status, Open Web UI, Pause/Resume All, Exit
- Writes/removes a Windows startup registry entry

Usage:
    python hydra_tray.py          # normal (console visible briefly)
    pythonw hydra_tray.py         # no console window at all
"""

import ctypes
import json
import os
import sys
import threading
import time
import webbrowser
import winreg
from typing import Optional
from urllib.parse import quote as url_quote

import pystray
import requests
from PIL import Image, ImageDraw
from pystray import MenuItem as Item, Menu

# Suppress InsecureRequestWarning — we use self-signed cert with verify=False
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass


# ---------------------------------------------------------------------------
# Hide console FIRST, before anything else runs
# ---------------------------------------------------------------------------

def _hide_console() -> None:
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass

_hide_console()


# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def resource_path(name: str) -> str:
    """Resolve a bundled resource (works in both script and PyInstaller)."""
    try:
        base = sys._MEIPASS  # type: ignore[attr-defined]
    except AttributeError:
        base = _SCRIPT_DIR
    return os.path.join(base, name)


_CONFIG_FILE     = os.path.join(_SCRIPT_DIR, 'hydra_config.json')
_DAEMON_URL_BASE = "https://192.168.20.33:8765"   # R710 LXC

_STARTUP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_REG_VALUE = "HydraTorrent"

_ICON_SIZE = 64    # tray icon canvas size (px)
_DOT_RADIUS = 8    # status dot radius (px)

# Status sentinel values
_STATUS_STARTING = 'starting'   # daemon not yet responding — orange
_STATUS_HEALTHY  = 'healthy'    # VPN connected — green
_STATUS_NO_VPN   = 'no_vpn'    # daemon up, VPN gone — red
_STATUS_DEAD     = 'dead'       # daemon not responding — red

_DOT_COLOUR = {
    _STATUS_STARTING: '#FFA500',
    _STATUS_HEALTHY:  '#00CC44',
    _STATUS_NO_VPN:   '#FF3333',
    _STATUS_DEAD:     '#FF3333',
}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _get_api_key() -> str:
    return _load_config().get('daemon_api_key', '')


# ---------------------------------------------------------------------------
# Tray icon drawing
# ---------------------------------------------------------------------------

def _make_icon(status: str) -> Image.Image:
    """Return a 64×64 RGBA image: Hydra logo + small coloured status dot."""
    ico_path = resource_path('image8.ico')
    try:
        base = Image.open(ico_path).convert('RGBA').resize(
            (_ICON_SIZE, _ICON_SIZE), Image.LANCZOS
        )
    except Exception:
        # Fallback: plain blue circle with 'H'
        base = Image.new('RGBA', (_ICON_SIZE, _ICON_SIZE), (30, 60, 100, 255))
        d = ImageDraw.Draw(base)
        d.ellipse([4, 4, _ICON_SIZE - 4, _ICON_SIZE - 4], fill=(60, 120, 200, 255))
        d.text((_ICON_SIZE // 2 - 6, _ICON_SIZE // 2 - 10), 'H', fill='white')

    # Status dot — bottom-right corner
    draw = ImageDraw.Draw(base)
    colour = _DOT_COLOUR.get(status, '#888888')
    margin = 4
    x0 = _ICON_SIZE - _DOT_RADIUS * 2 - margin
    y0 = _ICON_SIZE - _DOT_RADIUS * 2 - margin
    x1 = _ICON_SIZE - margin
    y1 = _ICON_SIZE - margin
    draw.ellipse([x0 - 2, y0 - 2, x1 + 2, y1 + 2], fill='white')   # white outline
    draw.ellipse([x0, y0, x1, y1], fill=colour)

    return base


# ---------------------------------------------------------------------------
# Windows startup registry
# ---------------------------------------------------------------------------

def _startup_enabled() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _STARTUP_REG_KEY, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, _STARTUP_REG_VALUE)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _enable_startup() -> None:
    """Write startup registry entry using pythonw.exe (no console on login)."""
    # sys.executable is always the interpreter that is actually running right now
    # and has all required dependencies — use it directly rather than searching.
    python_exe = sys.executable
    # Prefer pythonw.exe (same dir) so no console flashes on login.
    pythonw = os.path.join(os.path.dirname(python_exe), 'pythonw.exe')
    if not os.path.exists(pythonw):
        pythonw = python_exe
    value = f'"{pythonw}" "{os.path.abspath(__file__)}"'
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _STARTUP_REG_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _STARTUP_REG_VALUE, 0, winreg.REG_SZ, value)
    except Exception as e:
        print(f"[tray] Failed to enable startup: {e}", file=sys.stderr)


def _disable_startup() -> None:
    """Remove startup registry entry."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _STARTUP_REG_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _STARTUP_REG_VALUE)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[tray] Failed to disable startup: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# HydraTray
# ---------------------------------------------------------------------------

class HydraTray:

    def __init__(self) -> None:
        self._api_key: str = ''
        self._icon: Optional[pystray.Icon] = None
        self._status: str = _STATUS_STARTING
        self._vpn_text: str = 'VPN: Checking...'
        self._lock = threading.Lock()
        # Completion tracking — None means first poll hasn't happened yet
        # (so we don't spam notifications for already-finished torrents on startup)
        self._notified: Optional[set] = None

    # ── Status polling ────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Background thread: refresh status + check for completed downloads every 5 s."""
        while True:
            self._refresh_status()
            transfers = self._fetch_transfers()
            if transfers is not None:
                self._check_completions(transfers)
            time.sleep(5)

    def _fetch_transfers(self) -> Optional[list]:
        """Fetch current transfer list from daemon. Returns None on failure."""
        try:
            r = requests.get(
                f"{_DAEMON_URL_BASE}/transfers",
                headers={"X-API-Key": self._api_key},
                timeout=3,
                verify=False,
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def _check_completions(self, transfers: list) -> None:
        """Fire a Windows toast for any transfer that just finished downloading."""
        if self._notified is None:
            # First successful poll: record already-finished torrents silently
            self._notified = {
                t['name'] for t in transfers
                if t.get('status') in ('Seeding', 'Finished') or t.get('progress', 0) >= 100
            }
            return

        current_names = {t['name'] for t in transfers}
        for t in transfers:
            name = t['name']
            if name in self._notified:
                continue
            if t.get('status') in ('Seeding', 'Finished') or t.get('progress', 0) >= 100:
                self._fire_notification(name, t)
                self._notified.add(name)

        # Prune names that were removed (allow re-notification if same torrent re-added)
        self._notified.intersection_update(current_names)

    def _fire_notification(self, name: str, transfer: dict) -> None:
        """Show a Windows toast notification for a completed download."""
        try:
            plex_path = transfer.get('plex_path')
            if plex_path:
                dest = 'TV' if ('/tv' in plex_path or '\\tv' in plex_path) else 'Movies'
                msg = f"Moved to Plex {dest}"
            else:
                msg = "Download finished"
            if self._icon:
                self._icon.notify(msg, title=name[:64])
        except Exception as e:
            print(f"[tray] Notification error: {e}", file=sys.stderr)

    def _refresh_status(self) -> None:
        # Re-read key in case daemon just generated it
        api_key = _get_api_key() or self._api_key
        if api_key:
            self._api_key = api_key

        try:
            r = requests.get(
                f"{_DAEMON_URL_BASE}/status",
                headers={"X-API-Key": self._api_key},
                timeout=3,
                verify=False,
            )
            if r.status_code == 200:
                data = r.json()
                vpn = data.get('vpn', {})
                connected = vpn.get('connected', False)
                vpn_ip = vpn.get('vpn_ip')
                new_status = _STATUS_HEALTHY if connected else _STATUS_NO_VPN
                new_vpn_text = (
                    f"VPN: Protected ({vpn_ip})" if (connected and vpn_ip)
                    else "VPN: EXPOSED"
                )
            else:
                new_status = _STATUS_DEAD
                new_vpn_text = "VPN: Unknown (daemon error)"
        except Exception:
            new_status = _STATUS_DEAD
            new_vpn_text = "VPN: Unknown (daemon unreachable)"

        with self._lock:
            changed = (new_status != self._status or new_vpn_text != self._vpn_text)
            self._status = new_status
            self._vpn_text = new_vpn_text

        if changed and self._icon:
            self._icon.icon = _make_icon(new_status)
            self._icon.title = {
                _STATUS_HEALTHY:  "Hydra Torrent (R710) — OK",
                _STATUS_NO_VPN:   "Hydra Torrent (R710) — VPN DOWN",
                _STATUS_DEAD:     "Hydra Torrent (R710) — DAEMON UNREACHABLE",
                _STATUS_STARTING: "Hydra Torrent (R710) — Connecting...",
            }.get(new_status, "Hydra Torrent (R710)")
            self._icon.update_menu()

    def _get_vpn_text(self) -> str:
        with self._lock:
            return self._vpn_text

    # ── Menu actions ──────────────────────────────────────────────────────────

    def _action_open_web_ui(self, icon, item) -> None:
        webbrowser.open(f"{_DAEMON_URL_BASE}/ui")  # browser shows self-signed cert warning on first visit

    def _action_pause_all(self, icon, item) -> None:
        try:
            r = requests.get(
                f"{_DAEMON_URL_BASE}/transfers",
                headers={"X-API-Key": self._api_key},
                timeout=3,
                verify=False,
            )
            if r.status_code != 200:
                return
            for t in r.json():
                name = t.get('name', '')
                if name and not t.get('paused', False):
                    requests.post(
                        f"{_DAEMON_URL_BASE}/transfers/{url_quote(name, safe='')}/pause",
                        headers={"X-API-Key": self._api_key},
                        timeout=3,
                        verify=False,
                    )
        except Exception as e:
            print(f"[tray] Pause all error: {e}", file=sys.stderr)

    def _action_resume_all(self, icon, item) -> None:
        try:
            r = requests.get(
                f"{_DAEMON_URL_BASE}/transfers",
                headers={"X-API-Key": self._api_key},
                timeout=3,
                verify=False,
            )
            if r.status_code != 200:
                return
            for t in r.json():
                name = t.get('name', '')
                if name and t.get('paused', False):
                    requests.post(
                        f"{_DAEMON_URL_BASE}/transfers/{url_quote(name, safe='')}/resume",
                        headers={"X-API-Key": self._api_key},
                        timeout=3,
                        verify=False,
                    )
        except Exception as e:
            print(f"[tray] Resume all error: {e}", file=sys.stderr)

    def _action_toggle_startup(self, icon, item) -> None:
        if _startup_enabled():
            _disable_startup()
        else:
            _enable_startup()
        if self._icon:
            self._icon.update_menu()

    def _action_exit(self, icon, item) -> None:
        print("[tray] Exit")
        if self._icon:
            self._icon.stop()

    # ── Menu building ──────────────────────────────────────────────────────────

    def _build_menu(self) -> pystray.Menu:
        """
        Build the right-click menu.

        VPN text and startup label are callables so pystray re-evaluates them
        each time the menu opens — no manual update_menu() call needed for text.
        """
        return pystray.Menu(
            # Dynamic VPN status line (non-clickable)
            Item(lambda item: self._get_vpn_text(), None, enabled=False),
            Menu.SEPARATOR,
            Item("Open Web UI", self._action_open_web_ui),
            Item("Pause All",   self._action_pause_all),
            Item("Resume All",  self._action_resume_all),
            Menu.SEPARATOR,
            Item(
                lambda item: "Disable startup" if _startup_enabled() else "Enable startup",
                self._action_toggle_startup,
            ),
            Menu.SEPARATOR,
            Item("Exit", self._action_exit),
        )

    # ── Entry point ────────────────────────────────────────────────────────────

    def run(self) -> None:
        # 1. Load API key from local config
        self._api_key = _get_api_key()
        print(f"[tray] Connecting to R710 daemon at {_DAEMON_URL_BASE}")

        # 2. Refresh startup registry entry
        _enable_startup()

        # 3. Create and show the tray icon
        self._icon = pystray.Icon(
            name="HydraTorrent",
            icon=_make_icon(self._status),
            title="Hydra Torrent (R710)",
            menu=self._build_menu(),
        )

        # 4. Start background status poll thread
        threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="tray-poll",
        ).start()

        # 5. Run pystray on the main thread (blocks until icon.stop() is called)
        self._icon.run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    HydraTray().run()
