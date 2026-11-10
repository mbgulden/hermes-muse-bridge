#!/bin/bash
# kanban-push.sh — Deliver a kanban event JSON file toward Muse's inbox.
# Lives on the box (webtop-hermes); called by kanban event hooks.
#
# ARCHITECTURE NOTE (2026-10-19): Muse's VM accesses the tailnet via an
# outbound-only HTTP CONNECT proxy. There is NO inbound network path from
# the box to the VM, so the HTTP POST to Muse's listener will always fail.
# The local file fallback IS the production path: the hook writes the event
# to /home/ubuntu/.hermes/muse-inbox/kanban-events/, and Muse's 5-min poll
# picks it up via SSH. The POST attempt is kept for future use if the network
# architecture ever allows inbound connections.
#
# Usage: kanban-push.sh /path/to/event.json
set -uo pipefail

MUSE_IP="100.121.81.71"
PORT="8899"
LISTENER_URL="http://${MUSE_IP}:${PORT}/kanban-event"
FALLBACK_DIR="/home/ubuntu/.hermes/muse-inbox/kanban-events"

if [ $# -lt 1 ]; then
  echo "Usage: $0 /path/to/event.json" >&2
  exit 2
fi

EVENT_FILE="$1"

if [ ! -f "$EVENT_FILE" ]; then
  echo "kanban-push: file not found: $EVENT_FILE" >&2
  exit 1
fi

# Validate JSON before sending (stdlib only)
if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$EVENT_FILE" 2>/dev/null; then
  echo "kanban-push: not valid JSON: $EVENT_FILE" >&2
  exit 1
fi

# Try the push; require the listener's {"ok": true} response
RESP=$(curl -s -m 10 -X POST "$LISTENER_URL" \
  -H "Content-Type: application/json" \
  --data-binary @"$EVENT_FILE" 2>/dev/null || true)

if echo "$RESP" | grep -q '"ok":[[:space:]]*true\|"ok": true'; then
  echo "kanban-push: pushed $(basename "$EVENT_FILE") to Muse's listener"
  exit 0
fi

# Fallback: copy to the local inbox dir; Muse's poll picks it up
mkdir -p "$FALLBACK_DIR" 2>/dev/null || true
if cp "$EVENT_FILE" "$FALLBACK_DIR/$(basename "$EVENT_FILE")" 2>/dev/null; then
  echo "kanban-push: POST failed, staged in $FALLBACK_DIR (poll will pick it up)" >&2
  exit 0
else
  echo "kanban-push: FAILED — POST failed and fallback copy failed" >&2
  exit 1
fi
