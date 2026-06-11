#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE"
mkdir -p data logs
export DASHBOARD_HOST="${DASHBOARD_HOST:-0.0.0.0}"
export DASHBOARD_PORT="${DASHBOARD_PORT:-8765}"
# 启动前刷新一次状态，避免页面看到过旧数据
python3 scripts/collector.py > logs/status-collector.last.log 2>&1 || true
exec python3 scripts/serve.py
