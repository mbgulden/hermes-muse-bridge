#!/bin/bash
# verify.sh — Verify a hermes-muse-bridge installation.
#
# Checks:
#   1. Config: config.yaml exists with required fields
#   2. Listener: local HTTP listener responding on 8899
#   3. Tunnel: box can reach the listener through the reverse tunnel
#   4. Daemon: kanban-events-daemon.py running on the box
#   5. Skills: muse-kanban-worker present on Muse VM
#
# Usage:
#   ./verify.sh [--help] [--config PATH]
#
# Exit 0 if all checks pass, 1 otherwise.

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${1:-$REPO_DIR/config.yaml}"
if [ "${1:-}" = "--help" ]; then
  sed -n '2,/^set -u/p' "$0" | sed 's/^# //; s/^#//'
  exit 0
fi
if [ "${1:-}" = "--config" ] && [ -n "${2:-}" ]; then
  CONFIG_FILE="$2"
fi

VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo "unknown")"
PASS=0; FAIL=0

check() { # $1=name, $2...=command (succeeds = pass)
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $name"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== hermes-muse-bridge verify (v$VERSION) ==="
echo ""

# ── 1. Config ──
echo "--- Config ---"
if [ -f "$CONFIG_FILE" ]; then
  echo "  PASS: config.yaml exists ($CONFIG_FILE)"
  PASS=$((PASS + 1))
  for field in "transport:" "box:" "kanban:"; do
    check "config has '$field'" grep -q "$field" "$CONFIG_FILE"
  done
else
  echo "  FAIL: config.yaml not found ($CONFIG_FILE)"
  FAIL=$((FAIL + 1))
fi

# Parse config (minimal)
TRANSPORT=$(grep "^transport:" "$CONFIG_FILE" 2>/dev/null | awk '{print $2}')
BOX_HOST=$(grep -A3 "^box:" "$CONFIG_FILE" 2>/dev/null | grep "host:" | awk '{print $2}' | tr -d '"')
BOX_USER=$(grep -A3 "^box:" "$CONFIG_FILE" 2>/dev/null | grep "user:" | awk '{print $2}' | tr -d '"')
TRANSPORT="${TRANSPORT:-tailscale-ssh}"
BOX_HOST="${BOX_HOST:-}"
BOX_USER="${BOX_USER:-ubuntu}"

ssh_box() { # $1 = remote command
  if [ "$TRANSPORT" = "localhost" ]; then
    eval "$1"
  else
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null "$BOX_USER@$BOX_HOST" "$1"
  fi
}

# ── 2. Listener ──
echo ""
echo "--- Muse VM listener ---"
check "listener responding on :8899" \
  bash -c 'curl -s -m 5 http://127.0.0.1:8899/health | grep -q "\"ok\""'

# ── 3. Tunnel (skip for localhost) ──
echo ""
echo "--- Reverse tunnel ---"
if [ "$TRANSPORT" = "localhost" ]; then
  echo "  SKIP: localhost transport needs no tunnel"
else
  if [ -z "$BOX_HOST" ]; then
    echo "  FAIL: box host not configured, cannot test tunnel"
    FAIL=$((FAIL + 1))
  else
    check "box reaches listener via tunnel (:18989)" \
      bash -c "ssh_box 'curl -s -m 5 http://localhost:18989/health' | grep -q '\"ok\"'"
  fi
fi

# ── 4. Daemon ──
echo ""
echo "--- Box daemon ---"
if [ "$TRANSPORT" = "localhost" ] || [ -n "$BOX_HOST" ]; then
  check "kanban-events-daemon.py running on box" \
    bash -c "ssh_box 'pgrep -f kanban-events-daemon.py' "
else
  echo "  SKIP: box not configured"
fi

# ── 5. Skills ──
echo ""
echo "--- Skills ---"
check "muse-kanban-worker skill present" \
  test -f "$HOME/workspace/custom-skills/muse-kanban-worker/SKILL.md"

# ── Summary ──
echo ""
echo "=== $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
