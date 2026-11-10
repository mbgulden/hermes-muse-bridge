#!/bin/bash
# bridge-maint.sh — bridge housekeeping (audit 2026-11-09, decision 1).
# Runs from the hourly fleet-inbox-canary cron (first step, silent on success).
#   1. Prune the event spool to 48h on BOTH machines:
#      VM  ~/workspace/inbox/kanban-push/.processed/*.json
#      box ~/.hermes/muse-inbox/kanban-events/*
#      (requests/ and responses/ are the work channel — never pruned.)
#   2. Rotate bridge logs past 5 MB, keeping 3 generations:
#      VM watch.log, listener.log, tunnel.log.
#      (The box daemon log already self-rotates; kanban-daemon.log is empty.)
# Prints ONE summary line. Errors go to stderr and exit non-zero so the
# canary surfaces them; a transport-down box leg is a note, not a failure
# (the prune retries next hour; the SSH wall is the canary's own story).
set -u
MWH="$HOME/workspace/bin/mwh-safe.sh"
INBOX="$HOME/workspace/inbox"
CAP=$((5*1024*1024))
fail=0

rotate() {
  local f="$1"
  [ -f "$f" ] || return 0
  local sz
  sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
  if [ "$sz" -gt "$CAP" ]; then
    rm -f "$f.3"
    [ -f "$f.2" ] && mv "$f.2" "$f.3"
    [ -f "$f.1" ] && mv "$f.1" "$f.2"
    mv "$f" "$f.1"
    : > "$f"
    echo "rotated $f (${sz}B)"
  fi
}

# ── VM spool prune ──
VM_DIR="$INBOX/kanban-push/.processed"
vm_before=$(find "$VM_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l)
find "$VM_DIR" -maxdepth 1 -type f -mmin +2880 -delete 2>/dev/null
vm_after=$(find "$VM_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l)

# ── VM log rotation ──
rotate "$INBOX/watch.log"
rotate "$INBOX/listener.log"
rotate "$INBOX/tunnel.log"

# ── Box spool prune (base64 script — nested quoting eats inline patterns) ──
BOX_SCRIPT='EV=~/.hermes/muse-inbox/kanban-events
b=$(find $EV -maxdepth 1 -type f 2>/dev/null | wc -l)
find $EV -maxdepth 1 -type f -mmin +2880 -delete 2>/dev/null
a=$(find $EV -maxdepth 1 -type f 2>/dev/null | wc -l)
disk=$(df / | tail -1 | awk "{print \$5}")
echo "BOXPRUNE before=$b after=$a disk=$disk"'
B64=$(printf '%s' "$BOX_SCRIPT" | base64 -w0)
BOX_OUT=$("$MWH" "echo $B64 | base64 -d | bash" 2>/dev/null | grep BOXPRUNE | tail -1 || true)
if [ -z "$BOX_OUT" ]; then
  echo "bridge-maint: box leg unreachable this run (prune retries next hour)" >&2
  BOX_OUT="BOXPRUNE before=? after=? disk=?"
fi

echo "BRIDGE-MAINT vm-spool $vm_before->$vm_after | $BOX_OUT"
exit $fail
