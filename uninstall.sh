#!/bin/bash
# uninstall.sh — Cleanly remove a hermes-muse-bridge installation.
#
# Removes:
#   1. Box daemon (kanban-events-daemon.py) + @reboot cron entry (with confirmation)
#   2. Local listener and tunnel processes
#   3. Installed scripts in ~/workspace/bin/
#   4. Deployed skills (with confirmation)
#   5. config.yaml (asks first — you may want to keep it for reinstall)
#
# Usage:
#   ./uninstall.sh [--help] [--yes] [--config PATH]
#   --yes: skip all confirmation prompts

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$REPO_DIR/config.yaml"
YES=0

for arg in "$@"; do
  case "$arg" in
    --help) sed -n '2,/^set -u/p' "$0" | sed 's/^# //; s/^#//'; exit 0 ;;
    --yes) YES=1 ;;
    --config) shift; CONFIG_FILE="$1" ;;
  esac
done

VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo "unknown")"
echo "=== hermes-muse-bridge uninstall (v$VERSION) ==="
echo ""

confirm() { # $1=prompt — returns 0 if yes
  [ "$YES" = "1" ] && return 0
  local ans
  read -rp "$1 [y/N]: " ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ]
}

# Parse config if present
TRANSPORT="tailscale-ssh"; BOX_HOST=""; BOX_USER="ubuntu"; BOX_HOME="/home/ubuntu/.hermes"
if [ -f "$CONFIG_FILE" ]; then
  TRANSPORT=$(grep "^transport:" "$CONFIG_FILE" | awk '{print $2}')
  BOX_HOST=$(grep -A3 "^box:" "$CONFIG_FILE" | grep "host:" | awk '{print $2}' | tr -d '"')
  BOX_USER=$(grep -A3 "^box:" "$CONFIG_FILE" | grep "user:" | awk '{print $2}' | tr -d '"')
  TRANSPORT="${TRANSPORT:-tailscale-ssh}"
  BOX_USER="${BOX_USER:-ubuntu}"
  BOX_HOME="/home/$BOX_USER/.hermes"
fi

ssh_box() {
  if [ "$TRANSPORT" = "localhost" ]; then
    eval "$1"
  else
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null "$BOX_USER@$BOX_HOST" "$1" 2>/dev/null
  fi
}

# ── 1. Box daemon ──
echo "--- Box daemon ---"
if confirm "Stop kanban-events-daemon.py on the box and remove its @reboot cron?"; then
  ssh_box "pkill -f kanban-events-daemon.py 2>/dev/null; (crontab -l 2>/dev/null | grep -v 'kanban-events-daemon.py') | crontab -" \
    && echo "  Removed daemon + cron on box" \
    || echo "  (box unreachable or nothing to remove)"
else
  echo "  Skipped"
fi

# ── 2. Local listener + tunnel ──
echo ""
echo "--- Local processes ---"
pkill -f "inbox-listener.py" 2>/dev/null && echo "  Stopped listener" || echo "  Listener not running"
pkill -f "muse-tunnel.sh" 2>/dev/null && echo "  Stopped tunnel" || echo "  Tunnel not running"

# ── 3. Installed scripts ──
echo ""
echo "--- Installed scripts ---"
for f in "$HOME/workspace/bin/inbox-listener.py" "$HOME/workspace/bin/tunnel.sh" \
         "$HOME/workspace/bin/muse-tunnel.sh" "$HOME/workspace/bin/inbox-watch.sh" \
         "$HOME/workspace/bin/bridge-env.sh"; do
  if [ -f "$f" ]; then
    rm -f "$f" && echo "  Removed $(basename "$f")"
  fi
done

# ── 4. Skills ──
echo ""
echo "--- Skills ---"
if confirm "Remove the muse-kanban-worker skill from ~/workspace/custom-skills/?"; then
  rm -rf "$HOME/workspace/custom-skills/muse-kanban-worker" \
    && echo "  Removed muse-kanban-worker" || echo "  (not present)"
else
  echo "  Skipped"
fi
echo "  Note: remove skills/call-muse from Hermes profiles manually on the box if desired."

# ── 5. Config ──
echo ""
echo "--- Config ---"
if [ -f "$CONFIG_FILE" ]; then
  if confirm "Delete $CONFIG_FILE? (keep it if you plan to reinstall)"; then
    rm -f "$CONFIG_FILE" && echo "  Removed config.yaml"
  else
    echo "  Kept config.yaml"
  fi
fi

echo ""
echo "Uninstall complete. The Muse watcher cron (if any) must be removed manually"
echo "via your Muse agent's cron management."
