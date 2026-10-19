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
#   BRIDGE_REMOTE_PORT   — port on box to forward (default: 18989)
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

# localhost mode: no tunnel needed
if [ "$TRANSPORT" = "localhost" ]; then
  log "localhost transport — no tunnel needed, ensuring listener only"
  ensure_listener
  heartbeat
  # Keep alive for work-guard (heartbeat every 5 min)
  while true; do sleep 300; ensure_listener; heartbeat; done
fi

log "starting (transport=$TRANSPORT, remote=$REMOTE_PORT -> local=$LOCAL_PORT)"
while true; do
  ensure_listener
  heartbeat

  mapfile -t SSH_ARGS < <(build_ssh_cmd)
  log "connecting reverse tunnel to $BOX_USER@$BOX_HOST"
  ssh "${SSH_ARGS[@]}" 2>>"$HOME/workspace/inbox/tunnel.log"
  rc=$?
  log "tunnel exited (rc=$rc), reconnecting in 10s"
  sleep 10
done
