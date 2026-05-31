#!/bin/bash
# Run this once on a fresh Oracle Cloud Ubuntu 22.04 ARM VM.
# Usage: bash setup.sh
set -e

APP_DIR="/home/ubuntu/jobdrop"
PYTHON="python3"

echo "=== 1. System packages ==="
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip nginx git curl unzip

echo "=== 2. Clone repo ==="
# If already cloned, just pull
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull
else
    git clone --branch postgres-migration https://github.com/ruiqi-rachel-wang/JobRadar.git "$APP_DIR"
fi
cd "$APP_DIR"

echo "=== 3. Python venv ==="
$PYTHON -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=== 4. Playwright Chromium (ARM64) ==="
playwright install chromium
playwright install-deps chromium

echo "=== 5. Data directory ==="
mkdir -p "$APP_DIR/app/data"
# If you have an existing jobs.db, scp it here:
#   scp -i your_key.pem jobs.db ubuntu@YOUR_VM_IP:$APP_DIR/app/data/

echo "=== 6. Environment file ==="
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    # Generate a random SECRET_KEY
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/replace-with-a-long-random-string/$SECRET/" "$APP_DIR/.env"
    echo ""
    echo ">>> EDIT $APP_DIR/.env and set ADMIN_PASSWORD before continuing <<<"
    echo ""
fi

echo "=== 7. systemd services ==="
sudo cp "$APP_DIR/deploy/jobdrop.service"         /etc/systemd/system/
sudo cp "$APP_DIR/deploy/jobdrop-scanner.service" /etc/systemd/system/
sudo cp "$APP_DIR/deploy/jobdrop-scanner.timer"   /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable  jobdrop
sudo systemctl start   jobdrop

sudo systemctl enable  jobdrop-scanner.timer
sudo systemctl start   jobdrop-scanner.timer

echo "=== 8. nginx ==="
sudo cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/jobdrop
sudo ln -sf /etc/nginx/sites-available/jobdrop /etc/nginx/sites-enabled/jobdrop
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx

echo ""
echo "=== Done ==="
echo "Web app:    http://$(curl -s ifconfig.me)"
echo "App status: sudo systemctl status jobdrop"
echo "App logs:   sudo journalctl -u jobdrop -f"
echo "Scanner:    sudo systemctl status jobdrop-scanner.timer"
