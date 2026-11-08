#!/bin/bash
set -e

# Hydra Torrent installer for Linux
# Run this from the hydra-torrent directory: sudo bash install.sh

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="/opt/hydra"

echo ""
echo -e "${GREEN}Hydra Torrent Installer${NC}"
echo "========================"
echo ""

# Must be root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Run this as root: sudo bash install.sh${NC}"
  exit 1
fi

# Installs to a fixed system location instead of running in place
# wherever it was cloned. Running in place broke for anyone who cloned
# into a directory the restricted "hydra" service user can't reach -
# most commonly /root itself, since that's 700 by default and blocks a
# non-root user from even traversing into it, no matter what
# permissions the cloned folder has (confirmed live: systemd's own
# error is just "Permission denied" with no hint that the real cause is
# the parent directory, not the one it's actually trying to enter).
# /opt is standard, world-traversable, and every other Linux service
# expects to live there.
if [ "$SOURCE_DIR" != "$INSTALL_DIR" ]; then
  echo -n "Installing to $INSTALL_DIR... "
  mkdir -p "$INSTALL_DIR"
  cp -a "$SOURCE_DIR"/. "$INSTALL_DIR"/
  echo -e "${GREEN}ok${NC}"
fi

# Check Python
echo -n "Checking Python... "
if command -v python3 &>/dev/null; then
  PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  echo -e "${GREEN}found Python $PY_VERSION${NC}"
else
  echo -e "${YELLOW}not found, installing...${NC}"
  apt update && apt install -y python3
  PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  echo -e "${GREEN}installed Python $PY_VERSION${NC}"
fi

# python3-venv is a separate apt package on Debian/Ubuntu and does NOT
# come with a normal python3 install - "python3 -m venv" fails outright
# without it ("ensurepip is not available"). Checked unconditionally,
# not just in the "python3 not found" branch above: a completely normal
# fresh Ubuntu already has python3 (so that branch never runs) but does
# NOT have this package, confirmed live on a fresh Ubuntu 24.04 install.
echo -n "Checking python3-venv... "
if python3 -c "import ensurepip" &>/dev/null; then
  echo -e "${GREEN}ok${NC}"
else
  echo -e "${YELLOW}installing...${NC}"
  apt update && apt install -y python3-venv
fi

# Dependencies go in a dedicated virtual environment, not the system
# Python. Modern Debian/Ubuntu (PEP 668) blocks pip from touching system
# Python at all without --break-system-packages - but that flag alone
# isn't enough here either: Ubuntu ships its own apt-packaged
# typing_extensions with no pip RECORD file, so pip can't safely
# upgrade it in place even with the flag ("Cannot uninstall
# typing_extensions 4.10.0, RECORD file not found: The package was
# installed by debian" - the exact, real error hit installing this on a
# genuinely fresh Ubuntu 24.04 VM, not a hypothetical). A venv sidesteps
# the whole class of conflict instead of working around this one
# specific package - also the fix pip's own error message recommends.
echo ""
echo -n "Creating virtual environment... "
python3 -m venv "$INSTALL_DIR/venv"
echo -e "${GREEN}ok${NC}"

echo ""
echo "Installing dependencies..."
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
echo -e "${GREEN}Dependencies installed${NC}"

# GeoIP database for peer/VPN country flags on the map (optional feature -
# install continues fine if this fails, just without that data)
echo ""
echo -n "Fetching GeoIP database... "
"$INSTALL_DIR/venv/bin/python3" "$INSTALL_DIR/fetch_geoip.py" && echo -e "${GREEN}ok${NC}" || echo -e "${YELLOW}skipped (network issue) - country flags/map marker will be disabled${NC}"

# Create hydra user if it doesn't exist
echo ""
echo -n "Setting up hydra user... "
if id "hydra" &>/dev/null; then
  echo -e "${GREEN}already exists${NC}"
else
  useradd -r -s /usr/sbin/nologin -d "$INSTALL_DIR" hydra
  echo -e "${GREEN}created${NC}"
fi

# Set ownership
chown -R hydra:hydra "$INSTALL_DIR"

# Generate systemd service
echo -n "Creating systemd service... "
cat > /etc/systemd/system/hydra-torrent.service <<EOF
[Unit]
Description=Hydra Torrent Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=hydra
Group=hydra
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/hydra_daemon.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hydra-torrent

[Install]
WantedBy=multi-user.target
EOF
echo -e "${GREEN}ok${NC}"

# Enable and start
echo -n "Starting Hydra Torrent... "
systemctl daemon-reload
systemctl enable hydra-torrent --quiet
# restart, not start: on an update the service is already running, and "start"
# would leave the old code in memory.
systemctl restart hydra-torrent
echo -e "${GREEN}ok${NC}"

# Wait a sec for it to boot
sleep 2

# Get the IP
IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Hydra Torrent is running!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "  Open in your browser:"
echo -e "  ${YELLOW}https://$IP:8765/ui${NC}"
echo ""
echo -e "  Accept the certificate warning - it's a"
echo -e "  self-signed cert generated on first run."
echo ""
echo -e "  Commands:"
echo -e "    sudo systemctl status hydra-torrent"
echo -e "    sudo systemctl restart hydra-torrent"
echo -e "    sudo journalctl -u hydra-torrent -f"
echo ""
