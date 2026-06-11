#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE"

PORT="${DASHBOARD_PORT:-8765}"
HOST="${DASHBOARD_HOST:-0.0.0.0}"
STATUS_INTERVAL="${STATUS_INTERVAL:-30}"
SURGE_INTERVAL="${SURGE_INTERVAL:-5}"

mkdir -p data

# 先刷新一次主面板状态，避免打开看到旧数据
python3 scripts/collector.py >/tmp/health-dashboard-status-collector.last.log 2>&1 || true

if ! lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
  DASHBOARD_HOST="$HOST" DASHBOARD_PORT="$PORT" nohup python3 scripts/serve.py >/tmp/health-dashboard-serve.log 2>&1 &
  echo $! > /tmp/health-dashboard-serve.pid
  echo "started web server: $! (${HOST}:${PORT})"
else
  echo "web server already listening on ${PORT}"
fi

if ! pgrep -f "scripts/surge_collector.py --loop" >/dev/null 2>&1; then
  nohup python3 scripts/surge_collector.py --loop --interval "$SURGE_INTERVAL" >/tmp/health-dashboard-surge-collector.log 2>&1 &
  echo $! > /tmp/health-dashboard-surge-collector.pid
  echo "started surge collector: $!"
else
  echo "surge collector already running"
fi

if ! pgrep -f "scripts/status_loop.sh" >/dev/null 2>&1; then
  STATUS_INTERVAL="$STATUS_INTERVAL" nohup bash scripts/status_loop.sh >/tmp/health-dashboard-status-collector.log 2>&1 &
  echo $! > /tmp/health-dashboard-status-collector.pid
  echo "started status collector: $!"
else
  echo "status collector already running"
fi

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
LOCAL_URL="http://127.0.0.1:${PORT}/public/index.html"
LAN_URL="http://${LAN_IP:-127.0.0.1}:${PORT}/public/index.html"
echo "$LOCAL_URL"
echo "$LAN_URL"
if [ "${DASHBOARD_OPEN_BROWSER:-0}" = "1" ] && command -v open >/dev/null 2>&1; then
  open "$LOCAL_URL" || true
fi
