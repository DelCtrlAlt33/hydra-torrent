@echo off
REM Enable Jackett Network Access
REM Run this on YOUR computer (192.168.20.2) to let her access Jackett

echo ====================================================================
echo ENABLE JACKETT NETWORK SHARING
echo ====================================================================
echo.
echo This will configure Jackett to accept connections from other computers
echo on your network, so your girlfriend can use it.
echo.

REM Check for admin rights
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: This script needs Administrator privileges.
    echo Please right-click and select "Run as Administrator"
    echo.
    pause
    exit /b 1
)

echo [1/3] Adding Windows Firewall rule for Jackett...
netsh advfirewall firewall delete rule name="Jackett" >nul 2>&1
netsh advfirewall firewall add rule name="Jackett" dir=in action=allow protocol=TCP localport=9117 profile=any description="Jackett torrent indexer"
echo OK - Port 9117 opened in firewall

echo.
echo [2/3] Configuring Jackett to allow external access...

REM Find Jackett config location
set CONFIG_PATHS[0]=%ProgramData%\Jackett\ServerConfig.json
set CONFIG_PATHS[1]=%APPDATA%\Jackett\ServerConfig.json
set CONFIG_PATHS[2]=%LOCALAPPDATA%\Jackett\ServerConfig.json

set FOUND_CONFIG=
for /L %%i in (0,1,2) do (
    if exist "!CONFIG_PATHS[%%i]!" (
        set FOUND_CONFIG=!CONFIG_PATHS[%%i]!
        goto :found
    )
)

:found
if defined FOUND_CONFIG (
    echo Found config at: %FOUND_CONFIG%

    REM Backup config
    copy "%FOUND_CONFIG%" "%FOUND_CONFIG%.backup" >nul 2>&1

    REM Update AllowExternal to true
    powershell -Command "(Get-Content '%FOUND_CONFIG%') -replace '\"AllowExternal\": false', '\"AllowExternal\": true' | Set-Content '%FOUND_CONFIG%'"

    echo OK - Jackett configured for network access
) else (
    echo WARNING: Could not find Jackett config file.
    echo You may need to manually enable "Allow external access" in Jackett settings.
)

echo.
echo [3/3] Restarting Jackett service...
sc query Jackett >nul 2>&1
if %errorLevel% equ 0 (
    net stop Jackett
    timeout /t 2 /nobreak >nul
    net start Jackett
    echo OK - Jackett service restarted
) else (
    echo WARNING: Jackett is not running as a service.
    echo Please restart Jackett manually for changes to take effect.
)

echo.
echo ====================================================================
echo CONFIGURATION COMPLETE!
echo ====================================================================
echo.
echo Jackett is now accessible from other computers on your network.
echo.
echo Test from your girlfriend's computer:
echo   Open browser to: http://192.168.20.2:9117
echo.
echo If it doesn't work:
echo 1. Make sure Jackett is running
echo 2. Try restarting Jackett manually
echo 3. Check Jackett web UI - Settings - "Allow external access" should be checked
echo.
pause
