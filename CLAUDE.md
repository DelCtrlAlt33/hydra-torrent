# Hydra Torrent Project

## Project Overview
Hydra Torrent is a custom BitTorrent client with a GUI built in Python using tkinter and libtorrent. It features:
- Clean, themed GUI with dark mode support
- Peer list with country flags and real-time stats
- Automatic media organization for Plex
- Two-stage download process (incomplete → complete → Plex)
- Magnet link support
- Resume/seeding functionality

## Current State (2026-02-14)

### Working Features
- ✅ Download torrents via magnet links
- ✅ Peer list with country flags (async loading to prevent UI freeze)
- ✅ Automatic move to Plex when complete (movies → M:\movies, TV → M:\tv)
- ✅ Resume torrents on restart without re-downloading
- ✅ Plex auto-scan via API when new media added
- ✅ Theme system (dark/light modes)
- ✅ File icons and progress tracking

### Architecture (Current - Temporary)
```
Desktop (192.168.20.2)
├── Hydra Torrent GUI (peer.pyw)
├── Downloads to: C:\Users\Matth\hydra_torrent\downloads_incomplete (LOCAL)
└── Auto-moves to: \\192.168.20.4\Plex\movies or \tv (TrueNAS SMB)

TrueNAS (192.168.20.4)
├── \\192.168.20.4\Plex\movies (M:\movies)
└── \\192.168.20.4\Plex\tv (M:\tv)

R710 Proxmox (192.168.20.33)
└── Plex Container (LXC 100)
    └── Mounts TrueNAS via SMB at /mnt/smb-media
```

### Known Issues & Technical Debt

#### CRITICAL: Architectural Design Smell
**Problem**: Hydra Torrent runs on the desktop (workstation), not a server.
- Desktop should be a CLIENT, not a SERVER
- Can't reboot desktop freely without stopping downloads
- Uses local disk space unnecessarily
- Two-copy operation (local → TrueNAS) wastes I/O

**Why**: libtorrent doesn't work reliably when downloading directly to SMB shares (\\192.168.20.4\Plex\incomplete). We discovered this causes torrent failures.

**Solution**: Migrate Hydra to R710 server (see "Next Steps" below)

#### Other Issues
- Some Plex movies may still need manual "Fix Match" after library cleanup
- Flag downloads require internet connection (cached after first load)

## Recent Work Completed

### Session 2026-02-14: Fixes and Plex Cleanup

1. **Fixed Download Failures**
   - Problem: Torrents stopped working after changing `listen_interfaces` to specific IP
   - Solution: Reverted to `listen_interfaces: '0.0.0.0:6001'`
   - Problem: Downloads to SMB share unreliable
   - Solution: Download to local disk first, then auto-move

2. **Performance Optimization**
   - Removed debug alert monitoring thread (CPU overhead)
   - Changed logging from DEBUG to INFO
   - Made flag downloads async (prevented UI freezing on click)

3. **Fixed Resume Logic**
   - Problem: Completed torrents re-downloaded on restart
   - Solution: Store `plex_path` in transfer data, use it for resume instead of incomplete dir

4. **Plex Library Cleanup**
   - Created `plex_smart_cleanup.py` - comprehensive movie cleanup
     - Fixed 116 movies with proper naming: "Movie Name (YEAR)"
     - Removed quality tags (1080p, BluRay, x264, etc.)
     - Merged 2 duplicates
   - Created `plex_tv_cleanup.py` - TV show organization
     - Merged 5 scattered season folders (Breaking Bad, Smiling Friends)
     - Organized 13 loose episode files into proper structure
   - Created `plex_quick_fixes.py` - targeted fixes for 22 specific issues
     - Twilight series (5 movies)
     - Scary Movie series (3 movies)
     - Spider-Man movies
     - Matrix trilogy
   - Created `split_trilogies.py` - split trilogy packs
     - The Grudge trilogy → 3 separate movies
     - Austin Powers trilogy → 3 separate movies

5. **Plex Auto-Scan**
   - Configured Plex API token in `hydra_config.json`
   - Auto-triggers library scan when media added

## File Structure

### Core Application
- `peer.pyw` - Main GUI application (tkinter)
- `config.py` - Configuration (paths, logging, network settings)
- `media_organizer.py` - Automatic categorization (movies vs TV)
- `theme_manager.py` - Dark/light theme system

### Plex Utilities (One-time cleanup scripts)
- `plex_smart_cleanup.py` - Comprehensive movie cleanup
- `plex_tv_cleanup.py` - TV show organization
- `plex_quick_fixes.py` - Targeted manual fixes
- `split_trilogies.py` - Split trilogy packs

### Configuration Files
- `hydra_config.json` - Plex URL and API token
- `transfers.json` - Active/completed transfers state

### Data Directories
- `downloads_incomplete/` - Active downloads (local disk)
- `downloads_complete/` - Completed before Plex move (local disk)
- `flags/` - Cached country flag images

## Important Configuration

### config.py Settings
```python
# Download directories (local - fast and reliable for libtorrent)
DOWNLOAD_DIR_INCOMPLETE = os.path.join(BASE_DIR, 'downloads_incomplete')
DOWNLOAD_DIR_COMPLETE = os.path.join(BASE_DIR, 'downloads_complete')

# Plex media directories - map to TrueNAS Plex library via SMB
MEDIA_DIR_MOVIES = r'\\192.168.20.4\Plex\movies'
MEDIA_DIR_TV = r'\\192.168.20.4\Plex\tv'

# Network settings
PEER_PORT = 6001  # BitTorrent port

# Logging
logging.basicConfig(level=logging.INFO)  # NOT DEBUG (performance)
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

### Plex Configuration
```json
{
  "plex_url": "http://192.168.20.33:32400",
  "plex_token": "8Jkzcq8frQYqxELDQV3K"
}
```

### TrueNAS Permissions
```bash
# On TrueNAS
chown -R mediauser:mediauser /mnt/MainPool/media
chmod -R 775 /mnt/MainPool/media
```

## Next Steps

### PRIORITY: Migrate to Proper Server Architecture

**Why**: Running persistent services on desktop is a design anti-pattern. Desktop should be a client, not a server.

**Migration Plan**:

1. **Create Hydra LXC on R710** (Proxmox LXC 104)
   ```bash
   pct create 104 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
     --hostname hydra-torrent \
     --memory 2048 \
     --cores 2 \
     --net0 name=eth0,bridge=vmbr0,ip=192.168.20.104/24,gw=192.168.20.1 \
     --rootfs local-lvm:8
   ```

2. **Mount TrueNAS Storage**
   ```bash
   # In LXC
   mkdir -p /mnt/downloads /mnt/media
   mount -t nfs 192.168.20.4:/mnt/MainPool/downloads /mnt/downloads
   mount -t nfs 192.168.20.4:/mnt/MainPool/media /mnt/media
   # Add to /etc/fstab for persistence
   ```

3. **Install Dependencies**
   ```bash
   apt update && apt install python3 python3-pip python3-tk git
   pip3 install libtorrent requests pillow
   ```

4. **Copy Code and Update Config**
   - Copy entire hydra_torrent folder to `/opt/hydra_torrent`
   - Update paths in config.py to use `/mnt/downloads` and `/mnt/media`

5. **Create Systemd Service**
   - Run as persistent service
   - Auto-start on boot
   - Restart on failure

6. **Access from Desktop**
   - Desktop just opens browser to `http://192.168.20.104:8080`
   - Can shutdown desktop without affecting downloads

**Benefits**:
- No network copy overhead (downloads directly on server storage)
- Desktop free to reboot anytime
- Proper server/client separation
- R710 has 3TB storage, better suited for this workload

### Post-Migration Tasks
- Test resume functionality on server
- Verify Plex auto-scan still works
- Update documentation with new architecture
- Remove Hydra from desktop

## Troubleshooting

### Downloads Not Working
- Check `listen_interfaces` is `0.0.0.0:6001` (not a specific IP)
- Verify downloads are to LOCAL disk, not SMB share
- Check firewall: port 6001 open
- Check tracker response in logs

### Completed Torrents Re-download on Restart
- Verify `plex_path` is saved in transfers.json
- Check resume logic uses `plex_path` for save_path
- Look for "Resuming download..." in logs

### UI Freezing When Clicking Transfers
- Check flag downloads are async (threading.Thread)
- Verify flag_cache is being used
- May need to clear flag cache if corrupted

### Plex Not Showing New Media
- Check Plex API token is valid
- Verify auto-scan call succeeds (check logs)
- Manual scan: Plex → Movies → ... → "Scan Library Files"

### Permission Denied Moving to Plex
- Check TrueNAS ownership: `chown -R mediauser:mediauser /mnt/MainPool/media`
- Check permissions: `chmod -R 775 /mnt/MainPool/media`
- Verify SMB share allows write access

## Development Notes

### Why Local Downloads Then Move?
libtorrent has issues with SMB/network shares:
- Unreliable file locking
- Poor performance
- Tracker timeout issues

Downloading to local disk first, then moving when complete is the workaround until we migrate to R710 where downloads will be local to the server.

### Resume Logic
```python
# Store destination when moved to Plex
if success:
    t['moved_to_plex'] = True
    t['plex_path'] = os.path.dirname(dest_path)

# Resume with correct path
save_path = t.get('plex_path', DOWNLOAD_DIR_INCOMPLETE)
```

### Flag Loading Performance
Flags are downloaded async to prevent UI freeze:
```python
threading.Thread(target=download_flag, args=(flag_key,), daemon=True).start()
```

### Media Detection
```python
TV_PATTERNS = [
    r'[Ss]\d{1,2}[Ee]\d{1,2}',  # S01E05
    r'\d{1,2}x\d{1,2}',          # 1x05
    r'Season\s*\d+',
]
```

## Git Status
- Modified files: config.py, peer.pyw, theme_manager.py
- Untracked: Plex cleanup scripts, screenshots, production_peer.pyw

## Questions to Ask When Returning
1. "Did you migrate to R710 yet?" (see Next Steps)
2. "Any Plex movies still showing wrong posters?"
3. "Downloads working reliably?"
4. "Any new features needed?"

---
Last updated: 2026-02-14
