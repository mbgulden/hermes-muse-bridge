#!/bin/bash
# fleet-canary.sh — end-to-end probe of the Hermes→Jimmy event path.
# Plants a synthetic event ON THE BOX and confirms it travels the real path:
# box POST -> reverse tunnel -> Jimmy listener -> push inbox file.
# Also confirms the kanban-events daemon is alive on the box.
# Prints exactly one verdict line: "CANARY PASS" or "CANARY FAIL: <leg>".
# On failure, runs the known recovery (install-jimmy-units.sh) and retries once.
#
# IMPORTANT: the tunnel rotates its remote port and publishes the current one
# to the box (see tunnel.sh — the port file). Read the port from there.
# Never hardcode a port: a hardcoded port silently tests a dead socket while
# the real path is healthy (learned in production).
set -u
ID="canary-$(date +%s)"
PUSH_DIR="$HOME/workspace/inbox/kanban-push"
MWH="${BRIDGE_SSH_CMD:-$HOME/workspace/bin/mwh-safe.sh}"
PORT_FILE="${BRIDGE_PORT_FILE:-/home/ubuntu/.hermes/jimmy-listener-port}"
FALLBACK_PORT="${BRIDGE_REMOTE_PORT:-18997}"

# The tunnel publishes its current remote port to the box; read it — never hardcode it.
REMOTE_PORT="$("$MWH" "cat $PORT_FILE 2>/dev/null" 2>/dev/null | tail -1 | tr -dc '0-9')"
[ -z "$REMOTE_PORT" ] && REMOTE_PORT="$FALLBACK_PORT"

post_canary() {
  "$MWH" "curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:$REMOTE_PORT/kanban-event -H 'Content-Type: application/json' -d '{\"id\":\"$ID\",\"task_id\":\"canary\",\"kind\":\"canary\"}'" 2>/dev/null | tail -1
}

daemon_alive() {
  "$MWH" "pgrep -f kanban-events-daemon.py >/dev/null && echo yes || echo no" 2>/dev/null | tail -1
}

wait_for_file() {
  local tries=0
  while [ $tries -lt 18 ]; do
    if [ -f "$PUSH_DIR/$ID.json" ] || [ -f "$PUSH_DIR/.processed/$ID.json" ]; then
      return 0
    fi
    sleep 5; tries=$((tries+1))
  done
  return 1
}

cleanup() {
  rm -f "$PUSH_DIR/$ID.json" "$PUSH_DIR/.processed/$ID.json"
}

CODE="$(post_canary)"
if [ "$CODE" != "200" ]; then
  # Recovery attempt: reinstall/restart tunnel + listener units, then retry once.
  "$HOME/workspace/bin/install-jimmy-units.sh" >/dev/null 2>&1
  sleep 20
  CODE="$(post_canary)"
  if [ "$CODE" != "200" ]; then
    echo "CANARY FAIL: box POST through tunnel returned '$CODE' after unit recovery"
    exit 1
  fi
fi

if ! wait_for_file; then
  echo "CANARY FAIL: POST accepted (200) but $ID.json never landed in the push inbox"
  exit 1
fi

ALIVE="$(daemon_alive)"
cleanup
if [ "$ALIVE" != "yes" ]; then
  echo "CANARY FAIL: tunnel+listener path works but kanban-events-daemon is not running on the box"
  exit 1
fi

echo "CANARY PASS"
