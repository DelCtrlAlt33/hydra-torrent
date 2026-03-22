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
print("\n[1/3] Cleaning previous builds...")
if os.path.exists("build"):
    shutil.rmtree("build")
if os.path.exists("dist"):
    shutil.rmtree("dist")
if os.path.exists("installer_package"):
    shutil.rmtree("installer_package")

# Build the .exe
print("\n[2/3] Building Hydra Torrent executable...")
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

# Package the output
print("\n[3/3] Packaging...")
os.makedirs("installer_package", exist_ok=True)

shutil.copy("dist/HydraTorrent.exe", "installer_package/HydraTorrent.exe")
for f in ["image8.ico", "GeoLite2-Country.mmdb"]:
    if os.path.exists(f):
        shutil.copy(f, f"installer_package/{f}")

print("\n" + "=" * 70)
print("BUILD COMPLETE!")
print("=" * 70)
print(f"\nOutput: installer_package/")
print("\nContents:")
for f in os.listdir("installer_package"):
    size = os.path.getsize(os.path.join("installer_package", f))
    print(f"  {f} ({size:,} bytes)")
print("\nUsers just double-click HydraTorrent.exe — it self-installs on first run.")
print("(Creates desktop shortcut, Start Menu entry, config, and firewall rules)")
print("\n" + "=" * 70)
