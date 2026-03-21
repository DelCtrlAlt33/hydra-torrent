#!/bin/bash
set -e

# Hydra Torrent installer for Linux
# Run this from the hydra-torrent directory: sudo bash install.sh

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo -e "${GREEN}Hydra Torrent Installer${NC}"
echo "========================"
echo ""

# Must be root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Run this as root: sudo bash install.sh${NC}"
  exit 1
fi

# Check Python
echo -n "Checking Python... "
if command -v python3 &>/dev/null; then
  PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  echo -e "${GREEN}found Python $PY_VERSION${NC}"
else
  echo -e "${YELLOW}not found, installing...${NC}"
  apt update && apt install -y python3 python3-pip python3-venv
  PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  echo -e "${GREEN}installed Python $PY_VERSION${NC}"
fi

# Check pip
echo -n "Checking pip... "
if python3 -m pip --version &>/dev/null; then
  echo -e "${GREEN}ok${NC}"
else
  echo -e "${YELLOW}installing...${NC}"
  apt install -y python3-pip
fi

# Install Python dependencies
echo ""
echo "Installing dependencies..."
python3 -m pip install -r "$INSTALL_DIR/requirements.txt" --break-system-packages 2>/dev/null \
  || python3 -m pip install -r "$INSTALL_DIR/requirements.txt"
echo -e "${GREEN}Dependencies installed${NC}"

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
ExecStart=/usr/bin/python3 $INSTALL_DIR/hydra_daemon.py
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
systemctl start hydra-torrent
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
echo -e "  Accept the certificate warning — it's a"
echo -e "  self-signed cert generated on first run."
echo ""
echo -e "  Commands:"
echo -e "    sudo systemctl status hydra-torrent"
echo -e "    sudo systemctl restart hydra-torrent"
echo -e "    sudo journalctl -u hydra-torrent -f"
echo ""
