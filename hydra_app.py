#!/usr/bin/env python3
"""
Hydra Torrent — Desktop App Launcher

This is the entry point for desktop users who install Hydra Torrent as an app.
It starts the daemon in-process, opens the Web UI in a frameless pywebview
window, and shows a system tray icon.

Usage:
    python hydra_app.py       # normal
    pythonw hydra_app.py      # no console window
"""

import ctypes
import json
import os
import sys
import threading
import time

# ---------------------------------------------------------------------------
# PyInstaller bundle support
# ---------------------------------------------------------------------------
# When bundled as --onefile, PyInstaller extracts data files to a temp dir
# stored in sys._MEIPASS. We need that on sys.path so uvicorn can import
# hydra_daemon and its dependencies.

if getattr(sys, 'frozen', False):
    _BUNDLE_DIR = sys._MEIPASS
    if _BUNDLE_DIR not in sys.path:
        sys.path.insert(0, _BUNDLE_DIR)
    # Config lives next to the .exe, not in the temp extraction dir
    _EXE_DIR = os.path.dirname(sys.executable)
else:
    _BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    _EXE_DIR = _BUNDLE_DIR

# ---------------------------------------------------------------------------
# Single-instance enforcement
# ---------------------------------------------------------------------------
# If another instance is already running, show its window and exit.

_MUTEX_NAME = "HydraTorrentSingleInstance"
_mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    # Bring the existing window to the foreground and exit
    hwnd = ctypes.windll.user32.FindWindowW(None, "Hydra Torrent")
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    sys.exit(0)

import requests
import webview

# ---------------------------------------------------------------------------
# Hide console immediately
# ---------------------------------------------------------------------------

def _hide_console():
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass

_hide_console()

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------

_INSTALL_DIR = os.path.join(os.environ.get('LOCALAPPDATA', _EXE_DIR), 'HydraTorrent')
_SCRIPT_DIR = _BUNDLE_DIR
_CONFIG_FILE = os.path.join(_INSTALL_DIR, 'hydra_config.json')


def _load_config() -> dict:
    try:
        with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# First-run self-install
# ---------------------------------------------------------------------------

def _is_installed() -> bool:
    """Check if Hydra is installed in the proper location."""
    return os.path.isfile(os.path.join(_INSTALL_DIR, 'HydraTorrent.exe'))


def _self_install() -> None:
    """Copy exe + assets to %LOCALAPPDATA%\\HydraTorrent, create shortcuts
    and firewall rules. Runs silently on first launch."""
    import shutil
    import subprocess

    os.makedirs(_INSTALL_DIR, exist_ok=True)

    # Copy exe
    exe_src = sys.executable if getattr(sys, 'frozen', False) else __file__
    exe_dst = os.path.join(_INSTALL_DIR, 'HydraTorrent.exe')
    if os.path.abspath(exe_src) != os.path.abspath(exe_dst):
        shutil.copy2(exe_src, exe_dst)

    # Copy bundled assets next to installed exe
    for asset in ('image8.ico', 'GeoLite2-Country.mmdb'):
        src = os.path.join(_BUNDLE_DIR, asset) if getattr(sys, 'frozen', False) else os.path.join(_EXE_DIR, asset)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(_INSTALL_DIR, asset))

    # Create default config if none exists
    if not os.path.isfile(_CONFIG_FILE):
        with open(_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                "daemon_host": "127.0.0.1",
                "daemon_port": 8766,
                "daemon_use_ssl": False,
                "desktop_mode": True,
                "search_mode": "online",
            }, f, indent=2)

    # Create desktop shortcut
    icon_path = os.path.join(_INSTALL_DIR, 'image8.ico')
    _create_shortcut(
        os.path.join(os.path.expanduser('~'), 'Desktop', 'Hydra Torrent.lnk'),
        exe_dst, _INSTALL_DIR, icon_path,
    )

    # Create Start Menu shortcut
    start_menu = os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs')
    if os.path.isdir(start_menu):
        _create_shortcut(
            os.path.join(start_menu, 'Hydra Torrent.lnk'),
            exe_dst, _INSTALL_DIR, icon_path,
        )

    # Add firewall rules (best-effort, requires admin — silently skip if not admin)
    try:
        subprocess.run(
            ['netsh', 'advfirewall', 'firewall', 'delete', 'rule', 'name=Hydra Torrent'],
            capture_output=True, timeout=5,
        )
        for proto in ('TCP', 'UDP'):
            subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'add', 'rule',
                 'name=Hydra Torrent', 'dir=in', 'action=allow',
                 f'protocol={proto}', 'localport=6002', 'profile=any',
                 'description=Hydra Torrent BitTorrent client'],
                capture_output=True, timeout=5,
            )
        subprocess.run(
            ['netsh', 'advfirewall', 'firewall', 'add', 'rule',
             'name=Hydra Torrent DHT', 'dir=in', 'action=allow',
             'protocol=UDP', 'localport=6881', 'profile=any',
             'description=Hydra Torrent DHT'],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass  # not admin — firewall rules skipped, user will get the Windows prompt instead


def _create_shortcut(lnk_path: str, target: str, workdir: str, icon: str) -> None:
    """Create a Windows .lnk shortcut using a temporary PowerShell script."""
    import subprocess
    import tempfile
    try:
        ps_file = os.path.join(tempfile.gettempdir(), 'hydra_shortcut.ps1')
        with open(ps_file, 'w', encoding='utf-8') as f:
            f.write(
                f'$ws = New-Object -ComObject WScript.Shell\n'
                f'$s = $ws.CreateShortcut("{lnk_path}")\n'
                f'$s.TargetPath = "{target}"\n'
                f'$s.WorkingDirectory = "{workdir}"\n'
                f'$s.IconLocation = "{icon}"\n'
                f'$s.Save()\n'
            )
        subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-File', ps_file],
            capture_output=True, timeout=10,
        )
        os.remove(ps_file)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Daemon (runs in a background thread)
# ---------------------------------------------------------------------------

def _start_daemon(host: str, port: int, use_ssl: bool) -> None:
    """Start the Hydra daemon in-process on a background thread."""
    import uvicorn

    ssl_args = {}
    if use_ssl:
        cert_dir = os.path.join(_SCRIPT_DIR, 'certs')
        certfile = os.path.join(cert_dir, 'hydra.crt')
        keyfile = os.path.join(cert_dir, 'hydra.key')
        if os.path.isfile(certfile) and os.path.isfile(keyfile):
            ssl_args = {'ssl_certfile': certfile, 'ssl_keyfile': keyfile}
        else:
            print("[app] SSL certs not found, falling back to HTTP", file=sys.stderr)
            use_ssl = False

    uvicorn.run(
        "hydra_daemon:app",
        host=host,
        port=port,
        log_level="info",
        **ssl_args,
    )


def _wait_for_daemon(url: str, api_key: str, timeout: float = 15.0) -> bool:
    """Poll the daemon until it responds or timeout is reached."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(
                f"{url}/status",
                headers={"X-API-Key": api_key},
                timeout=2,
                verify=False,
            )
            if r.status_code in (200, 401):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# PyWebView window management
# ---------------------------------------------------------------------------

_window = None   # type: webview.Window | None
_daemon_url = ''


def _open_callback():
    """Called from tray 'Open' to show the window."""
    if _window is not None:
        _window.show()
        _window.restore()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _daemon_url

    # Suppress InsecureRequestWarning
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    # 0. First-run self-install
    if getattr(sys, 'frozen', False) and not _is_installed():
        _self_install()

    # 1. Load config
    cfg = _load_config()
    host = cfg.get('daemon_host', '127.0.0.1')
    port = int(cfg.get('daemon_port', 8766))
    use_ssl = cfg.get('daemon_use_ssl', False)
    api_key = cfg.get('daemon_api_key', '')

    scheme = 'https' if use_ssl else 'http'
    _daemon_url = f"{scheme}://{host}:{port}"

    print(f"[app] Hydra Torrent Desktop starting...")
    print(f"[app] Daemon: {_daemon_url}")

    # 2. Start daemon in background thread
    daemon_thread = threading.Thread(
        target=_start_daemon,
        args=(host, port, use_ssl),
        daemon=True,
        name="hydra-daemon",
    )
    daemon_thread.start()

    # 3. Wait for daemon to be ready
    print("[app] Waiting for daemon to start...")
    if not _wait_for_daemon(_daemon_url, api_key):
        print("[app] ERROR: Daemon failed to start within 15s", file=sys.stderr)
        # Still try to open — the page will show an error but at least the app opens
    else:
        print("[app] Daemon is ready")

    # 4. Create the frameless pywebview window
    from webview_api import ResizeAPI, apply_frameless

    global _window
    api = ResizeAPI(hide_on_close=True)

    w = webview.create_window(
        'Hydra Torrent',
        url=f'{_daemon_url}/ui',
        width=1200,
        height=800,
        min_size=(800, 500),
        frameless=True,
        easy_drag=False,
        js_api=api,
        background_color='#060606',
    )
    api.set_window(w)
    apply_frameless(w)
    _window = w

    # Intercept Alt+F4 / taskbar close — hide instead of destroy
    def _on_closing():
        w.hide()
        return False  # prevent actual destruction

    w.events.closing += _on_closing

    # 5. Start tray icon on a background thread
    from hydra_tray import HydraTray
    tray = HydraTray(desktop_mode=True, open_callback=_open_callback)
    threading.Thread(
        target=tray.run,
        daemon=True,
        name="hydra-tray",
    ).start()

    # 6. Run pywebview on the main thread (blocks forever because the window
    #    is never destroyed — it just hides/shows). Tray "Exit" calls
    #    os._exit(0) to terminate the process.
    webview.start()


if __name__ == "__main__":
    main()
