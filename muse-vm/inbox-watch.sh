#!/bin/bash
# inbox-watch.sh — Poll the Hermes box for agent requests, kanban events, and tasks.
#
# Driven by the hermes-inbox-trigger hook (~60s poll) or run standalone.
# Does four things:
#   1. Checks the tunnel-push inbox (~/workspace/inbox/kanban-push/) for instant events
#   2. Checks <hermes_home>/<prefix>-inbox/requests/ on the box for agent requests
#   3. Polls kanban.db task_events as fallback (catches tunnel misses)
#   4. Claims tasks assigned to the configured assignees and prepares them
#      for subagents; tasks in 'review' status are briefed WITHOUT claiming
#
# This is the polling design (2026-10-19): the box cannot SSH to the Muse
# VM (no sshd available), so the VM polls the box instead.
#
# Transport modes (BRIDGE_TRANSPORT):
#   tailscale-ssh, direct-ssh: use SSH via $BRIDGE_SSH_CMD to reach the box
#   localhost: box paths are local, no SSH needed
#
# Environment (defaults = the reference deployment):
#   BRIDGE_TRANSPORT   — transport mode (default: tailscale-ssh)
#   BRIDGE_SSH_CMD     — SSH command prefix (default: ~/workspace/bin/mwh-safe.sh)
#   BRIDGE_BOX_HOME    — Hermes home on box (default: /home/ubuntu/.hermes)
#   BRIDGE_NAME_PREFIX — names the inbox dir (<prefix>-inbox) and the brief
#                        files (<prefix>-task-<id>.md) (default: muse)
#   BRIDGE_ASSIGNEE    — comma-separated kanban assignees to claim, no
#                        spaces (default: muse)
#   BRIDGE_LEGACY_PREFIX — the pre-standard naming whose briefs are still
#                        honored until they complete (default: jimmy;
#                        set empty to disable the drain)
#
# History: the deployment grew jimmy-* names after Oct 2026 while the
# standard was muse; v1.7.0 canonized the deployment side by mistake and
# v1.8.0 completes the migration to the muse standard. Briefs written
# under the legacy prefix keep working until they drain.
#
# Usage:
#   ./inbox-watch.sh [--help]

set -euo pipefail

if [ "${1:-}" = "--help" ]; then
  sed -n '2,/^set -euo/p' "$0" | sed 's/^# //; s/^#//'
  exit 0
fi

# Lockfile: overlapping cron instances used to abort each other silently at the
# SSH wall probe (observed 2026-10-21 — two runs exited 43 mid-run). flock on
# a held fd means a second instance exits quietly instead of interleaving.
LOCKFILE="$HOME/workspace/inbox/.watch.lock"
exec 9>"$LOCKFILE"
if ! flock -n 9; then
  # Another run is in progress; stay silent, cron retries next minute.
  exit 0
fi

TRANSPORT="${BRIDGE_TRANSPORT:-tailscale-ssh}"
BOX_HOME="${BRIDGE_BOX_HOME:-/home/ubuntu/.hermes}"
NAME_PREFIX="${BRIDGE_NAME_PREFIX:-muse}"
ASSIGNEES="${BRIDGE_ASSIGNEE:-muse}"
export BOX_HOME NAME_PREFIX ASSIGNEES

# SSH command: override with BRIDGE_SSH_CMD, or local exec for localhost mode
if [ "$TRANSPORT" = "localhost" ]; then
  MWH="eval"
else
  MWH="${BRIDGE_SSH_CMD:-$HOME/workspace/bin/mwh-safe.sh}"
fi

# SQL IN-list for the claim query, from the comma-separated assignees
ASSIGNEE_SQL=$(printf "'%s', " $(echo "$ASSIGNEES" | tr ',' ' ') | sed 's/, $//')

REMOTE_REQ="$BOX_HOME/${NAME_PREFIX}-inbox/requests"
REMOTE_RESP="$BOX_HOME/${NAME_PREFIX}-inbox/responses"
REMOTE_QUAR="$BOX_HOME/${NAME_PREFIX}-inbox/quarantine"
KANBAN_DB="$BOX_HOME/kanban.db"
TASK_PREFIX="${NAME_PREFIX}-task-"
export TASK_PREFIX
LEGACY_TASK_PREFIX="${BRIDGE_LEGACY_PREFIX:-jimmy}-task-"
[ "$LEGACY_TASK_PREFIX" = "$TASK_PREFIX" ] && LEGACY_TASK_PREFIX=""
export LEGACY_TASK_PREFIX
LOCAL_WORK="$HOME/workspace/inbox/work"
LOG="$HOME/workspace/inbox/watch.log"

mkdir -p "$LOCAL_WORK"

log() {
  echo "[$(date -u +%FT%TZ)] $*" >> "$LOG"
}

# Self-healing HTTP listener check
if ! curl -s -m 3 http://127.0.0.1:8899/health | grep -q '"ok"'; then
  log "listener down — restarting"
  # 9>&-: close the flock fd before spawning, so the listener can't inherit
  # the watch lock and wedge every later run (2026-10-21 incident).
  ~/workspace/bin/ensure-listener.sh 9>&-
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
  # Extract kind + title for the log line (best effort)
  summary=$(python3 -c "
import json, sys
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

# ── SSH wall probe (2026-10-20): if the Tailscale re-auth wall is up,
# mwh-safe.sh exits 42 and every remote step below would mis-parse the
# wall message as data and abort mid-run under set -o pipefail.
# Detect it once, log the current login URL, and skip remote steps this run.
# The local tunnel-push inbox (Step 1) already ran above.
# 2026-10-21: also detect non-wall transport failures (mwh-safe rc 43/44/45/46).
# The probe used to only look for the re-auth URL, so a proxy flap sailed
# through and every remote step mis-parsed the error text as data, aborting
# mid-run (observed: exit 43, garbage ids like "proxy" entering the inbox loop).
PROBE_RC=0
PROBE_OUT=$($MWH "true" 2>/dev/null) || PROBE_RC=$?
WALL_URL=$(printf '%s' "$PROBE_OUT" | grep -o 'https://login\.tailscale\.com/a/[A-Za-z0-9]*' | head -1 || true)
if [ -n "$WALL_URL" ]; then
  log "SSH wall up — skipping remote steps (inbox, kanban tail, ${NAME_PREFIX}-worker). Michael must visit: $WALL_URL"
  log "=== watch run end (wall) ==="
  exit 0
fi
if [ "$PROBE_RC" -ne 0 ]; then
  log "SSH transport down (mwh-safe rc=$PROBE_RC) — skipping remote steps (inbox, kanban tail, ${NAME_PREFIX}-worker) this run"
  log "=== watch run end (transport) ==="
  exit 0
fi

# ── Step 2: Agent inbox ──
FILES=$($MWH "ls -1 $REMOTE_REQ/*.md 2>/dev/null" 2>/dev/null | grep '\.md$' | grep -v "Warning:" || true)

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

  # Download the request (skip if the file vanished since the listing — agent may have withdrawn it)
  B64=$($MWH "base64 -w0 '$f'" 2>/dev/null | grep -v "Warning:" | tail -1 || true)
  if [ -z "$B64" ]; then
    log "skip $id (request file vanished before download)"
    continue
  fi
  echo "$B64" | base64 -d > "$LOCAL_WORK/${id}.md"

  # Dead-letter quarantine (audit Nov 9, 2026): a request with no usable
  # frontmatter (no type: or id: line anywhere) can never be answered, but
  # until now it sat in requests/ being re-scanned every cycle, silently —
  # and the Nov 8 one crashed every run for ~36h before the guards landed.
  # Move it to the box quarantine dir where it is a countable artifact.
  # Only reached for files with no response yet (the skip above ran first),
  # and the original is deleted only after the copy verifies.
  if ! grep -q -E -m 1 '^(type|id):' "$LOCAL_WORK/${id}.md"; then
    QDATE=$(date -u +%Y%m%d)
    QB64=$(base64 -w0 "$LOCAL_WORK/${id}.md")
    $MWH "mkdir -p '$REMOTE_QUAR'; echo '$QB64' | base64 -d > '$REMOTE_QUAR/${id}.quarantined-${QDATE}.md'" 2>/dev/null | grep -v "Warning:" || true
    QVERIFY=$($MWH "test -f '$REMOTE_QUAR/${id}.quarantined-${QDATE}.md' && echo yes || echo no" 2>/dev/null | grep -v "Warning:" | tail -1)
    if [ "$QVERIFY" = "yes" ]; then
      $MWH "rm -f '$REMOTE_REQ/$fname'" 2>/dev/null | grep -v "Warning:" || true
      log "quarantine: $id (no type:/id: frontmatter) — moved to quarantine/"
      rm -f "$LOCAL_WORK/${id}.md"
    else
      log "quarantine FAILED for $id — left in requests/ for the guards to skip"
    fi
    continue
  fi

  # Parse frontmatter (guarded Nov 9, 2026: an unguarded grep here killed
  # every run under set -e/pipefail when a frontmatter-less request landed
  # Nov 8 — the watcher died in Step 2 for ~36h and never reached the
  # kanban tail or the worker section. Never let a parse miss exit the run.)
  TYPE=$(grep -m1 "^type:" "$LOCAL_WORK/${id}.md" | sed 's/type:[[:space:]]*//' || true)
  FROM=$(grep -m1 "^from:" "$LOCAL_WORK/${id}.md" | sed 's/from:[[:space:]]*//' || true)
  [ -z "$TYPE" ] && TYPE="request"
  [ -z "$FROM" ] && FROM="unknown"
  SKILL=$(grep -m1 "^skill:" "$LOCAL_WORK/${id}.md" | sed 's/skill:[[:space:]]*//' || echo "")

  log "  type=$TYPE from=$FROM skill=$SKILL"

  # Build response
  RESP="$LOCAL_WORK/${id}.response.md"
  {
    echo "---"
    echo "id: $id"
    echo "from: $NAME_PREFIX"
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
        echo "Escalation received. Jimmy will surface this to Michael in chat."
        echo ""
        echo "Handler v1: automatic escalation routing is not yet implemented."
        echo "The request has been logged and will be reviewed."
        ;;
      question)
        echo "Question received. Handler v1 does not auto-answer questions yet."
        echo ""
        echo "Your question has been logged. Jimmy will review and respond,"
        echo "or route it to Michael if it needs his input."
        ;;
      judgment)
        echo "Judgment request received. Handler v1 does not auto-judge yet."
        echo ""
        echo "The request has been logged for Jimmy's review."
        ;;
      review-request)
        echo "Review request received. Handler v1 does not auto-review yet."
        echo ""
        echo "The artifact path has been logged. Jimmy will review manually"
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
# Jimmy's awareness. This gives event-based visibility into the kanban board
# without webhooks or new services.

WATERMARK="$HOME/workspace/inbox/kanban-event-watermark"
# KANBAN_DB is set in the config block above.

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

# ── Jimmy as kanban worker: retry failed tasks ──
# If a subagent was dispatched but never wrote a .done marker, the task failed.
# Increment consecutive_failures, re-queue for retry. After 3 failures, block it.
BRIEF_FILES=()
for _b in "$LOCAL_WORK"/${TASK_PREFIX}*.md; do [ -f "$_b" ] && BRIEF_FILES+=("$_b"); done
if [ -n "$LEGACY_TASK_PREFIX" ]; then
  for _b in "$LOCAL_WORK"/${LEGACY_TASK_PREFIX}*.md; do [ -f "$_b" ] && BRIEF_FILES+=("$_b"); done
fi
for brief in "${BRIEF_FILES[@]}"; do
  [ -f "$brief" ] || continue
  tid=$(basename "$brief" .md | sed -e "s/^${TASK_PREFIX}//" -e "s/^${LEGACY_TASK_PREFIX}//")
  [ -f "$LOCAL_WORK/${TASK_PREFIX}${tid}.done" ] && continue  # completed successfully
  [ -f "$LOCAL_WORK/${LEGACY_TASK_PREFIX}${tid}.done" ] && continue  # legacy brief, completed

  # Brief exists but no .done — check age (give subagent 30 min before retry)
  brief_age=$(( $(date +%s) - $(stat -c %Y "$brief" 2>/dev/null || echo 0) ))
  if [ "$brief_age" -lt 1800 ]; then
    continue  # still within grace period
  fi

  TASK_STATUS=$($MWH "python3 -c \"import sqlite3; db=sqlite3.connect('$KANBAN_DB'); r=db.execute('SELECT status FROM tasks WHERE id=?', ('$tid',)).fetchone(); print(r[0] if r else '')\"" 2>/dev/null | grep -v "Warning:" | tail -1)
  if [ "$TASK_STATUS" = "review" ]; then
    log "kanban-worker: $tid is a review task — dropping stale brief, task stays in review"
    rm -f "$brief"
    continue
  fi

  # Claim-loop guard (Nov 5, 2026): the failure ladder below is only for a
  # worker that is actually holding the task (status 'running') and went
  # silent. If the claim already expired (status ready/todo), the brief is
  # simply queued work — leave it in place and count no failure. Terminal
  # states just need the brief cleaned up.
  if [ "$TASK_STATUS" != "running" ]; then
    if [ "$TASK_STATUS" = "done" ] || [ "$TASK_STATUS" = "blocked" ] || [ "$TASK_STATUS" = "archived" ]; then
      log "kanban-worker: $tid is $TASK_STATUS — dropping brief (terminal)"
      rm -f "$brief"
    else
      log "kanban-worker: $tid claim expired (status $TASK_STATUS) — brief stays queued, no failure counted"
    fi
    continue
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

# ── The agent as kanban worker ──
# Claim tasks assigned to the configured assignees and prepare them for subagent execution.
# The cron worker (not this script) spawns subagents to do the actual work.
# This section: find → claim → write brief. Max 5 claims per run.
# Tasks are claimed in priority order (urgent first).

WORKER_TASKS_JSON=$($MWH "python3 -c \"
import sqlite3, json
db = sqlite3.connect('$KANBAN_DB')
db.row_factory = sqlite3.Row
rows = []
for r in db.execute(\\\"SELECT id, title, body, status, priority FROM tasks WHERE assignee IN ($ASSIGNEE_SQL) AND status IN ('todo', 'ready', 'review') AND NOT EXISTS (SELECT 1 FROM task_links tl JOIN tasks p ON p.id = tl.parent_id WHERE tl.child_id = tasks.id AND p.status != 'done') ORDER BY priority DESC, created_at ASC LIMIT 5\\\"):
    rows.append(dict(r))
print(json.dumps(rows))
\"" 2>/dev/null | grep -v "Warning:" | tail -1)

if [ -z "$WORKER_TASKS_JSON" ] || [ "$WORKER_TASKS_JSON" = "[]" ]; then
  log "kanban-worker: no tasks assigned to $ASSIGNEES"
else
  echo "$WORKER_TASKS_JSON" > "$LOCAL_WORK/worker-tasks.json"
  CLAIMED_COUNT=$(python3 -c "
import json
with open('$LOCAL_WORK/worker-tasks.json') as f:
    tasks = json.load(f)
print(len(tasks))
")
  log "kanban-worker: $CLAIMED_COUNT task(s) assigned to $ASSIGNEES, claiming..."

  # Claim each task via the Hermes CLI and write a brief
  python3 << 'PYEOF' >> "$LOG" 2>&1
import json, subprocess, os, shlex

MWH = os.environ.get("BRIDGE_SSH_CMD", os.path.expanduser("~/workspace/bin/mwh-safe.sh"))
BOX_HOME = os.environ.get("BOX_HOME", "/home/ubuntu/.hermes")
NAME_PREFIX = os.environ.get("NAME_PREFIX", "muse")
TASK_PREFIX = os.environ.get("TASK_PREFIX", "muse-task-")
LEGACY_TASK_PREFIX = os.environ.get("LEGACY_TASK_PREFIX", "jimmy-task-")
WORK_DIR = os.path.expanduser("~/workspace/inbox/work")
LOCAL_WORK_DIR = os.path.expanduser("~/workspace/inbox/work")

with open(os.path.join(LOCAL_WORK_DIR, "worker-tasks.json")) as f:
    tasks = json.load(f)

for t in tasks:
    tid = t["id"]
    title = t.get("title", "?")
    # Claim-loop guard (Nov 5, 2026): never re-claim a task whose brief is
    # already outstanding. The claim TTL (900s) is shorter than the time a
    # queued task may wait for pickup, so without this skip the watcher
    # re-claims and re-briefs the same tasks every ~15 minutes forever,
    # holding them in 'running' and eating dispatcher concurrency slots.
    _brief = os.path.join(WORK_DIR, f"{TASK_PREFIX}{tid}.md")
    _done = os.path.join(WORK_DIR, f"{TASK_PREFIX}{tid}.done")
    _legacy_brief = os.path.join(WORK_DIR, f"{LEGACY_TASK_PREFIX}{tid}.md")
    _legacy_done = os.path.join(WORK_DIR, f"{LEGACY_TASK_PREFIX}{tid}.done")
    if (os.path.exists(_brief) and not os.path.exists(_done)) or (os.path.exists(_legacy_brief) and not os.path.exists(_legacy_done)):
        print(f"kanban-worker: brief already outstanding for {tid} — skipping re-claim")
        continue
    if t.get("status") == "review":
        brief_path = os.path.join(WORK_DIR, f"{TASK_PREFIX}{tid}.md")
        with open(brief_path, "w") as bf:
            bf.write(f"# Kanban Task (REVIEW): {tid}\n\n")
            bf.write(f"**Title:** {title}\n\n")
            bf.write("**Status:** review (assigned to muse — do NOT claim this task)\n\n")
            bf.write(f"**Priority:** {t.get('priority', 'normal')}\n\n")
            bf.write(f"## Body\n\n{t.get('body', '(no body)')}\n\n")
            bf.write("## Instructions — this is a REVIEW. Verify independently, then resolve:\n\n")
            bf.write("1. Check the deliverables yourself against the acceptance criteria (files, live state, attachments). Do not trust the handoff summary.\n")
            bf.write("2. If it passes: approve with\n")
            bf.write(f"   `HERMES_HOME={BOX_HOME} hermes kanban complete {tid} --result \"<verdict + evidence>\"`\n")
            bf.write("3. If it does not pass: send it back with\n")
            bf.write(f"   `HERMES_HOME={BOX_HOME} hermes kanban request-changes {tid} \"<exactly what must change>\"`\n")
            bf.write("4. Either way, add a comment with your key findings:\n")
            bf.write(f"   `HERMES_HOME={BOX_HOME} hermes kanban comment {tid} \"<comment>\"`\n")
        print(f"kanban-worker: review brief written for {tid} — {title}")
        continue
    # Claim via CLI (atomic, writes proper events)
    result = subprocess.run(
        shlex.split(MWH) + [f"HERMES_HOME={BOX_HOME} hermes kanban claim {tid}"],
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
        brief_path = os.path.join(WORK_DIR, f"{TASK_PREFIX}{tid}.md")
        with open(brief_path, "w") as bf:
            bf.write(f"# Kanban Task: {tid}\n\n")
            bf.write(f"**Title:** {title}\n\n")
            bf.write(f"**Status:** running (claimed by {NAME_PREFIX})\n\n")
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
