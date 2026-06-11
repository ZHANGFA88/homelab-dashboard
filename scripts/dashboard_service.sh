#!/usr/bin/env bash
set -euo pipefail
BASE="$(cd "$(dirname "$0")/.." && pwd)"
UID_NUM="$(id -u)"
AGENTS=(
  "com.lvxin.health-dashboard:$HOME/Library/LaunchAgents/com.lvxin.health-dashboard.plist"
  "com.lvxin.health-dashboard.status:$HOME/Library/LaunchAgents/com.lvxin.health-dashboard.status.plist"
  "com.lvxin.health-dashboard.surge:$HOME/Library/LaunchAgents/com.lvxin.health-dashboard.surge.plist"
  "com.lvxin.health-dashboard.logrotate:$HOME/Library/LaunchAgents/com.lvxin.health-dashboard.logrotate.plist"
)
usage(){ echo "Usage: $0 {start|stop|restart|status|logs}"; }
start(){
  for item in "${AGENTS[@]}"; do
    label="${item%%:*}"; plist="${item#*:}"
    launchctl print "gui/$UID_NUM/$label" >/dev/null 2>&1 || launchctl bootstrap "gui/$UID_NUM" "$plist"
    launchctl kickstart -k "gui/$UID_NUM/$label" || true
  done
}
stop(){
  for item in "${AGENTS[@]}"; do
    label="${item%%:*}"; plist="${item#*:}"
    launchctl bootout "gui/$UID_NUM" "$plist" >/dev/null 2>&1 || true
  done
}
status(){
  for item in "${AGENTS[@]}"; do
    label="${item%%:*}"
    echo "## $label"
    launchctl print "gui/$UID_NUM/$label" 2>/dev/null | grep -E 'state =|pid =|runs =|last exit code|last terminating' | sed -n '1,24p' || echo "not loaded"
  done
  echo "## listen"
  lsof -nP -iTCP:8765 -sTCP:LISTEN || true
  echo "## url"
  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
  echo "http://${LAN_IP:-127.0.0.1}:8765/public/index.html"
}
logs(){
  cd "$BASE"
  for f in logs/launchd-web.out.log logs/launchd-web.err.log logs/launchd-status.out.log logs/launchd-status.err.log logs/launchd-surge.out.log logs/launchd-surge.err.log; do
    echo "## $f"; tail -n 40 "$f" 2>/dev/null || true
  done
}
case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status) status ;;
  logs) logs ;;
  *) usage; exit 2 ;;
esac
