@echo off
REM Clean up old Hydra Torrent installation
REM Run this before installing the new version

echo ====================================================================
echo HYDRA TORRENT CLEANUP
echo ====================================================================
echo.
echo This will remove the old Hydra Torrent installation and clean up
echo any files that were incorrectly placed on the desktop.
echo.
echo Press Ctrl+C to cancel, or
pause

echo.
echo [1/5] Removing old installation directory...
if exist "%LOCALAPPDATA%\HydraTorrent\" (
    rd /s /q "%LOCALAPPDATA%\HydraTorrent"
    echo OK - Installation directory removed
) else (
    echo SKIP - No installation found
)

echo.
echo [2/5] Removing desktop shortcut...
if exist "%USERPROFILE%\Desktop\Hydra Torrent.lnk" (
    del /q "%USERPROFILE%\Desktop\Hydra Torrent.lnk"
    echo OK - Desktop shortcut removed
) else (
    echo SKIP - No shortcut found
)

echo.
echo [3/5] Cleaning up stray files on desktop...
set CLEANUP_COUNT=0

if exist "%USERPROFILE%\Desktop\transfers.json" (
    del /q "%USERPROFILE%\Desktop\transfers.json"
    set /a CLEANUP_COUNT+=1
    echo OK - Removed transfers.json
)

if exist "%USERPROFILE%\Desktop\hydra_config.json" (
    del /q "%USERPROFILE%\Desktop\hydra_config.json"
    set /a CLEANUP_COUNT+=1
    echo OK - Removed hydra_config.json
)

if exist "%USERPROFILE%\Desktop\downloads_incomplete\" (
    rd /s /q "%USERPROFILE%\Desktop\downloads_incomplete"
    set /a CLEANUP_COUNT+=1
    echo OK - Removed downloads_incomplete folder
)

if exist "%USERPROFILE%\Desktop\downloads_complete\" (
    rd /s /q "%USERPROFILE%\Desktop\downloads_complete"
    set /a CLEANUP_COUNT+=1
    echo OK - Removed downloads_complete folder
)

if exist "%USERPROFILE%\Desktop\shared_files\" (
    rd /s /q "%USERPROFILE%\Desktop\shared_files"
    set /a CLEANUP_COUNT+=1
    echo OK - Removed shared_files folder
)

if exist "%USERPROFILE%\Desktop\flags\" (
    rd /s /q "%USERPROFILE%\Desktop\flags"
    set /a CLEANUP_COUNT+=1
    echo OK - Removed flags folder
)

if %CLEANUP_COUNT%==0 (
    echo SKIP - No stray files found
) else (
    echo Cleaned up %CLEANUP_COUNT% items
)

echo.
echo [4/5] Checking for Hydra Torrent processes...
tasklist /FI "IMAGENAME eq HydraTorrent.exe" 2>NUL | find /I /N "HydraTorrent.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo WARNING: HydraTorrent.exe is still running
    echo Please close Hydra Torrent and run this script again
    pause
    exit /b 1
) else (
    echo OK - No running processes
)

echo.
echo [5/5] Network drives and firewall rules...
echo.
echo NOTE: We're keeping these because you'll need them for the new install:
echo - Network drive mapping to \\192.168.20.4\Plex
echo - Windows Firewall rules for port 6002
echo.
echo If you want to remove these too, run:
echo   net use \\192.168.20.4\Plex /delete
echo   netsh advfirewall firewall delete rule name="Hydra Torrent"

echo.
echo ====================================================================
echo CLEANUP COMPLETE!
echo ====================================================================
echo.
echo The old installation has been removed.
echo You can now run INSTALL.bat to install the new version.
echo.
pause
