#!/usr/bin/env bash
# Runs a PIN-gated copy of ECHO on this Linux host and publishes it over HTTPS with a Cloudflare quick tunnel.
# Usage: deploy/box-live.sh start|stop|status|url|restart-server|watch
set -u
SRC="$(cd "$(dirname "$0")/.." && pwd)"
LIVE="${ECHO_LIVE_DIR:-/workspace/echo-live}"
VENV="${ECHO_VENV:-/workspace/.echo-venv}"
PORT="${ECHO_LIVE_PORT:-8795}"
CF="${CLOUDFLARED:-$VENV/bin/cloudflared}"
LOG="$LIVE/logs"
sync_code() {
  mkdir -p "$LIVE/reports" "$LOG"
  rm -rf "$LIVE/app" "$LIVE/engine"
  (cd "$SRC" && tar --exclude='__pycache__' -cf - app engine requirements.txt) | (cd "$LIVE" && tar -xf -)
}
srv_up() { curl -fs -o /dev/null "http://127.0.0.1:$PORT/healthz"; }
start_srv() {
  srv_up && return 0
  ( cd "$LIVE" && ECHO_BIND=cloud ECHO_PORT=$PORT setsid nohup "$VENV/bin/gunicorn" --chdir app -w 1 --threads 16 \
      --timeout 0 -b 127.0.0.1:$PORT server:app >>"$LOG/server.log" 2>&1 < /dev/null & )
  for i in $(seq 1 40); do sleep 0.5; srv_up && return 0; done; echo "server failed - see $LOG/server.log"; return 1
}
start_tunnel() {
  pgrep -f "cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$PORT" >/dev/null && return 0
  : >"$LOG/tunnel.log"
  setsid nohup "$CF" tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >>"$LOG/tunnel.log" 2>&1 < /dev/null &
  for i in $(seq 1 60); do
    sleep 1; u=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG/tunnel.log" | head -1)
    [ -n "$u" ] && { echo "$u" >"$LIVE/.echo_public_url"; return 0; }
  done; echo "tunnel failed - see $LOG/tunnel.log"; return 1
}
case "${1:-status}" in
  start) sync_code; start_srv && start_tunnel && cat "$LIVE/.echo_public_url" ;;
  restart-server) sync_code; pkill -f "gunicorn.*127.0.0.1:$PORT"; sleep 1.5; start_srv && echo ok ;;
  stop) pkill -f "echo-live-watch"; pkill -f "cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$PORT"; pkill -f "gunicorn.*127.0.0.1:$PORT"; rm -f "$LIVE/.echo_public_url"; echo stopped ;;
  url) cat "$LIVE/.echo_public_url" 2>/dev/null ;;
  watch) exec -a echo-live-watch bash -c "while true; do '$0' _tick >/dev/null 2>&1; sleep 30; done" ;;
  _tick) start_srv; start_tunnel ;;
  *) srv_up && echo "server up" || echo "server down"; pgrep -f "cloudflared tunnel" >/dev/null && echo "tunnel up: $(cat "$LIVE/.echo_public_url" 2>/dev/null)" || echo "tunnel down" ;;
esac
