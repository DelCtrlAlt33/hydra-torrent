# Hydra Torrent Project

## Project Overview
Hydra Torrent is a custom BitTorrent client with a GUI built in Python using tkinter and libtorrent. It features:
- Clean, themed GUI with dark mode support
- Peer list with country flags and real-time stats
- Automatic media organization for Plex
- Two-stage download process (incomplete → complete → Plex)
- Magnet link support
- Resume/seeding functionality
- Standalone Windows installer for multi-user deployment
- PIA VPN kill switch with automatic IP leak prevention
- **NEW**: Headless daemon mode (FastAPI REST + WebSocket) with Windows system tray

## Current State (2026-02-21)

### Working Features
- ✅ Download torrents via magnet links
- ✅ Peer list with country flags (async loading to prevent UI freeze)
- ✅ Automatic move to Plex when complete (movies → M:\movies, TV → M:\tv)
- ✅ Resume torrents on restart without re-downloading
- ✅ Plex auto-scan via API when new media added (with detailed error logging)
- ✅ Theme system (dark/light modes)
- ✅ File icons and progress tracking
- ✅ Custom About dialog with Hydra logo and mission statement
- ✅ All dialogs use dark title bars and custom icon
- ✅ Standalone .exe installer package for girlfriend's computer
- ✅ PIA VPN kill switch — binds to VPN interface, pauses on disconnect, auto-resumes
- ✅ **Headless daemon (`hydra_daemon.py`) — FastAPI REST API + WebSocket streaming**
- ✅ **System tray icon (`hydra_tray.py`) — spawns daemon, live VPN status dot, right-click menu**

### Architecture (Current)
```
Main Desktop (192.168.20.2) - Port 6001
├── hydra_tray.py  ← Windows system tray (spawns daemon on login)
│   └── hydra_daemon.py  ← headless libtorrent engine + FastAPI on :8765
│       ├── REST API: http://127.0.0.1:8765  (auth: X-API-Key header)
│       ├── WebSocket: ws://127.0.0.1:8765/ws  (real-time transfer snapshots)
│       └── libtorrent bound to: wgpia0 (10.237.x.x) — VPN interface only
├── peer.pyw  ← original tkinter GUI (still works standalone)
├── Downloads to: C:\Users\Matth\hydra_torrent\downloads_incomplete (LOCAL)
├── Auto-moves to: \\192.168.20.4\Plex\movies or \tv (TrueNAS SMB)
└── Jackett: http://127.0.0.1:9117 (shared via ENABLE_JACKETT_SHARING.bat)

Girlfriend's Desktop (192.168.20.X) - Port 6002
├── Hydra Torrent (standalone .exe)
├── Downloads to: %LOCALAPPDATA%\HydraTorrent\downloads_incomplete
├── Auto-moves to: \\192.168.20.4\Plex\movies or \tv (shared TrueNAS)
└── Jackett: http://192.168.20.2:9117 (uses main Jackett instance)

TrueNAS (192.168.20.4)
├── \\192.168.20.4\Plex\movies
└── \\192.168.20.4\Plex\tv
└── Credentials: mediauser / [password]

R710 Proxmox (192.168.20.33)
└── Plex Container (LXC 100)
    └── Mounts TrueNAS via SMB at /mnt/smb-media
```

---

## Recent Work Completed

### Session 2026-02-21 (Evening): System Tray + Daemon

**Goal**: Run Hydra headlessly on startup with a system tray icon, no console window.

#### 1. Headless Daemon (`hydra_daemon.py` — new file)

Full libtorrent engine extracted from `peer.pyw`, wrapped in FastAPI. No tkinter dependency.
Runs independently; `peer.pyw` is unchanged and still works as a standalone GUI.

**Key design:**
- `DaemonStore` — thread-safe transfer state dict; strips internal fields before API responses
- `TorrentEngine` — libtorrent session, VPN kill switch, download/seeding monitors
- FastAPI app with lifespan startup/shutdown
- WebSocket broadcaster: drains `store.ws_queue`, pushes snapshots ≤10×/sec to all clients
- API key auto-generated on first run, saved to `hydra_config.json` as `daemon_api_key`

**REST endpoints (all require `X-API-Key` header):**
```
GET  /status                        → DaemonStatus (VPN, rates, torrent count)
GET  /vpn                           → VPNStatus
GET  /transfers                     → list of TransferState
GET  /transfers/{name}              → single TransferState
POST /transfers                     → add magnet (202, metadata fetch in background)
POST /transfers/{name}/pause        → pause
POST /transfers/{name}/resume       → resume
DEL  /transfers/{name}              → remove (?delete_files=true to wipe files)
POST /search                        → search (mode: online/jackett/local)
WS   /ws                            → real-time stream (send {"auth":"<key>"} within 5s)
GET  /docs                          → Swagger UI
```

**WebSocket auth protocol:**
1. Client connects to `ws://127.0.0.1:8765/ws`
2. Client sends `{"auth": "<api-key>"}` within 5 seconds
3. Server sends current snapshot immediately, then streams updates

**Pydantic models (`daemon_models.py` — new file):**
- `TransferState`, `AddMagnetRequest`, `SearchRequest`, `SearchResult`
- `VPNStatus`, `DaemonStatus`
- No imports from other hydra modules — safe to import anywhere

**Run the daemon:**
```bash
python hydra_daemon.py
# Listening on: http://127.0.0.1:8765
# API key:      <key>
# API docs:     http://127.0.0.1:8765/docs
```

#### 2. System Tray (`hydra_tray.py` — new file)

Thin wrapper: hides console, spawns `hydra_daemon.py`, shows tray icon with live status dot.

**Startup sequence:**
1. `_hide_console()` — hides console via `ctypes.windll` before any other code runs
2. Reads API key from `hydra_config.json` (may be empty on first run)
3. `_find_daemon_python()` — finds the Python interpreter that has `libtorrent` + `fastapi`
   (searches `sys.executable` first, then common `C:\Program Files\Python3xx\python.exe` paths)
4. Spawns `hydra_daemon.py` with `CREATE_NO_WINDOW` flag
5. Writes/refreshes Windows startup registry entry using `pythonw.exe` (no console on login)
6. Polls `GET /status` every 0.5s for up to 10s; re-reads API key after daemon writes it
7. Creates `pystray.Icon` and starts it on the main thread
8. Background thread polls `GET /status` every 5s → updates icon dot colour + VPN menu text

**Icon dot colours:**
| Colour | Meaning |
|---|---|
| Orange | Daemon starting up (first 10s) |
| Green | Daemon up + VPN connected |
| Red | VPN disconnected, or daemon offline / not responding |

**Right-click menu:**
```
● VPN: Protected (10.237.x.x)    ← dynamic, non-clickable (callable text, always fresh)
─────────────────────────────
  Open Web UI                    → opens http://127.0.0.1:8765/docs in browser
  Pause All                      → GET /transfers, POST /pause each active one
  Resume All                     → GET /transfers, POST /resume each paused one
─────────────────────────────
  Enable/Disable startup         → toggle Windows startup registry entry
─────────────────────────────
  Exit                           → terminate daemon subprocess, stop pystray
```

**Windows startup registry:**
```
HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run
"HydraTorrent" = '"C:\Program Files\Python311\pythonw.exe" "C:\...\hydra_tray.py"'
```
Uses `pythonw.exe` so no console window appears on login.
Entry is always refreshed on launch — corrects itself if previously written by wrong interpreter.

**Launch:**
```bash
# Normal launch (brief console flash):
python hydra_tray.py

# No console at all:
pythonw hydra_tray.py

# After first launch, auto-starts on Windows login via registry.
```

**Key implementation notes:**
- Menu item text is a callable (`lambda item: self._get_vpn_text()`) so it's always fresh on open
  without needing to call `update_menu()` manually
- `_find_daemon_python()` probe: `subprocess.run([exe, "-c", "import libtorrent, fastapi"])` — picks
  first interpreter that exits 0. Falls back to `sys.executable` if none found.
- Transfer name URL-encoding uses `urllib.parse.quote(name, safe='')` for pause/resume API calls
- Startup entry always uses the daemon Python's `pythonw.exe`, not necessarily `sys.executable`

#### 3. Dependency Added
`pystray` added to `requirements.txt`. Install in the Python that runs the tray:
```bash
python -m pip install pystray
```
(`Pillow` and `requests` were already present.)

---

### Session 2026-02-21 (Daytime): Security Hardening

**Goal**: Prevent IP leaks, add PIA kill switch, harden libtorrent settings.

#### 1. VPN Kill Switch (`vpn_guard.py` — new file)

Detects PIA's WireGuard adapter (`wgpia0`) using `psutil`. Monitors every 30 seconds in a
background daemon thread. Fires a callback when VPN connects or disconnects.

```python
from vpn_guard import VPNGuard

guard = VPNGuard(check_interval=30)
connected, iface, ip = guard.get_status()  # → (True, 'wgpia0', '10.237.x.x')
guard.start(on_change_callback)            # starts background monitor
```

Detection logic: find interface with "wgpia" or "pia" in name, `isup=True`, valid IPv4.

#### 2. libtorrent Bound to VPN Interface (`peer.pyw`)

**Before**: `listen_interfaces: '0.0.0.0:6001'` — all interfaces, real IP exposed if VPN drops.

**After**: `listen_interfaces: '10.237.x.x:6001'` — VPN IP only. Traffic cannot reach real NIC.

```python
listen_ip = self.vpn_ip if self.vpn_ip else '0.0.0.0'
self.ses = lt.session({
    'listen_interfaces': f'{listen_ip}:{PEER_PORT}',
    'enable_dht': True,
    'enable_lsd': False,       # LAN broadcast disabled
    'enable_upnp': False,      # UPnP disabled — was exposing real IP to router
    'enable_natpmp': False,    # NAT-PMP disabled
    'anonymous_mode': True,    # Hides client fingerprint from peers
    ...
})
```

#### 3. Startup Pre-Flight Dialog

If PIA is not connected when Hydra launches, a blocking dialog appears:
- **Check Again** — re-runs detection (connect PIA first, then click this)
- **Continue Without VPN** — proceeds, binds to `0.0.0.0`, shows red indicator
- **Exit** — closes the application

#### 4. Kill Switch on VPN Disconnect

When VPNGuard detects `wgpia0` going down:
1. `ses.apply_settings({'listen_interfaces': '127.0.0.1:PORT'})` — cuts all peer connections
2. All active torrent handles are paused (tagged `_vpn_paused`)
3. Footer indicator → red `VPN: EXPOSED`
4. Window title → `Hydra Torrent v0.1 — NO VPN`
5. Status log → `⚠ VPN DISCONNECTED — all downloads paused`

When VPN reconnects:
1. `ses.apply_settings({'listen_interfaces': 'NEW_IP:PORT'})` — rebinds to new VPN IP
2. All `_vpn_paused` torrents auto-resume
3. Footer indicator → green `VPN: Protected (10.x.x.x)`

#### 5. VPN Status Indicator (Footer)

Added to the bottom footer, right side:
- Green: `VPN: Protected (10.237.x.x)` — PIA connected, traffic is protected
- Red: `VPN: EXPOSED` — no VPN, all traffic goes over real IP

#### 6. Path Traversal Fix (`network.py`)

**Bug**: `file_path = os.path.join(SHARED_DIR, filename)` — a filename like `../../Windows/System32/SAM`
could escape SHARED_DIR and serve arbitrary files.

**Fix**:
```python
if not filename or '..' in filename or filename.startswith('/') or filename.startswith('\\'):
    # reject

safe_shared = os.path.realpath(SHARED_DIR)
file_path = os.path.realpath(os.path.join(SHARED_DIR, filename))
if not file_path.startswith(safe_shared + os.sep):
    # reject with "Invalid path"
```

---

### Session 2026-02-14 Evening: Installer Package Creation

**Goal**: Package Hydra Torrent for girlfriend's computer with one-click install.

#### 1. Created PyInstaller Build System
- Built standalone Windows .exe (~46MB) with all dependencies
- No Python installation required on target computer
- Bundles: libtorrent, ttkbootstrap, tkinter, PIL, maxminddb, all Python modules

#### 2. Fixed Resource Path Issues
**Solution**: Added `resource_path()` helper function in peer.pyw:
```python
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)
```

#### 3. Fixed Working Directory Issues
Updated config.py to detect PyInstaller and use AppData:
```python
def get_base_dir():
    if getattr(sys, 'frozen', False):
        app_data = os.path.join(os.environ['LOCALAPPDATA'], 'HydraTorrent')
        os.makedirs(app_data, exist_ok=True)
        return app_data
    else:
        return os.path.dirname(os.path.abspath(__file__))
```

#### 4. Created Complete Installer Package (`installer_package/`)
1. **HydraTorrent.exe** (46MB) — standalone, no Python needed
2. **INSTALL.bat** — prompts for Jackett key + TrueNAS creds, maps drives, creates shortcut
3. **CLEANUP_OLD_INSTALL.bat** — removes old install
4. **TEST_PLEX_CONNECTION.bat** — verifies Plex API token and connectivity
5. **README.txt** — installation instructions and troubleshooting

#### 5. Jackett Network Sharing
`ENABLE_JACKETT_SHARING.bat` — opens port 9117, sets `AllowExternal: true`, restarts Jackett.

---

## File Structure

### Core Application
- `peer.pyw` — Original tkinter GUI (standalone, still works)
- `hydra_daemon.py` — Headless libtorrent engine + FastAPI REST + WebSocket
- `hydra_tray.py` — Windows system tray wrapper (spawns daemon, live status icon)
- `daemon_models.py` — Pydantic v2 models for daemon REST API (no hydra deps)
- `vpn_guard.py` — PIA VPN detection and kill switch monitor
- `config.py` — Configuration (paths, logging, network settings, PyInstaller detection)
- `media_organizer.py` — Automatic categorization (movies vs TV) + Plex API
- `theme_manager.py` — Dark/light theme system
- `transfer_manager.py` — Download progress tracking (used by peer.pyw)
- `search.py` — Jackett/public torrent search
- `download.py` — Download handling
- `network.py` — Async peer file server (TLS)
- `certs.py` — SSL certificate handling

### Installer Files
- `build_installer.py` — PyInstaller build script
- `installer_package/` — Complete installer package (excluded from git)
- `ENABLE_JACKETT_SHARING.bat` — Enable Jackett network access on main computer

### Plex Utilities (One-time cleanup scripts)
- `plex_smart_cleanup.py` — Comprehensive movie cleanup
- `plex_tv_cleanup.py` — TV show organization
- `plex_quick_fixes.py` — Targeted manual fixes
- `split_trilogies.py` — Split trilogy packs

### Configuration Files (in AppData when running as .exe)
- `hydra_config.json` — Plex URL, API token, Jackett settings, `daemon_api_key`
- `transfers.json` — Active/completed transfers state

---

## Important Configuration

### Daemon Config (`hydra_config.json` fields)
```json
{
  "daemon_host": "127.0.0.1",
  "daemon_port": 8765,
  "daemon_api_key": "<auto-generated on first run>"
}
```
To allow LAN access to the daemon API: set `"daemon_host": "0.0.0.0"`.

### libtorrent Session Config (Current — post security hardening)
```python
self.ses = lt.session({
    'listen_interfaces': f'{vpn_ip}:{PEER_PORT}',  # VPN IP, not 0.0.0.0
    'enable_dht': True,
    'enable_lsd': False,       # disabled
    'enable_upnp': False,      # disabled
    'enable_natpmp': False,    # disabled
    'anonymous_mode': True,    # enabled
    'connections_limit': 200,
    'download_rate_limit': 0,
    'upload_rate_limit': 0,
})
```

**Note**: If PIA is not connected at startup and user clicks "Continue Without VPN",
`listen_ip` falls back to `0.0.0.0`. This is intentional — the user explicitly acknowledged the risk.

### PIA VPN Adapter (Confirmed)
- Adapter name: `wgpia0` (WireGuard Tunnel)
- IP range: `10.237.x.x` (varies per session/server)
- Detection: `psutil.net_if_addrs()` — look for interface with "wgpia"/"pia" in name, `isup=True`

### Girlfriend's Config (Auto-created by INSTALL.bat)
```json
{
  "peer_port": 6002,
  "jackett_url": "http://192.168.20.2:9117",
  "jackett_api_key": "[entered during install]",
  "plex_url": "http://192.168.20.33:32400",
  "plex_token": "[token]",
  "auto_move_to_plex": true,
  "search_mode": "jackett"
}
```

### TrueNAS Permissions
```bash
chown -R mediauser:mediauser /mnt/MainPool/Plex
chmod -R 775 /mnt/MainPool/Plex
```

---

## Known Issues & Solutions

### Issue: Tray spawns daemon with wrong Python interpreter
**Cause**: `_find_daemon_python()` searches a fixed candidate list. If Python 3.11 is installed
elsewhere, it may fall back to `sys.executable` which might not have `libtorrent`.
**Fix**: The tray still works if the daemon is already running — `_wait_for_daemon()` will find it.
For cold-start, launch `hydra_tray.py` with the Python that has all deps:
`"C:\Program Files\Python311\python.exe" hydra_tray.py`

### Issue: Downloads slow / no peers after VPN hardening
**Cause**: libtorrent is now bound to VPN interface only. If PIA is slow or routes change, fewer peers connect.
**Fix**: This is expected and correct behavior. Try a different PIA server location.

### Issue: "No VPN Detected" dialog on startup even though PIA is connected
**Cause**: PIA may still be initializing the `wgpia0` adapter.
**Fix**: Click "Check Again" after a few seconds.

### Issue: Downloads don't auto-resume after VPN reconnect
**Cause**: The `_vpn_paused` flag is only set on torrents active at disconnect time.
**Fix**: Manually resume from the transfers list.

### Issue: Files Appearing on Desktop
**Status**: ✅ FIXED — config.py uses `%LOCALAPPDATA%\HydraTorrent\` when running as .exe

### Issue: Icons Not Loading in .exe
**Status**: ✅ FIXED — `resource_path()` helper handles PyInstaller paths

### Issue: Desktop Shortcut Blank/Not Working
**Status**: ⚠️ PARTIAL FIX — create manually if needed:
Right-click Desktop → New → Shortcut → `%LOCALAPPDATA%\HydraTorrent\HydraTorrent.exe`

### Issue: Jackett Search Not Working
1. Run `ENABLE_JACKETT_SHARING.bat` on main computer
2. Add API key to hydra_config.json
3. Restart Hydra Torrent

### Issue: Plex Not Auto-Scanning
Run `TEST_PLEX_CONNECTION.bat` — check plex_token, connectivity to 192.168.20.33:32400, firewall port 32400.

---

## Troubleshooting

### Tray Icon Not Appearing
1. Check if `hydra_tray.py` is running: Task Manager → python.exe processes
2. Check if `pystray` is installed: `python -m pip show pystray`
3. Run with console: `python hydra_tray.py` to see startup output
4. If daemon failed to spawn: launch daemon manually (`python hydra_daemon.py`), then tray will find it

### Daemon Not Responding
```bash
# Check if daemon is running
python -c "import requests, json; cfg=json.load(open('hydra_config.json')); print(requests.get('http://127.0.0.1:8765/status', headers={'X-API-Key': cfg['daemon_api_key']}, timeout=3).json())"
# If not running, start manually:
python hydra_daemon.py
```

### Downloads Not Working After Security Hardening
- Verify PIA is connected — check tray icon (green dot) or footer for green `VPN: Protected` label
- If red dot/label: connect PIA, Hydra will auto-resume within 30 seconds
- Check libtorrent is binding to VPN IP in logs: `TLS Peer server listening on 10.237.x.x:6001`

### Downloads Not Working (General)
- Verify downloads are to LOCAL disk, not SMB share
- Check firewall: port open (6001 for main, 6002 for girlfriend)
- Check tracker response in logs

### Completed Torrents Re-download on Restart
- Verify `plex_path` is saved in transfers.json
- Check resume logic uses `plex_path` for save_path

### Permission Denied Moving to Plex
- Check TrueNAS ownership: `chown -R mediauser:mediauser /mnt/MainPool/Plex`
- Verify network drive is mapped with credentials

---

## Development Notes

### VPN Guard Module
```python
# vpn_guard.py — standalone, no GUI imports
from vpn_guard import VPNGuard, detect_pia_interface

iface, ip = detect_pia_interface()  # ('wgpia0', '10.237.x.x') or (None, None)

guard = VPNGuard(check_interval=30)
connected, iface, ip = guard.get_status()
guard.start(lambda c, i, ip: callback(c, i, ip))  # fires on status change
guard.stop()
```

### Daemon API Key Flow
1. `hydra_daemon.py` starts → `_get_or_create_api_key()` reads `hydra_config.json`
2. If `daemon_api_key` absent: generates `secrets.token_urlsafe(32)`, saves to config
3. `hydra_tray.py` reads the key from config after daemon has started
4. All API calls include `X-API-Key: <key>` header

### PyInstaller Build — Add new files
`hydra_daemon.py`, `hydra_tray.py`, `daemon_models.py` will be bundled automatically.
`pystray` and `psutil` may need hidden imports:
```bash
pyinstaller ... --hidden-import=psutil --hidden-import=pystray peer.pyw
```

### Why libtorrent is Bound to VPN IP, Not 0.0.0.0
Binding to `0.0.0.0` means libtorrent uses whichever interface the OS picks — usually the fastest,
which is the real Ethernet NIC. If PIA drops, the OS silently falls back to the real NIC and your
IP is exposed to every peer in the swarm. Binding to the VPN IP means libtorrent literally cannot
reach peers if the VPN interface goes away.

### Why Local Downloads Then Move?
libtorrent has issues with SMB/network shares (unreliable file locking, poor performance, tracker
timeouts). Download to local disk first, move to TrueNAS when complete.

### Detecting PyInstaller Environment
```python
if getattr(sys, 'frozen', False):
    app_data = os.path.join(os.environ['LOCALAPPDATA'], 'HydraTorrent')
else:
    script_dir = os.path.dirname(os.path.abspath(__file__))
```

---

## Next Steps

### IMMEDIATE
1. Test tray right-click menu: Pause All / Resume All / Open Web UI
2. Test tray VPN dot: disconnect PIA → icon should turn red within 5s; reconnect → green
3. Test cold-start: log out and back in → tray icon should appear automatically

### TODO: Future Improvements
- Build a simple web UI (React or plain HTML) that talks to the daemon REST/WebSocket API
- Rebuild installer .exe to include daemon + tray (`hydra_tray.exe` as the entry point)
- Add VPN kill switch to girlfriend's installer config (she'll need PIA too, or bypass)
- Fix desktop shortcut creation (currently requires manual creation sometimes)
- Add logging viewer in GUI for troubleshooting
- Create uninstaller script (remove registry entry + AppData)
- Consider signed executable to avoid Windows Defender warnings

### FUTURE: Migrate to R710 Server (Long-term)
Run `hydra_daemon.py` on the R710 with `daemon_host: "0.0.0.0"` — any device on the LAN
can control downloads via the REST API or web UI. Removes per-client VPN enforcement entirely.

---

## Git Status
- Uncommitted changes: `hydra_tray.py` (new), `daemon_models.py` (new), `hydra_daemon.py` (new),
  `requirements.txt` (+pystray), `peer.pyw` (VPN label fixes)
- Latest commits:
  - `3479b85` — Reposition VPN label to float in tab bar after Transfers tab
  - `5c996d1` — Fix AttributeError: init vpn_ip before tab creation
  - `99eaf14` — Update CLAUDE.md for 2026-02-21 security hardening session
  - `a406c22` — Add PIA VPN kill switch and security hardening

## Questions to Ask When Returning
1. "Is the tray icon working? Green dot when PIA is connected?"
2. "Does the icon turn red when PIA disconnects, and green when it reconnects?"
3. "Does the tray auto-start after logging out and back in?"
4. "Did the installer work on girlfriend's computer? Does she have PIA?"
5. "Any issues with Jackett search or Plex auto-scan?"

---
Last updated: 2026-02-21
