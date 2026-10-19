#!/bin/bash
# inbox-watch.sh — Poll the Hermes box for agent requests, kanban events, and tasks.
#
# Runs every minute via Muse cron. Does four things:
#   1. Checks tunnel-push inbox (~/workspace/inbox/kanban-push/) for instant events
#   2. Checks <hermes_home>/muse-inbox/requests/ on the box for agent requests
#   3. Polls kanban.db task_events as fallback (catches tunnel misses)
#   4. Claims tasks assigned to $BRIDGE_ASSIGNEE and prepares them for subagents
#
# Transport modes (BRIDGE_TRANSPORT):
#   tailscale-ssh, direct-ssh: use SSH via $BRIDGE_SSH_CMD to reach the box
#   localhost: box paths are local, no SSH needed
#
# Environment:
#   BRIDGE_TRANSPORT  — transport mode (default: tailscale-ssh)
#   BRIDGE_SSH_CMD    — SSH command prefix (default: mwh-safe.sh equivalent)
#   BRIDGE_BOX_HOME   — Hermes home on box (default: /home/ubuntu/.hermes)
#   BRIDGE_ASSIGNEE   — kanban assignee to claim (default: muse)
#
# Usage:
#   ./inbox-watch.sh [--help]

set -euo pipefail

if [ "${1:-}" = "--help" ]; then
  sed -n '2,/^set -euo/p' "$0" | sed 's/^# //; s/^#//'
  exit 0
fi

TRANSPORT="${BRIDGE_TRANSPORT:-tailscale-ssh}"
BOX_HOME="${BRIDGE_BOX_HOME:-/home/ubuntu/.hermes}"
ASSIGNEE="${BRIDGE_ASSIGNEE:-muse}"
export BOX_HOME ASSIGNEE  # Available to Python heredocs via os.environ

# SSH command: override with BRIDGE_SSH_CMD, or use local exec for localhost mode
if [ "$TRANSPORT" = "localhost" ]; then
  # Local mode: run commands directly (box is this machine)
  mwh() { eval "$1"; }
  MWH="mwh"
else
  MWH="${BRIDGE_SSH_CMD:-$HOME/workspace/bin/mwh-safe.sh}"
  mwh() { "$MWH" "$1" 2>/dev/null | grep -v "Warning:" || true; }
fi

REMOTE_REQ="$BOX_HOME/muse-inbox/requests"
REMOTE_RESP="$BOX_HOME/muse-inbox/responses"
KANBAN_DB="$BOX_HOME/kanban.db"
LOCAL_WORK="$HOME/workspace/inbox/work"
LOG="$HOME/workspace/inbox/watch.log"

mkdir -p "$LOCAL_WORK"

log() {
  echo "[$(date -u +%FT%TZ)] $*" >> "$LOG"
}

# Self-healing HTTP listener check
if ! curl -s -m 3 http://127.0.0.1:8899/health | grep -q '"ok"'; then
  log "listener down — restarting"
  ~/workspace/bin/ensure-listener.sh
fi

log "=== watch run start ==="

# ── Step 1: Tunnel push inbox ──
# The box's kanban-events-daemon POSTs events through the reverse SSH tunnel
# to the local listener, which writes them as JSON to kanban-push/.
# Process each: log with KANBAN prefix, then archive to .processed/.
PUSH_DIR="$HOME/workspace/inbox/kanban-push"
PROCESSED_DIR="$PUSH_DIR/.processed"
mkdir -p "$PROCESSED_DIR"

push_count=0
for pf in "$PUSH_DIR"/*.json; do
  [ -f "$pf" ] || continue
  fname=$(basename "$pf")
  summary=$(python3 -c "
import json
try:
    with open('$pf') as f:
        d = json.load(f)
    kind = d.get('kind', '?')
    title = d.get('title', d.get('task_id', '?'))
    print(f'{kind} — {title}')
except Exception as e:
    print(f'unparseable ({e})')
" 2>/dev/null)
  log "KANBAN push: $fname: $summary"
  mv "$pf" "$PROCESSED_DIR/$fname"
  push_count=$((push_count + 1))
done
[ "$push_count" -gt 0 ] && log "kanban-push: processed $push_count file(s)"

# ── Step 2: Agent inbox ──
# List request files on the box
FILES=$($MWH "ls -1 $REMOTE_REQ/*.md 2>/dev/null" 2>/dev/null | grep -v "Warning:" || true)

if [ -z "$FILES" ]; then
  log "no requests"
fi

if [ -n "$FILES" ]; then
for f in $FILES; do
  fname=$(basename "$f")
  id="${fname%.md}"

  # Skip if already processed (response exists)
  EXISTS=$($MWH "test -f $REMOTE_RESP/${id}.response.md && echo yes || echo no" 2>/dev/null | grep -v "Warning:" | tail -1)
  if [ "$EXISTS" = "yes" ]; then
    log "skip $id (already has response)"
    continue
  fi

  log "processing $id"

  # Download the request
  B64=$($MWH "base64 -w0 '$f'" 2>/dev/null | grep -v "Warning:" | tail -1)
  echo "$B64" | base64 -d > "$LOCAL_WORK/${id}.md"

  # Parse frontmatter
  TYPE=$(grep -m1 "^type:" "$LOCAL_WORK/${id}.md" | sed 's/type:[[:space:]]*//')
  FROM=$(grep -m1 "^from:" "$LOCAL_WORK/${id}.md" | sed 's/from:[[:space:]]*//')
  SKILL=$(grep -m1 "^skill:" "$LOCAL_WORK/${id}.md" | sed 's/skill:[[:space:]]*//' || echo "")

  log "  type=$TYPE from=$FROM skill=$SKILL"

  # Build response
  RESP="$LOCAL_WORK/${id}.response.md"
  {
    echo "---"
    echo "id: $id"
    echo "from: muse"
    echo "to: $FROM"
    echo "created: $(date -u +%FT%TZ)"
    case "$TYPE" in
      escalation)
        echo "status: escalated"
        ;;
      question|judgment|review-request)
        echo "status: needs-clarification"
        ;;
      *)
        echo "status: declined"
        ;;
    esac
    echo "---"
    echo ""
    case "$TYPE" in
      escalation)
        echo "Escalation received. Muse will surface this to Michael in chat."
        echo ""
        echo "Handler v1: automatic escalation routing is not yet implemented."
        echo "The request has been logged and will be reviewed."
        ;;
      question)
        echo "Question received. Handler v1 does not auto-answer questions yet."
        echo ""
        echo "Your question has been logged. Your Muse agent will review and respond,"
        echo "or route it to Michael if it needs his input."
        ;;
      judgment)
        echo "Judgment request received. Handler v1 does not auto-judge yet."
        echo ""
        echo "The request has been logged for Muse's review."
        ;;
      review-request)
        echo "Review request received. Handler v1 does not auto-review yet."
        echo ""
        echo "The artifact path has been logged. It will be reviewed manually"
        echo "or dispatch it through the pstack-interrogate playbook."
        ;;
      *)
        echo "Unknown request type: $TYPE. No action taken."
        ;;
    esac
  } > "$RESP"

  # Upload response to the box
  RB64=$(base64 -w0 "$RESP")
  $MWH "echo '$RB64' | base64 -d > '$REMOTE_RESP/${id}.response.md'" 2>/dev/null | grep -v "Warning:" || true

  # Verify
  VERIFY=$($MWH "test -f '$REMOTE_RESP/${id}.response.md' && echo yes || echo no" 2>/dev/null | grep -v "Warning:" | tail -1)
  if [ "$VERIFY" = "yes" ]; then
    log "  response written for $id"
  else
    log "  FAILED to write response for $id"
  fi
done
fi

log "=== watch run end (inbox) ==="

# ── Kanban event tail ──
# Poll task_events in kanban.db for new lifecycle events since last watermark.
# Significant events (completed, blocked, crashed, failed) are logged for
# Muse's awareness. This gives event-based visibility into the kanban board
# without webhooks or new services.

WATERMARK="$HOME/workspace/inbox/kanban-event-watermark"
# KANBAN_DB is set from BOX_HOME in the header

LAST_ID=0
[ -f "$WATERMARK" ] && LAST_ID=$(cat "$WATERMARK")

EVENTS_JSON=$($MWH "python3 -c \"
import sqlite3, json
db = sqlite3.connect('$KANBAN_DB')
db.row_factory = sqlite3.Row
rows = []
for r in db.execute('SELECT id, task_id, kind, created_at FROM task_events WHERE id > $LAST_ID ORDER BY id'):
    d = dict(r)
    # Enrich with task title
    t = db.execute('SELECT title, status, assignee FROM tasks WHERE id = ?', (d['task_id'],)).fetchone()
    if t:
        d['title'] = t['title']
        d['status'] = t['status']
        d['assignee'] = t['assignee']
    rows.append(d)
print(json.dumps(rows))
\"" 2>/dev/null | grep -v "Warning:" | tail -1)

if [ -z "$EVENTS_JSON" ] || [ "$EVENTS_JSON" = "[]" ]; then
  log "kanban: no new events (watermark=$LAST_ID)"
else
  # Process events with python for JSON safety
  echo "$EVENTS_JSON" > "$LOCAL_WORK/kanban-events.json"
  NEW_WATERMARK=$(python3 -c "
import json, sys
with open('$LOCAL_WORK/kanban-events.json') as f:
    events = json.load(f)
if not events:
    print($LAST_ID)
else:
    # Log significant events to stderr (keeps stdout clean for watermark)
    for e in events:
        kind = e.get('kind', '')
        title = e.get('title', e.get('task_id', '?'))
        if kind in ('completed', 'blocked', 'crashed', 'failed', 'gave_up'):
            print(f\"KANBAN {kind.upper()}: {title} (task {e.get('task_id')})\", file=sys.stderr)
        elif kind in ('claimed',):
            print(f\"KANBAN claimed: {title} by {e.get('assignee', '?')}\", file=sys.stderr)
    print(max(e['id'] for e in events))
" 2>> "$LOG")
  # Append significant events to the log
  python3 -c "
import json
with open('$LOCAL_WORK/kanban-events.json') as f:
    events = json.load(f)
for e in events:
    kind = e.get('kind', '')
    if kind in ('completed', 'blocked', 'crashed', 'failed', 'gave_up', 'claimed', 'created'):
        title = e.get('title', e.get('task_id', '?'))
        print(f'kanban event: {kind} — {title}')
" >> "$LOG"

  echo "$NEW_WATERMARK" > "$WATERMARK"
  log "kanban: watermark $LAST_ID → $NEW_WATERMARK"
fi

# ── Muse as kanban worker: retry failed tasks ──
# If a subagent was dispatched but never wrote a .done marker, the task failed.
# Increment consecutive_failures, re-queue for retry. After 3 failures, block it.
for brief in "$LOCAL_WORK"/muse-task-*.md; do
  [ -f "$brief" ] || continue
  tid=$(basename "$brief" .md | sed 's/muse-task-//')
  done_marker="$LOCAL_WORK/muse-task-${tid}.done"
  [ -f "$done_marker" ] && continue  # completed successfully

  # Brief exists but no .done — check age (give subagent 30 min before retry)
  brief_age=$(( $(date +%s) - $(stat -c %Y "$brief" 2>/dev/null || echo 0) ))
  if [ "$brief_age" -lt 1800 ]; then
    continue  # still within grace period
  fi

  log "kanban-worker: task $tid has no .done after ${brief_age}s — treating as failed"
  $MWH "python3 << 'PYEOF'
import sqlite3
db = sqlite3.connect('$KANBAN_DB')
row = db.execute('SELECT consecutive_failures FROM tasks WHERE id = ?', ('$tid',)).fetchone()
fails = (row[0] or 0) + 1 if row else 1
if fails >= 3:
    db.execute(\"UPDATE tasks SET status='blocked', consecutive_failures=? WHERE id = ?\", (fails, '$tid'))
    db.execute(\"INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'blocked', ?, strftime('%s','now'))\",
               ('$tid', '{\"reason\": \"3 consecutive failures, escalated to Michael\"}'))
    print(f'BLOCKED $tid after {fails} failures')
else:
    db.execute(\"UPDATE tasks SET status='todo', consecutive_failures=? WHERE id = ?\", (fails, '$tid'))
    db.execute(\"INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'created', ?, strftime('%s','now'))\",
               ('$tid', '{\"reason\": \"retry %d\"}' % fails))
    print(f'REQUEUED $tid for retry {fails}')
db.commit()
PYEOF" 2>/dev/null | grep -v "Warning:" | while read -r line; do log "kanban-worker: $line"; done

  # Remove the stale brief so it gets re-created on re-claim
  rm -f "$brief"
done

# ── Muse as kanban worker ──
# Claim tasks assigned to 'muse' and prepare them for subagent execution.
# The cron worker (not this script) spawns subagents to do the actual work.
# This section: find → claim → write brief. Max 5 claims per run.
# Tasks are claimed in priority order (urgent first).

MUSE_TASKS_JSON=$($MWH "python3 -c \"
import sqlite3, json
db = sqlite3.connect('$KANBAN_DB')
db.row_factory = sqlite3.Row
rows = []
for r in db.execute(\\\"SELECT id, title, body, status, priority FROM tasks WHERE assignee='${BRIDGE_ASSIGNEE:-muse}' AND status IN ('todo', 'ready') ORDER BY priority DESC, created_at ASC LIMIT 5\\\"):
    rows.append(dict(r))
print(json.dumps(rows))
\"" 2>/dev/null | grep -v "Warning:" | tail -1)

if [ -z "$MUSE_TASKS_JSON" ] || [ "$MUSE_TASKS_JSON" = "[]" ]; then
  log "kanban-worker: no tasks assigned to muse"
else
  echo "$MUSE_TASKS_JSON" > "$LOCAL_WORK/muse-tasks.json"
  CLAIMED_COUNT=$(python3 -c "
import json
with open('$LOCAL_WORK/muse-tasks.json') as f:
    tasks = json.load(f)
print(len(tasks))
")
  log "kanban-worker: $CLAIMED_COUNT task(s) assigned to muse, claiming..."

  # Claim each task via the Hermes CLI and write a brief
  python3 << 'PYEOF' >> "$LOG" 2>&1
import json, subprocess, os

MWH = os.environ.get("BRIDGE_SSH_CMD", os.path.expanduser("~/workspace/bin/mwh-safe.sh"))
BOX_HOME = os.environ.get("BOX_HOME", "/home/ubuntu/.hermes")
WORK_DIR = os.path.expanduser("~/workspace/inbox/work")
LOCAL_WORK_DIR = os.path.expanduser("~/workspace/inbox/work")

with open(os.path.join(LOCAL_WORK_DIR, "muse-tasks.json")) as f:
    tasks = json.load(f)

for t in tasks:
    tid = t["id"]
    title = t.get("title", "?")
    # Claim via CLI (atomic, writes proper events)
    result = subprocess.run(
        [MWH, f"HERMES_HOME={BOX_HOME} hermes kanban claim {tid}"],
        capture_output=True, text=True, timeout=30
    )
    # Check if claim succeeded (CLI prints workspace path on success)
    output = result.stdout + result.stderr
    if "Warning:" in output:
        # Filter warnings, check for actual error
        lines = [l for l in output.split("\n") if "Warning:" not in l and "HERMES_HOME fallback" not in l]
        output = "\n".join(lines)
    if result.returncode == 0 or "claimed" in output.lower():
        print(f"kanban-worker: claimed {tid} — {title}")
        # Write task brief for subagent execution
        brief_path = os.path.join(WORK_DIR, f"muse-task-{tid}.md")
        with open(brief_path, "w") as bf:
            bf.write(f"# Kanban Task: {tid}\n\n")
            bf.write(f"**Title:** {title}\n\n")
            bf.write(f"**Status:** running (claimed by muse)\n\n")
            bf.write(f"**Priority:** {t.get('priority', 'normal')}\n\n")
            bf.write(f"## Body\n\n{t.get('body', '(no body)')}\n\n")
            bf.write("## Instructions\n\n")
            bf.write("1. Do the work described above.\n")
            bf.write("2. When done, update the kanban task:\n")
            bf.write(f"   `HERMES_HOME={BOX_HOME} hermes kanban complete {tid} --result \"<summary>\"`\n")
            bf.write("3. If blocked, mark it blocked:\n")
            bf.write(f"   `HERMES_HOME={BOX_HOME} hermes kanban block {tid}`\n")
            bf.write("   and explain why in your report.\n")
            bf.write("4. Add a comment with key findings:\n")
            bf.write(f"   `HERMES_HOME={BOX_HOME} hermes kanban comment {tid} \"<comment>\"`\n")
        print(f"kanban-worker: brief written to {brief_path}")
    else:
        print(f"kanban-worker: FAILED to claim {tid} — {output[:200]}")
PYEOF
fi

log "=== watch run end ==="
