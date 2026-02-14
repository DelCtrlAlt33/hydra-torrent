@echo off
REM Hydra Torrent Complete Installer
REM Installs to %LOCALAPPDATA%\HydraTorrent

echo ====================================================================
echo HYDRA TORRENT INSTALLER
echo ====================================================================
echo.

REM Check for admin rights
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: This installer needs Administrator privileges.
    echo Please right-click and select "Run as Administrator"
    echo.
    pause
    exit /b 1
)

echo [1/7] Creating installation directory...
set INSTALL_DIR=%LOCALAPPDATA%\HydraTorrent
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

echo [2/7] Copying files...
copy /Y HydraTorrent.exe "%INSTALL_DIR%\HydraTorrent.exe"
copy /Y image8.ico "%INSTALL_DIR%\image8.ico"
copy /Y GeoLite2-Country.mmdb "%INSTALL_DIR%\GeoLite2-Country.mmdb"

echo.
echo ====================================================================
echo CONFIGURATION SETUP
echo ====================================================================
echo.
echo Hydra needs a few details to connect to Jackett and TrueNAS.
echo.

REM Get Jackett API Key
echo [3/7] Jackett Configuration
echo.
echo Please enter your Jackett API key.
echo You can find it at: http://192.168.20.2:9117 (top right corner)
echo.
set /p JACKETT_API_KEY="Jackett API Key: "

if "%JACKETT_API_KEY%"=="" (
    echo.
    echo WARNING: No API key entered. Jackett search will not work.
    echo You can add it later in Settings.
    set JACKETT_API_KEY=
)

REM Get TrueNAS Credentials
echo.
echo [4/7] TrueNAS Share Configuration
echo.
echo Please enter your TrueNAS credentials to access the Plex media folders.
echo This is the username/password for \\192.168.20.4\Plex
echo.
set /p TRUENAS_USER="TrueNAS Username (e.g., mediauser): "
set /p TRUENAS_PASS="TrueNAS Password: "

if "%TRUENAS_USER%"=="" (
    echo.
    echo WARNING: No credentials entered. You won't be able to save downloads.
    echo You'll need to map the network drives manually later.
    set SKIP_MAPPING=1
)

echo.
echo [5/7] Creating configuration file...
(
echo {
echo   "peer_port": 6002,
echo   "jackett_url": "http://192.168.20.2:9117",
if not "%JACKETT_API_KEY%"=="" echo   "jackett_api_key": "%JACKETT_API_KEY%",
echo   "plex_url": "http://192.168.20.33:32400",
echo   "plex_token": "8Jkzcq8frQYqxELDQV3K",
echo   "auto_move_to_plex": true,
echo   "search_mode": "jackett"
echo }
) > "%INSTALL_DIR%\hydra_config.json"

echo OK - Configuration saved

if not "%SKIP_MAPPING%"=="1" (
    echo.
    echo [6/7] Mapping TrueNAS network shares...

    REM Remove existing mappings if any
    net use \\192.168.20.4\Plex /delete >nul 2>&1

    REM Map the Plex share with credentials
    net use \\192.168.20.4\Plex /user:%TRUENAS_USER% %TRUENAS_PASS% /persistent:yes >nul 2>&1

    if %errorLevel% equ 0 (
        echo OK - TrueNAS share mapped successfully
        echo Testing access to \\192.168.20.4\Plex\movies...

        if exist "\\192.168.20.4\Plex\movies\" (
            echo OK - Movies folder accessible
        ) else (
            echo WARNING: Movies folder not found. Check TrueNAS paths.
        )

        if exist "\\192.168.20.4\Plex\tv\" (
            echo OK - TV folder accessible
        ) else (
            echo WARNING: TV folder not found. Check TrueNAS paths.
        )
    ) else (
        echo ERROR: Failed to map network share.
        echo Please check your username and password.
        echo You can try mapping manually: net use \\192.168.20.4\Plex /user:%TRUENAS_USER%
        pause
    )
) else (
    echo [6/7] Skipping network share mapping (no credentials provided)
)

echo.
echo [7/7] Adding Windows Firewall rules...
netsh advfirewall firewall delete rule name="Hydra Torrent" >nul 2>&1
netsh advfirewall firewall add rule name="Hydra Torrent" dir=in action=allow protocol=TCP localport=6002 profile=any description="Hydra Torrent BitTorrent client"
netsh advfirewall firewall add rule name="Hydra Torrent" dir=in action=allow protocol=UDP localport=6002 profile=any description="Hydra Torrent BitTorrent client"
netsh advfirewall firewall add rule name="Hydra Torrent DHT" dir=in action=allow protocol=UDP localport=6881 profile=any description="Hydra Torrent DHT"
echo OK - Firewall rules added

echo.
echo [8/8] Creating desktop shortcut...
powershell -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%USERPROFILE%\Desktop\Hydra Torrent.lnk'); $Shortcut.TargetPath = '%INSTALL_DIR%\HydraTorrent.exe'; $Shortcut.WorkingDirectory = '%INSTALL_DIR%'; $Shortcut.IconLocation = '%INSTALL_DIR%\image8.ico'; $Shortcut.Save()"
echo OK - Desktop shortcut created

echo.
echo ====================================================================
echo INSTALLATION COMPLETE!
echo ====================================================================
echo.
echo Hydra Torrent has been installed to:
echo %INSTALL_DIR%
echo.
echo Configuration:
echo - BitTorrent Port: 6002
echo - Jackett: http://192.168.20.2:9117
if not "%JACKETT_API_KEY%"=="" echo - Jackett API Key: Configured
echo - Plex: http://192.168.20.33:32400
if not "%SKIP_MAPPING%"=="1" echo - TrueNAS Share: Mapped with credentials
echo.
echo You can now launch Hydra Torrent from your desktop!
echo.
echo Troubleshooting:
echo - If Jackett search doesn't work, check the API key in Settings
echo - If you can't save downloads, make sure \\192.168.20.4\Plex is accessible
echo - Test TrueNAS access: Try opening \\192.168.20.4\Plex\movies in File Explorer
echo.
pause
