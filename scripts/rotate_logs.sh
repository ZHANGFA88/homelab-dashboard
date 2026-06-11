#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE"
mkdir -p logs
MAX_KB="${LOG_MAX_KB:-5120}"
KEEP="${LOG_KEEP:-5}"
find logs -type f -name '*.log' | while read -r f; do
  [ -f "$f" ] || continue
  kb=$(du -k "$f" | awk '{print $1}')
  if [ "${kb:-0}" -gt "$MAX_KB" ]; then
    ts=$(date +%Y%m%d-%H%M%S)
    mv "$f" "$f.$ts"
    : > "$f"
  fi
  old_files=$(ls -1t "$f".* 2>/dev/null | tail -n +$((KEEP+1)) || true)
  if [ -n "$old_files" ]; then
    printf '%s\n' "$old_files" | xargs rm -f
  fi
 done
