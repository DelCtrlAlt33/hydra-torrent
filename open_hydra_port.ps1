New-NetFirewallRule -DisplayName "Hydra Daemon (LAN)" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private
Write-Host "Done. Port 8765 is now open on private networks."
