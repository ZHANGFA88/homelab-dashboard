#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
INTERVAL="${STATUS_INTERVAL:-30}"
cd "$BASE"
echo "[$(date '+%F %T')] status collector loop started, interval=${INTERVAL}s"
while true; do
  python3 scripts/collector.py >/tmp/health-dashboard-status-collector.last.log 2>&1 || true
  sleep "$INTERVAL"
done
