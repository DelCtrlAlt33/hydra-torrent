@echo off
REM Test Plex Connection and Auto-Scan
REM Run this to verify Plex auto-scan will work

echo ====================================================================
echo PLEX CONNECTION TEST
echo ====================================================================
echo.

set PLEX_URL=http://192.168.20.33:32400
set PLEX_TOKEN=8Jkzcq8frQYqxELDQV3K

echo Testing Plex connection...
echo Server: %PLEX_URL%
echo.

REM Test basic connectivity
echo [1/3] Testing if Plex server is reachable...
curl -s --connect-timeout 5 "%PLEX_URL%/identity" >nul 2>&1
if %errorLevel% equ 0 (
    echo OK - Plex server is reachable
) else (
    echo ERROR - Cannot connect to Plex server
    echo.
    echo Troubleshooting:
    echo - Is Plex running on 192.168.20.33?
    echo - Can you ping 192.168.20.33?
    echo - Is there a firewall blocking port 32400?
    echo.
    pause
    exit /b 1
)

REM Test with token
echo.
echo [2/3] Testing Plex API with token...
curl -s "%PLEX_URL%/library/sections?X-Plex-Token=%PLEX_TOKEN%" >nul 2>&1
if %errorLevel% equ 0 (
    echo OK - Plex token is valid
) else (
    echo ERROR - Plex token may be invalid
    echo.
    echo The token in the config might be wrong or expired.
    echo Check: %PLEX_URL%/web/index.html
    echo.
    pause
    exit /b 1
)

REM Test library scan trigger
echo.
echo [3/3] Testing library scan trigger...
curl -s "%PLEX_URL%/library/sections/all/refresh?X-Plex-Token=%PLEX_TOKEN%" >nul 2>&1
if %errorLevel% equ 0 (
    echo OK - Successfully triggered Plex library scan!
) else (
    echo WARNING - Scan trigger may have failed
)

echo.
echo ====================================================================
echo TEST COMPLETE
echo ====================================================================
echo.
echo Plex auto-scan should work from this computer.
echo.
echo When Hydra Torrent downloads complete:
echo 1. Files move to \\192.168.20.4\Plex\movies or \tv
echo 2. Plex gets notified to scan for new media
echo 3. New content appears in Plex within a few minutes
echo.
echo Check Hydra Torrent logs if you still have issues.
echo.
pause
