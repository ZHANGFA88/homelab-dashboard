#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${CONFIG_PATH:-/config/config.json}"
STATUS_INTERVAL="${STATUS_INTERVAL:-30}"
SURGE_INTERVAL="${SURGE_INTERVAL:-5}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8765}"
DASHBOARD_HOST="${DASHBOARD_HOST:-0.0.0.0}"

mkdir -p "$(dirname "$CONFIG_PATH")" /app/data

if [ ! -f "$CONFIG_PATH" ]; then
  echo "[entrypoint] config not found, creating from /app/config.example.json -> $CONFIG_PATH"
  cp /app/config.example.json "$CONFIG_PATH"
fi

export CONFIG_PATH STATUS_INTERVAL SURGE_INTERVAL DASHBOARD_PORT DASHBOARD_HOST

# Initial collection. Never fail container startup because external services are unavailable.
python3 /app/scripts/collector.py >/tmp/health-dashboard-status-collector.last.log 2>&1 || true
python3 /app/scripts/surge_collector.py >/tmp/health-dashboard-surge-collector.last.log 2>&1 || true

bash /app/scripts/status_loop.sh >/tmp/health-dashboard-status-collector.log 2>&1 &
python3 /app/scripts/surge_collector.py --loop --interval "$SURGE_INTERVAL" >/tmp/health-dashboard-surge-collector.log 2>&1 &

exec python3 /app/scripts/serve.py
