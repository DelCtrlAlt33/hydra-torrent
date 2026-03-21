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
    # Data files
    "--add-data=image8.ico;.",
    "--add-data=GeoLite2-Country.mmdb;.",
    "--add-data=static;static",
    # Local modules (daemon + dependencies)
    "--add-data=hydra_daemon.py;.",
    "--add-data=hydra_tray.py;.",
    "--add-data=webview_api.py;.",
    "--add-data=config.py;.",
    "--add-data=certs.py;.",
    "--add-data=search.py;.",
    "--add-data=download.py;.",
    "--add-data=vpn_guard.py;.",
    "--add-data=media_organizer.py;.",
    "--add-data=rss_poller.py;.",
    "--add-data=daemon_models.py;.",
    "--add-data=network.py;.",
    "--add-data=transfer_manager.py;.",
    "--add-data=theme_manager.py;.",
    # Hidden imports — pywebview + GUI
    "--hidden-import=webview",
    "--hidden-import=pystray",
    "--hidden-import=PIL",
    # Hidden imports — daemon
    "--hidden-import=uvicorn",
    "--hidden-import=uvicorn.logging",
    "--hidden-import=uvicorn.loops",
    "--hidden-import=uvicorn.loops.auto",
    "--hidden-import=uvicorn.protocols",
    "--hidden-import=uvicorn.protocols.http",
    "--hidden-import=uvicorn.protocols.http.auto",
    "--hidden-import=uvicorn.lifespan",
    "--hidden-import=uvicorn.lifespan.on",
    "--hidden-import=fastapi",
    "--hidden-import=starlette",
    "--hidden-import=pydantic",
    "--hidden-import=libtorrent",
    "--hidden-import=maxminddb",
    "--hidden-import=psutil",
    "--hidden-import=bs4",
    "--hidden-import=aiofiles",
    "--hidden-import=miniupnpc",
    "--hidden-import=cryptography",
    # Entry point
    "hydra_app.py"
]

result = subprocess.run(pyinstaller_cmd)
if result.returncode != 0:
    print("\nERROR: PyInstaller build failed!")
    sys.exit(1)

print("\n[OK] Executable built successfully!")

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

print("[OK] Files copied to installer_package/")

# Create the installer script
print("\n[4/4] Creating installer script...")

installer_script = r"""@echo off
REM Hydra Torrent Installer
REM Installs to %LOCALAPPDATA%\HydraTorrent

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

echo [1/6] Creating installation directory...
set INSTALL_DIR=%LOCALAPPDATA%\HydraTorrent
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

echo [2/6] Copying files...
copy /Y HydraTorrent.exe "%INSTALL_DIR%\HydraTorrent.exe"
copy /Y image8.ico "%INSTALL_DIR%\image8.ico"
copy /Y GeoLite2-Country.mmdb "%INSTALL_DIR%\GeoLite2-Country.mmdb"

echo [3/6] Creating configuration...
if not exist "%INSTALL_DIR%\hydra_config.json" (
    (
    echo {
    echo   "daemon_host": "127.0.0.1",
    echo   "daemon_port": 8766,
    echo   "daemon_use_ssl": false,
    echo   "desktop_mode": true,
    echo   "search_mode": "online"
    echo }
    ) > "%INSTALL_DIR%\hydra_config.json"
    echo    Configuration created with defaults.
) else (
    echo    Existing configuration preserved.
)

echo [4/6] Adding Windows Firewall rules...
netsh advfirewall firewall delete rule name="Hydra Torrent" >nul 2>&1
netsh advfirewall firewall add rule name="Hydra Torrent" dir=in action=allow protocol=TCP localport=6002 profile=any description="Hydra Torrent BitTorrent client"
netsh advfirewall firewall add rule name="Hydra Torrent" dir=in action=allow protocol=UDP localport=6002 profile=any description="Hydra Torrent BitTorrent client"
netsh advfirewall firewall add rule name="Hydra Torrent DHT" dir=in action=allow protocol=UDP localport=6881 profile=any description="Hydra Torrent DHT"

echo [5/6] Creating desktop shortcut...
powershell -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%USERPROFILE%\Desktop\Hydra Torrent.lnk'); $Shortcut.TargetPath = '%INSTALL_DIR%\HydraTorrent.exe'; $Shortcut.WorkingDirectory = '%INSTALL_DIR%'; $Shortcut.IconLocation = '%INSTALL_DIR%\image8.ico'; $Shortcut.Save()"

echo [6/6] Creating Start Menu shortcut...
set START_MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs
powershell -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%START_MENU%\Hydra Torrent.lnk'); $Shortcut.TargetPath = '%INSTALL_DIR%\HydraTorrent.exe'; $Shortcut.WorkingDirectory = '%INSTALL_DIR%'; $Shortcut.IconLocation = '%INSTALL_DIR%\image8.ico'; $Shortcut.Save()"

echo.
echo ====================================================================
echo INSTALLATION COMPLETE!
echo ====================================================================
echo.
echo Hydra Torrent has been installed to:
echo %INSTALL_DIR%
echo.
echo - Desktop shortcut created
echo - Start Menu shortcut created
echo - Firewall rules added
echo.
echo Click the Hydra Torrent icon on your desktop to get started!
echo.
pause
"""

with open("installer_package/INSTALL.bat", "w") as f:
    f.write(installer_script)

print("[OK] Installer script created!")

print("\n" + "=" * 70)
print("BUILD COMPLETE!")
print("=" * 70)
print(f"\nInstaller package created in: installer_package/")
print("\nContents:")
for f in os.listdir("installer_package"):
    size = os.path.getsize(os.path.join("installer_package", f))
    print(f"  {f} ({size:,} bytes)")
print("\nTo install on another computer:")
print("1. Copy the entire 'installer_package' folder")
print("2. Right-click INSTALL.bat and 'Run as Administrator'")
print("3. Click the desktop shortcut — Hydra Torrent opens!")
print("\n" + "=" * 70)
