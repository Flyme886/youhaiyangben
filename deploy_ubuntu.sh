#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/harmful-sample-web"
SERVICE_FILE="/etc/systemd/system/harmful-sample-web.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run as root: sudo bash deploy_ubuntu.sh"
  exit 1
fi

cd "$APP_DIR"

apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates curl gnupg \
  fonts-wqy-zenhei fonts-wqy-microhei fonts-noto-cjk

if ! command -v node >/dev/null 2>&1 || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 18 ? 0 : 1)' >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

npm install
npx playwright install --with-deps chromium

mkdir -p data/tasks
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env. Edit APP_PASSWORD before exposing the service."
fi

chmod +x run_server.sh
cp harmful-sample-web.service "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable harmful-sample-web
systemctl restart harmful-sample-web
systemctl status harmful-sample-web --no-pager
