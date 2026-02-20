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
- **NEW**: PIA VPN kill switch with automatic IP leak prevention

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
- ✅ **PIA VPN kill switch — binds to VPN interface, pauses on disconnect, auto-resumes**

### Architecture (Current)
```
Main Desktop (192.168.20.2) - Port 6001
├── Hydra Torrent GUI (peer.pyw)
├── libtorrent bound to: wgpia0 (10.237.x.x) — VPN interface only
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

### Session 2026-02-21: Security Hardening

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
# Detect VPN at startup, bind to its IP
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
- `peer.pyw` - Main GUI application (tkinter)
- `vpn_guard.py` - PIA VPN detection and kill switch monitor
- `config.py` - Configuration (paths, logging, network settings, PyInstaller detection)
- `media_organizer.py` - Automatic categorization (movies vs TV) + Plex API
- `theme_manager.py` - Dark/light theme system
- `transfer_manager.py` - Download progress tracking
- `search.py` - Jackett/public torrent search
- `download.py` - Download handling
- `network.py` - Async peer file server (TLS)
- `certs.py` - SSL certificate handling

### Installer Files
- `build_installer.py` - PyInstaller build script
- `installer_package/` - Complete installer package (excluded from git)
- `ENABLE_JACKETT_SHARING.bat` - Enable Jackett network access on main computer

### Plex Utilities (One-time cleanup scripts)
- `plex_smart_cleanup.py` - Comprehensive movie cleanup
- `plex_tv_cleanup.py` - TV show organization
- `plex_quick_fixes.py` - Targeted manual fixes
- `split_trilogies.py` - Split trilogy packs

### Configuration Files (in AppData when running as .exe)
- `hydra_config.json` - Plex URL, API token, Jackett settings
- `transfers.json` - Active/completed transfers state

---

## Important Configuration

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

### Downloads Not Working After Security Hardening
- Verify PIA is connected — check footer for green `VPN: Protected` label
- If red label: connect PIA, Hydra will auto-resume within 30 seconds
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

### PyInstaller Build — Add vpn_guard.py
`vpn_guard.py` will be bundled automatically by PyInstaller since it's a local import.
psutil must be added as a hidden import if not auto-detected:
```bash
pyinstaller ... --hidden-import=psutil peer.pyw
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
1. Test VPN kill switch: start a download → disconnect PIA → verify downloads pause within 30s
2. Test auto-resume: reconnect PIA → verify downloads resume automatically
3. Test startup dialog: close PIA → launch Hydra → verify dialog appears

### TODO: Future Improvements
- Rebuild installer .exe to include security hardening (need to re-run PyInstaller)
- Add VPN kill switch to girlfriend's installer config (she'll need PIA too, or bypass)
- Fix desktop shortcut creation (currently requires manual creation sometimes)
- Add logging viewer in GUI for troubleshooting
- Create uninstaller script
- Consider signed executable to avoid Windows Defender warnings

### FUTURE: Migrate to R710 Server (Long-term)
Server-side torrent downloading removes the need for per-client VPN enforcement entirely.

---

## Git Status
- Clean working tree (all changes committed)
- Latest commit: `a406c22` — VPN kill switch + security hardening

## Questions to Ask When Returning
1. "Did the VPN kill switch work correctly? Did downloads pause when PIA disconnected?"
2. "Did the installer work on girlfriend's computer? Does she have PIA?"
3. "Any issues with Jackett search or Plex auto-scan?"
4. "Any Windows Firewall warnings or issues?"

---
Last updated: 2026-02-21
