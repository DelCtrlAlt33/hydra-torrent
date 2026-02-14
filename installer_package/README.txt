====================================================================
HYDRA TORRENT INSTALLER
====================================================================

Installation Instructions
--------------------------

1. Right-click on INSTALL.bat
2. Select "Run as Administrator" (required for firewall rules)
3. Enter your Jackett API key when prompted
4. Enter your TrueNAS credentials when prompted
5. Done!


What You'll Need
----------------

Before installing, have these ready:

1. Jackett API Key
   - Open http://192.168.20.2:9117 in your browser
   - Look at the top right corner for the API key
   - Copy it (looks like: abc123def456...)

2. TrueNAS Credentials
   - Username (probably "mediauser")
   - Password for accessing \\192.168.20.4\Plex


Configuration
-------------

The installer will automatically:
- Set port 6002 (won't conflict with main instance on 6001)
- Connect to Jackett at http://192.168.20.2:9117
- Connect to Plex at http://192.168.20.33:32400
- Map network drives to \\192.168.20.4\Plex\movies and tv
- Add Windows Firewall rules
- Create desktop shortcut


After Installation
------------------

All your files will be in:
%LOCALAPPDATA%\HydraTorrent\

This includes:
- HydraTorrent.exe
- hydra_config.json (your settings)
- transfers.json (your downloads)
- downloads_incomplete/ (active downloads)
- downloads_complete/ (finished downloads before moving to Plex)


Manual Configuration
--------------------

If you need to edit settings manually:

1. Close Hydra Torrent
2. Open: %LOCALAPPDATA%\HydraTorrent\hydra_config.json
3. Edit the JSON (be careful with commas!)
4. Restart Hydra Torrent

Example config with Jackett API key:
{
  "peer_port": 6002,
  "jackett_url": "http://192.168.20.2:9117",
  "jackett_api_key": "your_api_key_here",
  "plex_url": "http://192.168.20.33:32400",
  "plex_token": "8Jkzcq8frQYqxELDQV3K",
  "auto_move_to_plex": true,
  "search_mode": "jackett"
}

NOTE: Make sure there's a comma after every line EXCEPT the last one!


Troubleshooting
---------------

Shortcut doesn't work:
  - Right-click desktop -> New -> Shortcut
  - Browse to: %LOCALAPPDATA%\HydraTorrent\HydraTorrent.exe
  - Click Next, name it "Hydra Torrent", Finish
  - Right-click shortcut -> Properties -> Change Icon
  - Browse to: %LOCALAPPDATA%\HydraTorrent\image8.ico

Jackett search not working:
  - Make sure you entered the API key during install
  - Or add it manually to hydra_config.json (see above)
  - Restart Hydra Torrent after changing config

Can't download to network share:
  - Open File Explorer
  - Type: \\192.168.20.4\Plex
  - Enter your TrueNAS username and password
  - Check "Remember my credentials"
  - Try again

Plex not auto-scanning:
  - Check that auto_move_to_plex is true in config
  - Make sure Plex server is accessible from this computer
  - Verify plex_token is correct

Files appearing on desktop:
  - This shouldn't happen with the new version
  - If it does, delete those files and they'll be in the right place


Uninstall
---------

To uninstall:
1. Delete desktop shortcut
2. Delete folder: %LOCALAPPDATA%\HydraTorrent
3. Unmap network drive: net use \\192.168.20.4\Plex /delete
4. Remove firewall rules:
   netsh advfirewall firewall delete rule name="Hydra Torrent"


====================================================================
Built with love. No ads. No tracking. No bullshit.
====================================================================
