#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE"
mkdir -p data logs
export STATUS_INTERVAL="${STATUS_INTERVAL:-30}"
exec bash scripts/status_loop.sh
