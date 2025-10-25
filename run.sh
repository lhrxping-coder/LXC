#!/bin/bash
set -e

WORKDIR="$HOME/lxc_vps_bot"
SERVICE_NAME="lxc-vps-bot.service"
PYTHON_BIN="/usr/bin/python3"

echo "=== Full Real: LXD + Discord VPS Bot installer for Debian ==="

echo "[1/8] Updating apt..."
sudo apt update -y
sudo apt upgrade -y

echo "[2/8] Installing prerequisites..."
sudo apt install -y snapd python3 python3-venv python3-pip git curl jq

echo "[3/8] Ensuring snapd is running..."
sudo systemctl enable --now snapd.socket || true
sleep 2

if ! command -v lxd >/dev/null 2>&1; then
  echo "[4/8] Installing LXD via snap..."
  sudo snap install lxd
  # add current user to lxd group
  sudo usermod -aG lxd $USER || true
  echo "[4.1] Running 'newgrp lxd' may be needed or logout/login."
fi

echo "[5/8] Run 'lxd init --auto' (non-interactive default)."
sudo lxd init --auto || true

echo "[6/8] Create working directory: $WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

echo "[7/8] Create Python virtualenv and install requirements..."
if [ ! -d "venv" ]; then
  python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip
if [ ! -f requirements.txt ]; then
  cat > requirements.txt <<'REQ'
discord.py==2.4.1
aiosqlite
REQ
fi
pip install -r requirements.txt

echo "[8/8] Create config & plans defaults if missing..."
if [ ! -f config.json ]; then
  cat > config.json <<'JSON'
{
  "BOT_TOKEN": "YOUR_BOT_TOKEN_HERE",
  "ADMIN_ROLE_ID": "YOUR_ADMIN_ROLE_ID_HERE",
  "LXC_PATH": "/usr/bin/lxc",
  "FAKE_MODE_IF_NO_LXC": false
}
JSON
  echo "Created template config.json in $WORKDIR — please edit BOT_TOKEN and ADMIN_ROLE_ID."
fi

if [ ! -f plans.json ]; then
  cat > plans.json <<'JSON'
{
  "basic": { "name": "Basic", "ram_mb": 512, "cpu": 1, "disk_gb": 10, "price": 1 },
  "small": { "name": "Small", "ram_mb": 1024, "cpu": 1, "disk_gb": 20, "price": 2 },
  "medium": { "name": "Medium", "ram_mb": 2048, "cpu": 2, "disk_gb": 40, "price": 4 },
  "large": { "name": "Large", "ram_mb": 4096, "cpu": 4, "disk_gb": 80, "price": 8 }
}
JSON
  echo "Created default plans.json"
fi

# Create systemd service
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"
if [ ! -f "$SERVICE_PATH" ]; then
  sudo tee "$SERVICE_PATH" > /dev/null <<EOF
[Unit]
Description=LXC VPS Discord Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$WORKDIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$WORKDIR/venv/bin/python3 $WORKDIR/lxc_vps_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable --now "$SERVICE_NAME"
  echo "Created and started systemd service: $SERVICE_NAME"
else
  echo "Systemd service already exists: $SERVICE_PATH"
fi

echo "=== Setup complete ==="
echo "Edit $WORKDIR/config.json to set BOT_TOKEN and ADMIN_ROLE_ID if you haven't already."
echo "To view logs: sudo journalctl -u $SERVICE_NAME -f"
