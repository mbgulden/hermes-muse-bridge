#!/bin/bash
# fleet-request-canary.sh — end-to-end probe of the Muse REQUEST path
# (audit 2026-11-09, decision 3). Companion to fleet-canary.sh (event path)
# and fleet-claim-canary.sh (claim path). The event canary stayed green
# through the whole Nov 8 outage while this path was dead — hence this probe.
#
#   1. Request round-trip: plants a synthetic request in the box
#      muse-inbox/requests/ and waits for the watcher's response file.
#      Verdict carries the latency. Target <300s; FAIL at 900s (the audit's
#      alarm line). Cleans up its own request + response either way.
#   2. Watcher silence: newest "=== watch run end" line in watch.log must be
#      <10 min old. Scans the tail — the fresh log is small; the rotated
#      .1 is only consulted when the fresh log has no end line at all.
#   3. Spool/disk guard: total spool (box kanban-events + VM .processed)
#      must be <1 GB, box disk <92%, quarantine count reported.
# Prints exactly one verdict line: "REQUEST CANARY PASS ..." or
# "REQUEST CANARY FAIL: <leg>". No recovery here — recovery belongs to the
# event canary (units) and the watcher's own guards; this probe measures.
set -u
MWH="$HOME/workspace/bin/mwh-safe.sh"
INBOX="$HOME/workspace/inbox"
STAMP="$(date +%Y%m%d%H%M)"
ID="canary-req-${STAMP}"
LOG="$INBOX/work/request-canary.log"
mkdir -p "$INBOX/work"

log() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG"; }
box() { "$MWH" "$1" 2>/dev/null | grep -v 'Permanently added' | tail -1; }

fail() { echo "REQUEST CANARY FAIL: $1"; log "FAIL: $1"; exit 1; }

# ── Leg 2 first (cheap, local): watcher silence ──
LAST_END=$( { grep -a '=== watch run end' "$INBOX/watch.log" | tail -1; } )
if [ -z "$LAST_END" ] && [ -f "$INBOX/watch.log.1" ]; then
  LAST_END=$(grep -a '=== watch run end' "$INBOX/watch.log.1" | tail -1)
fi
TS=$(printf '%s' "$LAST_END" | sed -n 's/^\[\([0-9T:-]*\)Z\].*/\1/p' | head -1)
if [ -z "$TS" ]; then
  fail "watcher silence: no completed run found in watch.log"
fi
EPOCH=$(date -u -d "${TS/T/ } UTC" +%s 2>/dev/null || echo 0)
AGE=$(( $(date +%s) - EPOCH ))
if [ "$EPOCH" -eq 0 ] || [ "$AGE" -gt 600 ]; then
  fail "watcher silence: last completed run ${AGE}s ago (>600s)"
fi

# ── Leg 3: spool + disk guard (one box call) ──
GUARD=$(box 'EV=$(du -sm ~/.hermes/muse-inbox/kanban-events 2>/dev/null | cut -f1); DISK=$(df / | tail -1 | awk "{print \$5}" | tr -dc 0-9); Q=$(ls ~/.hermes/muse-inbox/quarantine/ 2>/dev/null | wc -l); echo "ev=${EV:-?} disk=${DISK:-?} quar=${Q:-?}"')
EV_MB=$(printf '%s' "$GUARD" | sed -n 's/.*ev=\([0-9?]*\).*/\1/p')
DISK_PCT=$(printf '%s' "$GUARD" | sed -n 's/.*disk=\([0-9?]*\).*/\1/p')
QUAR=$(printf '%s' "$GUARD" | sed -n 's/.*quar=\([0-9?]*\).*/\1/p')
VM_MB=$(du -sm "$INBOX/kanban-push/.processed" 2>/dev/null | cut -f1)
VM_MB=${VM_MB:-0}
if [ "${EV_MB:-?}" != "?" ] && [ $((EV_MB + VM_MB)) -gt 1024 ]; then
  fail "spool guard: total spool $((EV_MB + VM_MB))MB > 1024MB (box ${EV_MB}MB + vm ${VM_MB}MB)"
fi
if [ "${DISK_PCT:-?}" != "?" ] && [ "$DISK_PCT" -gt 92 ]; then
  fail "spool guard: box disk at ${DISK_PCT}% (>92%)"
fi

# ── Leg 1: request round-trip ──
REQ_BODY="---
id: $ID
from: canary
to: muse
type: question
created: $(date -u +%FT%TZ)
---

Synthetic request-path canary (fleet-request-canary). No action needed —
the watcher's automatic response is the round-trip being timed."
RB64=$(printf '%s' "$REQ_BODY" | base64 -w0)
box "echo $RB64 | base64 -d > ~/.hermes/muse-inbox/requests/$ID.md" >/dev/null
START=$(date +%s)
FOUND=0
while [ $(( $(date +%s) - START )) -lt 900 ]; do
  R=$(box "test -f ~/.hermes/muse-inbox/responses/$ID.response.md && echo yes || echo no")
  if [ "$R" = "yes" ]; then FOUND=1; break; fi
  sleep 20
done
LAT=$(( $(date +%s) - START ))
box "rm -f ~/.hermes/muse-inbox/requests/$ID.md ~/.hermes/muse-inbox/responses/$ID.response.md" >/dev/null
if [ "$FOUND" -ne 1 ]; then
  fail "request path: no response to $ID within 900s"
fi
log "PASS latency=${LAT}s spool_box=${EV_MB}MB spool_vm=${VM_MB}MB disk=${DISK_PCT}% quarantine=${QUAR}"
echo "REQUEST CANARY PASS latency=${LAT}s spool=$(( ${EV_MB:-0} + VM_MB ))MB disk=${DISK_PCT}% quarantine=${QUAR}"
