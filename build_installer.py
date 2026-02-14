#!/usr/bin/env python3
"""
Build Hydra Torrent installer for Windows
Creates a standalone .exe with PyInstaller and installer script
"""
import os
import shutil
import subprocess
import sys

print("=" * 70)
print("HYDRA TORRENT INSTALLER BUILDER")
print("=" * 70)

# Check if PyInstaller is installed
try:
    import PyInstaller
except ImportError:
    print("\nPyInstaller not found. Installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
    print("PyInstaller installed!")

# Clean previous builds
print("\n[1/4] Cleaning previous builds...")
if os.path.exists("build"):
    shutil.rmtree("build")
if os.path.exists("dist"):
    shutil.rmtree("dist")
if os.path.exists("installer_package"):
    shutil.rmtree("installer_package")

# Build the .exe
print("\n[2/4] Building Hydra Torrent executable...")
print("This may take a few minutes...\n")

pyinstaller_cmd = [
    "pyinstaller",
    "--name=HydraTorrent",
    "--windowed",  # No console window
    "--onefile",   # Single .exe
    "--icon=image8.ico",
    "--add-data=image8.ico;.",
    "--add-data=GeoLite2-Country.mmdb;.",
    "--add-data=theme_manager.py;.",
    "--add-data=transfer_manager.py;.",
    "--add-data=media_organizer.py;.",
    "--add-data=config.py;.",
    "--add-data=certs.py;.",
    "--add-data=search.py;.",
    "--add-data=download.py;.",
    "--hidden-import=ttkbootstrap",
    "--hidden-import=libtorrent",
    "--hidden-import=maxminddb",
    "--hidden-import=PIL",
    "--collect-all=ttkbootstrap",
    "peer.pyw"
]

result = subprocess.run(pyinstaller_cmd)
if result.returncode != 0:
    print("\nERROR: PyInstaller build failed!")
    sys.exit(1)

print("\n✓ Executable built successfully!")

# Create installer package
print("\n[3/4] Creating installer package...")
os.makedirs("installer_package", exist_ok=True)

# Copy the .exe
shutil.copy("dist/HydraTorrent.exe", "installer_package/HydraTorrent.exe")

# Copy required files
files_to_copy = ["image8.ico", "GeoLite2-Country.mmdb"]
for f in files_to_copy:
    if os.path.exists(f):
        shutil.copy(f, f"installer_package/{f}")

print("✓ Files copied to installer_package/")

# Create the installer script
print("\n[4/4] Creating installer script...")

installer_script = """@echo off
REM Hydra Torrent Installer
REM Installs to %LOCALAPPDATA%\\HydraTorrent

echo ====================================================================
echo HYDRA TORRENT INSTALLER
echo ====================================================================
echo.

REM Check for admin rights
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: This installer needs Administrator privileges for firewall rules.
    echo Please right-click and select "Run as Administrator"
    echo.
    pause
    exit /b 1
)

echo [1/5] Creating installation directory...
set INSTALL_DIR=%LOCALAPPDATA%\\HydraTorrent
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

echo [2/5] Copying files...
copy /Y HydraTorrent.exe "%INSTALL_DIR%\\HydraTorrent.exe"
copy /Y image8.ico "%INSTALL_DIR%\\image8.ico"
copy /Y GeoLite2-Country.mmdb "%INSTALL_DIR%\\GeoLite2-Country.mmdb"

echo [3/5] Creating configuration...
(
echo {
echo   "peer_port": 6002,
echo   "jackett_url": "http://192.168.20.2:9117",
echo   "plex_url": "http://192.168.20.33:32400",
echo   "plex_token": "8Jkzcq8frQYqxELDQV3K",
echo   "auto_move_to_plex": true,
echo   "search_mode": "jackett"
echo }
) > "%INSTALL_DIR%\\hydra_config.json"

echo [4/5] Adding Windows Firewall rules...
netsh advfirewall firewall delete rule name="Hydra Torrent" >nul 2>&1
netsh advfirewall firewall add rule name="Hydra Torrent" dir=in action=allow protocol=TCP localport=6002 profile=any description="Hydra Torrent BitTorrent client"
netsh advfirewall firewall add rule name="Hydra Torrent" dir=in action=allow protocol=UDP localport=6002 profile=any description="Hydra Torrent BitTorrent client"
netsh advfirewall firewall add rule name="Hydra Torrent DHT" dir=in action=allow protocol=UDP localport=6881 profile=any description="Hydra Torrent DHT"

echo [5/5] Creating desktop shortcut...
powershell -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%USERPROFILE%\\Desktop\\Hydra Torrent.lnk'); $Shortcut.TargetPath = '%INSTALL_DIR%\\HydraTorrent.exe'; $Shortcut.WorkingDirectory = '%INSTALL_DIR%'; $Shortcut.IconLocation = '%INSTALL_DIR%\\image8.ico'; $Shortcut.Save()"

echo.
echo ====================================================================
echo INSTALLATION COMPLETE!
echo ====================================================================
echo.
echo Hydra Torrent has been installed to:
echo %INSTALL_DIR%
echo.
echo A desktop shortcut has been created.
echo Firewall rules have been added for port 6002.
echo.
echo Configuration:
echo - BitTorrent Port: 6002
echo - Jackett: http://192.168.20.2:9117
echo - Plex: http://192.168.20.33:32400
echo - Downloads: \\\\192.168.20.4\\Plex\\movies and tv
echo.
echo You can now launch Hydra Torrent from your desktop!
echo.
pause
"""

with open("installer_package/INSTALL.bat", "w") as f:
    f.write(installer_script)

# Create README
readme = """# Hydra Torrent Installer

## Installation Instructions

1. Right-click on INSTALL.bat
2. Select "Run as Administrator" (required for firewall rules)
3. Follow the prompts

The installer will:
- Install Hydra Torrent to %LOCALAPPDATA%\\HydraTorrent
- Create Windows Firewall rules for port 6002
- Configure to use existing Jackett and Plex servers
- Create a desktop shortcut

## Configuration

The installer automatically configures:
- Port 6002 (so it doesn't conflict with the main instance on 6001)
- Jackett at http://192.168.20.2:9117
- Plex at http://192.168.20.33:32400
- Downloads to \\\\192.168.20.4\\Plex\\movies and tv

## Uninstall

To uninstall:
1. Delete the folder: %LOCALAPPDATA%\\HydraTorrent
2. Delete the desktop shortcut
3. Remove firewall rules (optional):
   - Run: netsh advfirewall firewall delete rule name="Hydra Torrent"

## Troubleshooting

**"Access Denied" when installing:**
- Make sure you right-click INSTALL.bat and choose "Run as Administrator"

**Can't download torrents:**
- Check Windows Firewall hasn't blocked the program
- Verify port 6002 is open

**Can't connect to shares:**
- Make sure you can access \\\\192.168.20.4\\Plex from this computer
- Check network permissions
"""

with open("installer_package/README.txt", "w") as f:
    f.write(readme)

print("✓ Installer script created!")

print("\n" + "=" * 70)
print("BUILD COMPLETE!")
print("=" * 70)
print(f"\nInstaller package created in: installer_package/")
print("\nTo install on another computer:")
print("1. Copy the entire 'installer_package' folder")
print("2. Right-click INSTALL.bat and 'Run as Administrator'")
print("3. Done!")
print("\n" + "=" * 70)
