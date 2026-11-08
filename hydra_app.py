#!/usr/bin/env python3
"""
Hydra Torrent - Desktop App Launcher

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
# Paths / config
# ---------------------------------------------------------------------------
# Defined before the single-instance check below, which needs _load_config()
# to reach the already-running daemon's own API.

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
# Single-instance enforcement
# ---------------------------------------------------------------------------
# If another instance is already running, show its window and exit.
#
# The raw ShowWindow/SetForegroundWindow approach used to be the whole
# story here, but it silently does nothing once the window's been closed
# to tray at least once: pywebview's WinForms .Hide() leaves the window
# DWM-cloaked (confirmed live: DWMWA_CLOAKED reads 1 even though
# IsWindowVisible still reports 1), and cloak state can only be cleared
# by the owning process - DwmSetWindowAttribute from here returns
# E_ACCESSDENIED (0x80070005), confirmed live against a real cloaked
# window. No combination of window-handle tricks from outside the
# process can undo that. The only thing that actually works is asking
# the *other* process to call its own window.show()/.restore() from
# inside itself - which is exactly what the tray's "Open" menu item
# already does via _open_callback. So this hits that same already-
# running daemon's own local API (stdlib urllib only - avoid importing
# the heavier `requests`/`webview` this early, since this path exits
# immediately in the common case and shouldn't pay for imports it never
# uses) to trigger the same callback from the process that actually
# owns the window. Best-effort: if the daemon isn't reachable for any
# reason, still try the old raw restore as a harmless fallback (it does
# work for a plain-minimized, never-hidden-to-tray window) before exiting.

_MUTEX_NAME = "HydraTorrentSingleInstance"
_mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    try:
        import urllib.request
        _cfg = _load_config()
        _scheme = 'https' if _cfg.get('daemon_use_ssl', False) else 'http'
        _host = _cfg.get('daemon_host', '127.0.0.1')
        if _host == '0.0.0.0':
            _host = '127.0.0.1'
        _port = int(_cfg.get('daemon_port', 8765))
        _api_key = _cfg.get('daemon_api_key', '')
        _req = urllib.request.Request(
            f"{_scheme}://{_host}:{_port}/window/show",
            method="POST",
            headers={"X-API-Key": _api_key},
        )
        if _scheme == 'https':
            import ssl
            _ctx = ssl.create_default_context()
            _ctx.check_hostname = False
            _ctx.verify_mode = ssl.CERT_NONE
            urllib.request.urlopen(_req, timeout=3, context=_ctx)
        else:
            urllib.request.urlopen(_req, timeout=3)
    except Exception:
        # Fallback: works for a plain-minimized window, harmless no-op
        # for a cloaked one. Only a window owned by another running copy of
        # Hydra: a title alone also matches other programs (a File Explorer tab
        # on a folder called "Hydra Torrent" has a window with that exact
        # title), and restoring and focusing that would be wrong.
        try:
            from window_lookup import find_window
            hwnd = find_window("Hydra Torrent",
                               exe_prefix="hydratorrent" if getattr(sys, 'frozen', False) else "python")
        except Exception:
            hwnd = 0
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    sys.exit(0)

import requests
import webview

from cert_pinning import verify_daemon_identity

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


def _save_config(cfg: dict) -> None:
    try:
        with open(_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f)
    except Exception:
        pass


def _verify_daemon_identity(cfg: dict, host: str, port) -> bool:
    """Wraps cert_pinning.verify_daemon_identity: prints the result and
    persists a newly-pinned fingerprint. See that module's docstring for
    why this exists - the short version is that verify=False on its own
    accepts any certificate, and daemon_host is user-configurable."""
    trusted, updated_cfg, message = verify_daemon_identity(cfg, host, port)
    if message:
        print(f"[app] {message}", file=sys.stderr if not trusted else sys.stdout)
    if updated_cfg is not cfg:
        cfg.update(updated_cfg)
        _save_config(cfg)
    return trusted


# ---------------------------------------------------------------------------
# First-run self-install
# ---------------------------------------------------------------------------

def _needs_install() -> bool:
    """True if there's no installed copy yet, or the exe currently running
    isn't the installed one - i.e. this is a freshly downloaded build the
    user launched directly (from Downloads, an extracted zip, etc.)
    rather than via the Desktop/Start Menu shortcut. Either way the
    install dir needs to be (re)synced, otherwise the shortcut keeps
    pointing at whatever version happened to install first and silently
    never gets updated, no matter how many newer releases get run."""
    installed_exe = os.path.join(_INSTALL_DIR, 'HydraTorrent.exe')
    if not os.path.isfile(installed_exe):
        return True
    running_exe = sys.executable if getattr(sys, 'frozen', False) else __file__
    return os.path.abspath(running_exe) != os.path.abspath(installed_exe)


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
    for asset in ('image8.ico', 'geoip-country.mmdb'):
        src = os.path.join(_BUNDLE_DIR, asset) if getattr(sys, 'frozen', False) else os.path.join(_EXE_DIR, asset)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(_INSTALL_DIR, asset))

    # Create default config if none exists
    if not os.path.isfile(_CONFIG_FILE):
        with open(_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                "daemon_host": "127.0.0.1",
                "daemon_port": 8765,
                "daemon_use_ssl": False,
                "desktop_mode": True,
                "search_mode": "jackett",
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

    # Add firewall rules (best-effort, requires admin - silently skip if not admin)
    try:
        # libtorrent listens on config.PEER_PORT (6001) for both peer TCP and DHT
        # UDP. Older builds opened 6002 and 6881, which nothing listens on, so
        # inbound peers were blocked. Remove those stale rules, then add the
        # real port. Literal here because importing config this early has
        # side effects (see the single-instance note above); kept in step with
        # config.PEER_PORT by tests/test_firewall_port.py.
        peer_port = '6001'
        for stale in ('Hydra Torrent', 'Hydra Torrent DHT'):
            subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'delete', 'rule', f'name={stale}'],
                capture_output=True, timeout=5,
            )
        for proto in ('TCP', 'UDP'):
            subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'add', 'rule',
                 'name=Hydra Torrent', 'dir=in', 'action=allow',
                 f'protocol={proto}', f'localport={peer_port}', 'profile=any',
                 'description=Hydra Torrent BitTorrent client'],
                capture_output=True, timeout=5,
            )
    except Exception:
        pass  # not admin - firewall rules skipped, user will get the Windows prompt instead


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
        # Same certificate files the standalone daemon uses (certs.py writes
        # fullchain.pem/privkey.pem under BASE_DIR/certs). This used to look
        # for hydra.crt/hydra.key, which nothing creates, so daemon_use_ssl
        # silently fell back to plain HTTP.
        try:
            from certs import ensure_certificates
            from config import FULLCHAIN_PATH, PRIVKEY_PATH
            ensure_certificates()
            ssl_args = {'ssl_certfile': str(FULLCHAIN_PATH), 'ssl_keyfile': str(PRIVKEY_PATH)}
        except Exception as e:
            print(f"[app] SSL certs unavailable ({e}), falling back to HTTP", file=sys.stderr)
            use_ssl = False

    uvicorn.run(
        "hydra_daemon:app",
        host=host,
        port=port,
        log_level="info",
        **ssl_args,
    )


def _wait_for_daemon(url: str, api_key: str, cfg: dict, host: str, port,
                      timeout: float = 15.0) -> bool:
    """Poll the daemon until it responds or timeout is reached.

    Returns False (refuses to proceed) if the daemon does come up but its
    TLS certificate doesn't match what was pinned on an earlier run - see
    _verify_daemon_identity.
    """
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
                return _verify_daemon_identity(cfg, host, port)
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

    # WebView2's embedded Chromium process doesn't necessarily inherit the
    # same color-management/rasterization path a standalone Edge/Chrome tab
    # uses - reported as the UI looking visibly duller/less saturated in the
    # wrapped desktop window than the identical page in a real browser.
    # Must be set before the window (and its underlying WebView2 browser
    # process) is created, so this goes as early in main() as possible.
    os.environ.setdefault(
        'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS',
        '--force-color-profile=srgb --enable-gpu-rasterization',
    )

    # Suppress InsecureRequestWarning
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    # 0. First-run self-install / update-in-place if a newer exe was just run
    if getattr(sys, 'frozen', False) and _needs_install():
        _self_install()

    # 1. Load config
    cfg = _load_config()
    host = cfg.get('daemon_host', '127.0.0.1')
    port = int(cfg.get('daemon_port', 8765))
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
    if not _wait_for_daemon(_daemon_url, api_key, cfg, host, port):
        print("[app] ERROR: daemon failed to start within 15s, or its TLS "
              "certificate didn't match what was pinned on an earlier run "
              "(see the WARNING above)", file=sys.stderr)
        # Still try to open - the page will show an error but at least the app opens
    else:
        print("[app] Daemon is ready")

    # 4. Create the frameless pywebview window
    from webview_api import ResizeAPI, apply_frameless

    global _window
    api = ResizeAPI(hide_on_close=True)

    w = webview.create_window(
        'Hydra Torrent',
        url=f'{_daemon_url}/ui',
        # Fallback only - the frontend measures its own real content
        # height on first load and calls pywebview.api.size_to_content()
        # to resize precisely (see checkForUpdate's caller in
        # static/index.html). Maximizing was tried and reverted: the
        # Transfers pane has a deliberate max-width so it doesn't stretch
        # absurdly wide on large monitors, which just left a big empty
        # void past that cap instead. Width here matches that same
        # max-width plus margin, so even if JS resize never fires this
        # still looks reasonable rather than randomly guessed.
        width=1440,
        height=900,
        min_size=(800, 500),
        frameless=True,
        easy_drag=False,
        js_api=api,
        background_color='#060606',
    )
    api.set_window(w)
    apply_frameless(w)
    _window = w

    # Registers _open_callback with the already-running daemon module
    # (imported in-process by the uvicorn thread above) so a second,
    # short-lived process launched via the shortcut can ask *this*
    # process's daemon to show the window from the inside - see the
    # single-instance handling near the top of this file for why that's
    # necessary instead of poking the window handle from outside.
    try:
        import hydra_daemon
        hydra_daemon.set_show_window_callback(_open_callback)
    except Exception:
        pass

    # Intercept Alt+F4 / taskbar close - hide instead of destroy
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
    #    is never destroyed - it just hides/shows). Tray "Exit" calls
    #    os._exit(0) to terminate the process.
    webview.start()


if __name__ == "__main__":
    main()
