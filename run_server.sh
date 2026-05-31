#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/harmful-sample-web"
cd "$APP_DIR"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8787}"

exec "$APP_DIR/.venv/bin/python" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
