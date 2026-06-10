#!/usr/bin/env bash
set -euo pipefail
pkill -f "scripts/surge_collector.py --loop" 2>/dev/null || true
pkill -f "scripts/status_loop.sh" 2>/dev/null || true
# 不默认杀 8765 Web 服务，避免误杀其他 Python；如需停止可手动：kill $(cat /tmp/health-dashboard-serve.pid)
echo "stopped collectors. web server left running if already active."
