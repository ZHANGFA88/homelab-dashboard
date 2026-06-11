#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE"
mkdir -p data logs
export SURGE_INTERVAL="${SURGE_INTERVAL:-5}"
exec python3 scripts/surge_collector.py --loop --interval "$SURGE_INTERVAL"
