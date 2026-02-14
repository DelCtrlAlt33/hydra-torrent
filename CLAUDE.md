# Hydra Torrent Project

## Project Overview
Hydra Torrent is a custom BitTorrent client with a GUI built in Python using tkinter and libtorrent. It features:
- Clean, themed GUI with dark mode support
- Peer list with country flags and real-time stats
- Automatic media organization for Plex
- Two-stage download process (incomplete → complete → Plex)
- Magnet link support
- Resume/seeding functionality
- **NEW**: Standalone Windows installer for multi-user deployment

## Current State (2026-02-14 Evening)

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
- ✅ **Standalone .exe installer package for girlfriend's computer**

### Architecture (Current)
```
Main Desktop (192.168.20.2) - Port 6001
├── Hydra Torrent GUI (peer.pyw)
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

## Recent Work Completed

### Session 2026-02-14 Evening: Installer Package Creation

**Goal**: Package Hydra Torrent for girlfriend's computer with one-click install.

#### 1. Created PyInstaller Build System
- Built standalone Windows .exe (~46MB) with all dependencies
- No Python installation required on target computer
- Bundles: libtorrent, ttkbootstrap, tkinter, PIL, maxminddb, all Python modules

#### 2. Fixed Resource Path Issues
**Problem**: PyInstaller .exe couldn't find bundled resources (icons, GeoIP database)

**Solution**: Added `resource_path()` helper function in peer.pyw:
```python
def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        base_path = sys._MEIPASS  # PyInstaller temp folder
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)
```

Updated all resource loads:
- `image8.ico` → `resource_path("image8.ico")`
- `GeoLite2-Country.mmdb` → `resource_path("GeoLite2-Country.mmdb")`

#### 3. Fixed Working Directory Issues
**Problem**: When running as .exe, files (transfers.json, config, etc.) were being created on desktop instead of app directory.

**Solution**: Updated config.py to detect PyInstaller and use AppData:
```python
def get_base_dir():
    """Get the correct base directory for data files"""
    if getattr(sys, 'frozen', False):
        # Running as compiled .exe - use AppData
        app_data = os.path.join(os.environ['LOCALAPPDATA'], 'HydraTorrent')
        os.makedirs(app_data, exist_ok=True)
        return app_data
    else:
        # Running as script - use script directory
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()
```

Now all files go to: `%LOCALAPPDATA%\HydraTorrent\`

#### 4. Created Complete Installer Package

**Contents of `installer_package/` folder (55MB):**

1. **HydraTorrent.exe** (46MB)
   - Standalone executable
   - Includes all dependencies
   - Works on any Windows 10/11 machine

2. **INSTALL.bat**
   - Complete automated installer
   - Prompts for Jackett API key
   - Prompts for TrueNAS credentials
   - Maps network drives
   - Adds Windows Firewall rules (ports 6002, 6881)
   - Creates desktop shortcut
   - Pre-configures port 6002 (won't conflict with main instance on 6001)

3. **CLEANUP_OLD_INSTALL.bat**
   - Removes old installation
   - Cleans up stray files on desktop
   - Prepares for fresh install

4. **TEST_PLEX_CONNECTION.bat**
   - Tests Plex server connectivity
   - Verifies API token works
   - Tests library scan trigger
   - Diagnoses auto-scan issues

5. **README.txt**
   - Complete installation instructions
   - Troubleshooting guide
   - Manual configuration examples
   - Correct JSON format for config

6. **image8.ico** - Hydra logo (401KB)
7. **GeoLite2-Country.mmdb** - GeoIP database (9.2MB)

#### 5. Improved UI Consistency
- Replaced all `messagebox` and `simpledialog` with custom styled dialogs
- All popups now have dark title bar and Hydra icon
- Created comprehensive About dialog with mission statement
- Removed unused `simpledialog` import

#### 6. Enhanced Plex Auto-Scan
**Improved error logging in media_organizer.py:**
- ✓ "Plex library scan triggered successfully"
- ✗ "Invalid token" (401 error)
- ✗ "Connection timeout"
- ✗ "Cannot connect to Plex server"

Now provides actionable error messages when auto-scan fails.

#### 7. Jackett Network Sharing
Created `ENABLE_JACKETT_SHARING.bat` for main computer:
- Opens port 9117 in Windows Firewall
- Configures Jackett to allow external access (`AllowExternal: true`)
- Restarts Jackett service
- Allows girlfriend's computer to use main Jackett instance at http://192.168.20.2:9117

### Installation Process (What User Does)

**On Main Computer (192.168.20.2):**
```
1. Right-click ENABLE_JACKETT_SHARING.bat → Run as Administrator
   (Allows girlfriend to use your Jackett)
```

**On Girlfriend's Computer:**
```
1. Copy entire installer_package folder
2. Right-click CLEANUP_OLD_INSTALL.bat → Run (if reinstalling)
3. Right-click INSTALL.bat → Run as Administrator
4. Enter Jackett API key when prompted (from http://192.168.20.2:9117)
5. Enter TrueNAS credentials (mediauser / password)
6. Done! Desktop shortcut appears
```

**Testing:**
```
Run TEST_PLEX_CONNECTION.bat to verify Plex auto-scan will work
```

## File Structure

### Core Application
- `peer.pyw` - Main GUI application (tkinter)
- `config.py` - Configuration (paths, logging, network settings, PyInstaller detection)
- `media_organizer.py` - Automatic categorization (movies vs TV) + Plex API
- `theme_manager.py` - Dark/light theme system
- `transfer_manager.py` - Download progress tracking
- `search.py` - Jackett/public torrent search
- `download.py` - Download handling
- `certs.py` - SSL certificate handling

### Installer Files
- `build_installer.py` - PyInstaller build script
- `installer_package/` - Complete installer package
  - `HydraTorrent.exe`
  - `INSTALL.bat`
  - `CLEANUP_OLD_INSTALL.bat`
  - `TEST_PLEX_CONNECTION.bat`
  - `README.txt`
  - `image8.ico`
  - `GeoLite2-Country.mmdb`
- `ENABLE_JACKETT_SHARING.bat` - Enable Jackett network access

### Plex Utilities (One-time cleanup scripts)
- `plex_smart_cleanup.py` - Comprehensive movie cleanup
- `plex_tv_cleanup.py` - TV show organization
- `plex_quick_fixes.py` - Targeted manual fixes
- `split_trilogies.py` - Split trilogy packs

### Configuration Files (in AppData when running as .exe)
- `hydra_config.json` - Plex URL, API token, Jackett settings
- `transfers.json` - Active/completed transfers state

### Data Directories (in AppData when running as .exe)
- `downloads_incomplete/` - Active downloads
- `downloads_complete/` - Completed before Plex move
- `flags/` - Cached country flag images

## Important Configuration

### config.py Settings (Auto-detected)
```python
# BASE_DIR changes based on environment:
# - Development: C:\Users\Matth\hydra_torrent
# - Installed .exe: C:\Users\[User]\AppData\Local\HydraTorrent

# Download directories (local - fast and reliable for libtorrent)
DOWNLOAD_DIR_INCOMPLETE = os.path.join(BASE_DIR, 'downloads_incomplete')
DOWNLOAD_DIR_COMPLETE = os.path.join(BASE_DIR, 'downloads_complete')

# Plex media directories - map to TrueNAS Plex library via SMB
MEDIA_DIR_MOVIES = r'\\192.168.20.4\Plex\movies'
MEDIA_DIR_TV = r'\\192.168.20.4\Plex\tv'

# Network settings
PEER_PORT = 6001  # Main instance
# Girlfriend's instance uses 6002 (set in her hydra_config.json)
```

### libtorrent Session Config
```python
self.ses = lt.session({
    'listen_interfaces': '0.0.0.0:6001',  # MUST be 0.0.0.0, not specific IP
    'enable_dht': True,
    'enable_lsd': True,
    'enable_upnp': True,
    'enable_natpmp': True,
})
```

### Girlfriend's Config (Auto-created by INSTALL.bat)
```json
{
  "peer_port": 6002,
  "jackett_url": "http://192.168.20.2:9117",
  "jackett_api_key": "[entered during install]",
  "plex_url": "http://192.168.20.33:32400",
  "plex_token": "8Jkzcq8frQYqxELDQV3K",
  "auto_move_to_plex": true,
  "search_mode": "jackett"
}
```

### TrueNAS Credentials (Needed for Install)
```
Username: mediauser
Password: [actual password]
```

### TrueNAS Permissions
```bash
# On TrueNAS
chown -R mediauser:mediauser /mnt/MainPool/Plex
chmod -R 775 /mnt/MainPool/Plex
```

## Known Issues & Solutions

### Issue: Files Appearing on Desktop
**Status**: ✅ FIXED
- **Was**: PyInstaller .exe created files on desktop
- **Fix**: Updated config.py to use `%LOCALAPPDATA%\HydraTorrent\` when running as .exe

### Issue: Icons Not Loading in .exe
**Status**: ✅ FIXED
- **Was**: image8.ico and GeoLite2-Country.mmdb not found
- **Fix**: Added `resource_path()` helper for PyInstaller resource loading

### Issue: Desktop Shortcut Blank/Not Working
**Status**: ⚠️ PARTIAL FIX
- **Workaround**: Manual shortcut creation documented in README.txt
- May need to create shortcut manually: Right-click Desktop → New → Shortcut → Browse to `%LOCALAPPDATA%\HydraTorrent\HydraTorrent.exe`

### Issue: Jackett Search Not Working
**Causes**:
1. API key not in config
2. Can't reach Jackett at http://192.168.20.2:9117
3. Jackett not configured for external access

**Fix**:
1. Run `ENABLE_JACKETT_SHARING.bat` on main computer
2. Add API key to hydra_config.json
3. Restart Hydra Torrent

### Issue: Can't Access TrueNAS Share
**Error**: `[WinError 1326] The user name or password is incorrect`

**Fix**:
- Run INSTALL.bat again and enter correct credentials
- Or manually: `net use \\192.168.20.4\Plex /user:mediauser PASSWORD /persistent:yes`

### Issue: Plex Not Auto-Scanning
**Diagnosis**: Run `TEST_PLEX_CONNECTION.bat`

**Common causes**:
1. plex_token not in config
2. Can't reach Plex server at 192.168.20.33:32400
3. Firewall blocking port 32400
4. Token expired/invalid

**Fix**: Check logs for detailed error messages (now includes ✓/✗ indicators)

## Troubleshooting

### Downloads Not Working
- Check `listen_interfaces` is `0.0.0.0:[port]` (not a specific IP)
- Verify downloads are to LOCAL disk, not SMB share
- Check firewall: port open (6001 for main, 6002 for girlfriend)
- Check tracker response in logs

### Completed Torrents Re-download on Restart
- Verify `plex_path` is saved in transfers.json
- Check resume logic uses `plex_path` for save_path
- Look for "Resuming download..." in logs

### UI Freezing When Clicking Transfers
- ✅ FIXED: Flag downloads are now async
- If still freezing, clear flag cache

### Permission Denied Moving to Plex
- Check TrueNAS ownership: `chown -R mediauser:mediauser /mnt/MainPool/Plex`
- Check permissions: `chmod -R 775 /mnt/MainPool/Plex`
- Verify network drive is mapped with credentials

## Development Notes

### PyInstaller Build Process
```bash
# Build the installer
cd C:\Users\Matth\hydra_torrent
python build_installer.py

# Output: installer_package/ folder with all files
```

### Rebuilding After Code Changes
```bash
rm -rf build dist
pyinstaller --name=HydraTorrent --windowed --onefile --icon=image8.ico \
  --add-data="image8.ico;." --add-data="GeoLite2-Country.mmdb;." \
  --hidden-import=ttkbootstrap --hidden-import=libtorrent \
  --hidden-import=maxminddb --hidden-import=PIL \
  --collect-all=ttkbootstrap peer.pyw

cp dist/HydraTorrent.exe installer_package/
```

### Why Local Downloads Then Move?
libtorrent has issues with SMB/network shares:
- Unreliable file locking
- Poor performance
- Tracker timeout issues

Downloading to local disk first, then moving when complete is the workaround.

### Resource Path Helper (PyInstaller)
```python
def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        base_path = sys._MEIPASS  # PyInstaller extracts here
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# Usage:
logo_img = Image.open(resource_path("image8.ico"))
geo_reader = maxminddb.open_database(resource_path('GeoLite2-Country.mmdb'))
```

### Detecting PyInstaller Environment
```python
if getattr(sys, 'frozen', False):
    # Running as .exe
    app_data = os.path.join(os.environ['LOCALAPPDATA'], 'HydraTorrent')
else:
    # Running as script
    script_dir = os.path.dirname(os.path.abspath(__file__))
```

## Next Steps

### IMMEDIATE: Test Installer on Girlfriend's Computer
1. Copy `installer_package` folder to her computer
2. Run `ENABLE_JACKETT_SHARING.bat` on main computer first
3. Run `CLEANUP_OLD_INSTALL.bat` (if needed)
4. Run `INSTALL.bat` as Administrator
5. Enter Jackett API key and TrueNAS credentials
6. Run `TEST_PLEX_CONNECTION.bat` to verify setup
7. Test downloading a torrent
8. Verify it moves to Plex and Plex auto-scans

### TODO: Future Improvements
- Fix desktop shortcut creation (currently requires manual creation sometimes)
- Add logging viewer in GUI for troubleshooting
- Add network connectivity tests in GUI
- Create uninstaller script
- Add update mechanism for installed .exe
- Consider signed executable to avoid Windows Defender warnings

### FUTURE: Migrate to R710 Server (Long-term)
See original migration plan in "Next Steps" section of previous version.

## Git Status
- Clean working tree (all changes committed)
- Ready for deployment
- installer_package/ folder excluded from git (.gitignore)

## Questions to Ask When Returning
1. "Did the installer work on girlfriend's computer?"
2. "Any issues with Jackett search or Plex auto-scan?"
3. "Are downloads and auto-organization working correctly?"
4. "Any Windows Firewall warnings or issues?"
5. "Does the desktop shortcut work properly?"

---
Last updated: 2026-02-14 Evening
