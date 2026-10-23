#!/usr/bin/env bash
# fleet-review-trigger.sh — Muse hook script (install to ~/hooks/scripts/).
#
# Wakes a review-sweep pass the moment a significant kanban lifecycle event
# lands in the push inbox (completed/blocked/crashed/failed/gave_up/timed_out,
# or any event whose status is 'review'), instead of waiting for the next
# scheduled sweep. Pair it with a Muse hook definition whose prompt is the
# review-pass prompt in review-pass-prompt.md, polling every ~20s.
#
# The hook runtime ($HATCH_HOOK_RUNTIME) provides: silent, log, wake.
# State: ~/hooks/state/fleet-review-trigger.watermark = highest numeric event
# id already accounted for. Dry runs never advance it.
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

STATE_DIR="$HOME/hooks/state"
WM_FILE="$STATE_DIR/fleet-review-trigger.watermark"
PUSH_DIR="$HOME/workspace/inbox/kanban-push"
mkdir -p "$STATE_DIR"

WM=0
if [ -f "$WM_FILE" ]; then WM="$(cat "$WM_FILE")"; fi

# Collect candidate event files (both the live dir and .processed) with a
# numeric id greater than the watermark.
RESULT="$(python3 - "$WM" "$PUSH_DIR" <<'PYEOF'
import json, os, sys
wm = int(sys.argv[1]); push = sys.argv[2]
significant = {"completed", "blocked", "crashed", "failed", "gave_up", "gave-up", "timed_out"}
events = []
max_id = wm
for sub in (push, os.path.join(push, ".processed")):
    try:
        names = os.listdir(sub)
    except OSError:
        continue
    for name in names:
        if not name.endswith(".json"):
            continue
        stem = name[:-5]
        if not stem.isdigit():
            continue
        eid = int(stem)
        if eid <= wm:
            continue
        if eid > max_id:
            max_id = eid
        try:
            with open(os.path.join(sub, name)) as f:
                d = json.load(f)
        except Exception:
            continue
        kind = str(d.get("kind", ""))
        status = str(d.get("status", ""))
        if kind in significant or status == "review":
            events.append({"id": eid, "task_id": d.get("task_id"), "kind": kind, "status": status})
events.sort(key=lambda e: e["id"])
task_ids = []
for e in events:
    if e["task_id"] and e["task_id"] not in task_ids:
        task_ids.append(e["task_id"])
print(json.dumps({"max_id": max_id, "events": events[:20], "task_ids": task_ids}))
PYEOF
)"

MAX_ID="$(printf '%s' "$RESULT" | jq -r '.max_id')"
N_EVENTS="$(printf '%s' "$RESULT" | jq '.events | length')"

if [ "$N_EVENTS" -eq 0 ]; then
  if [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ] && [ "$MAX_ID" -gt "$WM" ]; then
    printf '%s' "$MAX_ID" > "$WM_FILE"
  fi
  silent "no significant kanban events since watermark $WM" "{\"watermark\": $MAX_ID}"
fi

if [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then
  printf '%s' "$MAX_ID" > "$WM_FILE"
fi
log "significant events detected" "$RESULT"
wake "Significant kanban lifecycle events landed via push" "$RESULT"
