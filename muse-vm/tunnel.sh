#!/bin/bash
# muse-tunnel.sh — Persistent reverse SSH tunnel: Hermes box → Muse VM.
#
# Opens ssh -N -R <remote_port>:localhost:<local_port> to the Hermes box,
# so the box can POST kanban events to http://localhost:<remote_port>/kanban-event
# and reach Muse's HTTP listener.
#
# Transport modes (set via environment or config):
#   tailscale-ssh: SSH through Tailscale (uses ProxyCommand if TS_TUNNEL_PROXY set)
#   direct-ssh:    Plain SSH to box host
#   localhost:     No tunnel needed — exits immediately
#
# Self-healing: loops forever, reconnects on drop, heartbeats to work-guard.
#
# Environment:
#   BRIDGE_TRANSPORT     — tailscale-ssh | direct-ssh | localhost (default: tailscale-ssh)
#   BRIDGE_BOX_HOST      — box hostname/IP (default: from config or localhost)
#   BRIDGE_BOX_USER      — SSH user (default: ubuntu)
#   BRIDGE_REMOTE_PORT   — port on box to forward (default: 18989; ignored when
#                          BRIDGE_PORT_ROTATION=1, which uses the pool below)
#   BRIDGE_PORT_ROTATION — 1 to rotate through a port pool (default: 1).
#                          Works around Tailscale SSH not releasing reverse-forward
#                          ports when connections die ungracefully. The live port
#                          is published to the box via $BRIDGE_PORT_FILE.
#   BRIDGE_PORT_BASE     — first port in rotation pool (default: 18991)
#   BRIDGE_PORT_COUNT    — pool size (default: 10, i.e. 18991-19000)
#   BRIDGE_PORT_FILE     — path on box where live port is written
#                          (default: /home/ubuntu/.hermes/jimmy-listener-port)
#   BRIDGE_LOCAL_PORT    — local listener port (default: 8899)
#   BRIDGE_SSH_KEY       — path to SSH key (default: ~/.ssh/id_ed25519)
#   BRIDGE_PROXY_CMD     — ProxyCommand string (for Tailscale tunnel proxy)
#
# Usage:
#   ./muse-tunnel.sh [--help]
#
# Register with work-guard for failsafe restarts:
#   work-guard.sh register muse-tunnel "Reverse SSH tunnel box->VM" "~/workspace/bin/muse-tunnel.sh"
set -u

if [ "${1:-}" = "--help" ]; then
  sed -n '2,/^set -u/p' "$0" | sed 's/^# //; s/^#//'
  exit 0
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_GUARD="$HERE/work-guard.sh"
HEARTBEAT_FILE="$HOME/workspace/.work-heartbeat-muse-tunnel"

TRANSPORT="${BRIDGE_TRANSPORT:-tailscale-ssh}"
BOX_HOST="${BRIDGE_BOX_HOST:-localhost}"
BOX_USER="${BRIDGE_BOX_USER:-ubuntu}"
REMOTE_PORT="${BRIDGE_REMOTE_PORT:-18989}"
LOCAL_PORT="${BRIDGE_LOCAL_PORT:-8899}"
SSH_KEY="${BRIDGE_SSH_KEY:-$HOME/.ssh/id_ed25519}"
PORT_ROTATION="${BRIDGE_PORT_ROTATION:-1}"
PORT_BASE="${BRIDGE_PORT_BASE:-18991}"
PORT_COUNT="${BRIDGE_PORT_COUNT:-10}"
PORT_FILE="${BRIDGE_PORT_FILE:-/home/ubuntu/.hermes/jimmy-listener-port}"
PID_FILE="$HOME/workspace/.tunnel.pid"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) muse-tunnel: $*" >> "$HOME/workspace/inbox/tunnel.log"; }

heartbeat() {
  touch "$HEARTBEAT_FILE" 2>/dev/null || true
  [ -x "$WORK_GUARD" ] && "$WORK_GUARD" heartbeat muse-tunnel 2>/dev/null || true
}

ensure_listener() {
  if ! curl -s --max-time 3 "http://127.0.0.1:$LOCAL_PORT/health" 2>/dev/null | grep -q '"ok"'; then
    log "listener not responding, starting it"
    nohup python3 "$HERE/inbox-listener.py" >/dev/null 2>&1 &
    sleep 2
  fi
}

build_ssh_cmd() {
  local args=()
  args+=(-N)
  args+=(-o BatchMode=yes)
  args+=(-o ConnectTimeout=15)
  args+=(-o StrictHostKeyChecking=no)
  args+=(-o UserKnownHostsFile=/dev/null)
  args+=(-o ServerAliveInterval=30)
  args+=(-o ServerAliveCountMax=3)
  args+=(-o ExitOnForwardFailure=yes)
  [ -f "$SSH_KEY" ] && args+=(-i "$SSH_KEY")
  if [ -n "${BRIDGE_PROXY_CMD:-}" ]; then
    args+=(-o "ProxyCommand=$BRIDGE_PROXY_CMD")
  elif [ "$TRANSPORT" = "tailscale-ssh" ] && [ -n "${HTTPS_PROXY:-}" ]; then
    # Derive Tailscale tunnel proxy from HTTPS_PROXY (port 3128 → 3130)
    local tproxy
    tproxy="$(echo "$HTTPS_PROXY" | sed 's/:3128$/:3130/')"
    args+=(-o "ProxyCommand=python3 $HERE/tsproxy.py %h %p")
    export TS_TUNNEL_PROXY="$tproxy"
  fi
  args+=(-R "$REMOTE_PORT:localhost:$LOCAL_PORT")
  args+=("$BOX_USER@$BOX_HOST")
  printf '%s\n' "${args[@]}"
}

# Publish the live remote port to the box so the daemon knows where to POST
publish_port() {
  local port=$1
  ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      ${BRIDGE_PROXY_CMD:+-o "ProxyCommand=$BRIDGE_PROXY_CMD"} \
      "$BOX_USER@$BOX_HOST" "echo '$port' > $PORT_FILE" 2>/dev/null
  if [ $? -eq 0 ]; then
    log "published port $port to box"
    return 0
  else
    log "WARN: failed to publish port to box"
    return 1
  fi
}

# Find a free remote port from the rotation pool (checks box via SSH)
find_free_port() {
  local start_offset=$(($(date +%s) % PORT_COUNT))
  for i in $(seq 0 $((PORT_COUNT - 1))); do
    local offset=$(((start_offset + i) % PORT_COUNT))
    local port=$((PORT_BASE + offset))
    if ! ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        ${BRIDGE_PROXY_CMD:+-o "ProxyCommand=$BRIDGE_PROXY_CMD"} \
        "$BOX_USER@$BOX_HOST" "ss -tln 2>/dev/null | grep -q ':$port '" >/dev/null 2>&1; then
      echo $port
      return 0
    fi
  done
  echo $((PORT_BASE + (start_offset % PORT_COUNT)))
}

# Singleton via PID file
singleton_check() {
  if [ -f "$PID_FILE" ]; then
    local old_pid
    old_pid=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
      if ps -p "$old_pid" -o args= 2>/dev/null | grep -q "tunnel\.sh"; then
        log "another instance running (pid=$old_pid), exiting"
        exit 0
      fi
    fi
  fi
  echo $$ > "$PID_FILE"
  # Background heartbeat: the foreground loop blocks inside `ssh -N` for the
  # tunnel's whole life, so a per-iteration heartbeat goes stale on healthy
  # tunnels and the watchdog false-restarts them. Feed the watchdog from a
  # subshell every 60s instead. (Fix applied Oct 20, 2026.)
  ( while true; do heartbeat; sleep 60; done ) &
  HB_PID=$!
  trap 'kill $HB_PID 2>/dev/null; rm -f "$PID_FILE"' EXIT
}

# localhost mode: no tunnel needed
if [ "$TRANSPORT" = "localhost" ]; then
  log "localhost transport — no tunnel needed, ensuring listener only"
  ensure_listener
  heartbeat
  # Keep alive for work-guard (heartbeat every 5 min)
  while true; do sleep 300; ensure_listener; heartbeat; done
fi

log "starting (transport=$TRANSPORT, remote=$REMOTE_PORT -> local=$LOCAL_PORT)"
singleton_check

# Port rotation: pick a free port from the pool and publish it to the box
if [ "$PORT_ROTATION" = "1" ] && [ "$TRANSPORT" != "localhost" ]; then
  REMOTE_PORT=$(find_free_port)
  log "port rotation: selected remote port $REMOTE_PORT"
  publish_port $REMOTE_PORT
fi

while true; do
  ensure_listener
  heartbeat

  mapfile -t SSH_ARGS < <(build_ssh_cmd)
  log "connecting reverse tunnel to $BOX_USER@$BOX_HOST (remote=$REMOTE_PORT)"
  ssh "${SSH_ARGS[@]}" 2>>"$HOME/workspace/inbox/tunnel.log"
  rc=$?
  # If bind failed, rotate to the next free port
  if [ "$PORT_ROTATION" = "1" ] && tail -5 "$HOME/workspace/inbox/tunnel.log" 2>/dev/null | grep -q "forwarding failed"; then
    OLD_PORT=$REMOTE_PORT
    REMOTE_PORT=$(find_free_port)
    log "port $OLD_PORT blocked, rotating to $REMOTE_PORT"
    publish_port $REMOTE_PORT
  else
    log "tunnel exited (rc=$rc), reconnecting in 10s"
  fi
  sleep 10
done
