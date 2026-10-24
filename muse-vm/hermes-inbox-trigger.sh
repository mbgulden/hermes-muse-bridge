#!/usr/bin/env bash
# hermes-inbox-trigger.sh — Muse hook script (install to ~/hooks/scripts/).
#
# Hook form of the old 1-minute inbox-watch LLM cron (token-burn
# modulation: replace polling crons that burn agent tokens on empty runs
# with deterministic scripts that wake an agent only when judgment is
# needed).
#
# Every poll runs the existing watcher script (which does all polling,
# answering, claiming and briefing in bash), then diffs durable state and
# wakes an agent ONLY when something needs judgment: a new jimmy task
# brief, a newly processed agent request, a significant kanban event
# (blocked/crashed/failed/gave_up), a failure flag from the script-only
# timer jobs, or the Tailscale SSH re-auth wall.
#
# Pair it with a Muse hook definition whose prompt is the inbox-trigger
# prompt (see inbox-trigger-prompt.md), polling every ~60s.
# The hook runtime ($HATCH_HOOK_RUNTIME) provides: silent, log, wake.
#
# State: ~/hooks/state/hermes-inbox-trigger.state.json
#   {briefs_woken: [...], log_offset: int, flags_seen: {name: mtime}}
# The first live poll initializes state to the current baseline (no wake).
# Dry runs never run the watcher and never advance state.
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

STATE_DIR="$HOME/hooks/state"
STATE_FILE="$STATE_DIR/hermes-inbox-trigger.state.json"
mkdir -p "$STATE_DIR"

if [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then
  "$HOME/workspace/bin/hermes-inbox-watch.sh" >/dev/null 2>&1 || true
fi

RESULT="$(python3 - "$STATE_FILE" "${HATCH_HOOK_DRY_RUN:-0}" <<'PYEOF'
import json, os, re, sys, glob

state_path = sys.argv[1]
dry = sys.argv[2] == "1"
home = os.path.expanduser("~")
work = os.path.join(home, "workspace/inbox/work")
watch_log = os.path.join(home, "workspace/inbox/watch.log")
flags_dir = os.path.join(home, "workspace/.flags")

state = None
if os.path.exists(state_path):
    try:
        with open(state_path) as f:
            state = json.load(f)
    except Exception:
        state = None

briefs_now = []
for p in sorted(glob.glob(os.path.join(work, "jimmy-task-*.md"))):
    if not os.path.exists(p[:-3] + ".done"):
        briefs_now.append(os.path.basename(p))

flags_now = {}
if os.path.isdir(flags_dir):
    for p in sorted(glob.glob(os.path.join(flags_dir, "*"))):
        if os.path.isfile(p):
            flags_now[os.path.basename(p)] = int(os.path.getmtime(p))

log_size = os.path.getsize(watch_log) if os.path.exists(watch_log) else 0

if state is None:
    new_state = {"briefs_woken": briefs_now, "log_offset": log_size,
                 "flags_seen": flags_now}
    if not dry:
        with open(state_path, "w") as f:
            json.dump(new_state, f)
    print(json.dumps({"wake": False, "payload": {"baseline": True}}))
    sys.exit(0)

woken = set(state.get("briefs_woken", []))
new_briefs = [b for b in briefs_now if b not in woken]

offset = state.get("log_offset", 0)
if log_size < offset:
    offset = 0
segment = ""
if os.path.exists(watch_log):
    with open(watch_log, "rb") as f:
        f.seek(offset)
        segment = f.read().decode("utf-8", "replace")

requests = []
cur = None
for line in segment.splitlines():
    m = re.search(r"processing (\S+)\s*$", line.strip())
    if m:
        cur = m.group(1)
    m = re.search(r"type=(\S+)", line)
    if m and cur:
        requests.append({"id": cur, "type": m.group(1)})
        cur = None

events = []
seen_ev = set()
for line in segment.splitlines():
    for pat in (r"KANBAN (?:BLOCKED|CRASHED|FAILED|GAVE_UP): .+",
                r"kanban event: (?:blocked|crashed|failed|gave_up) — .+"):
        m = re.search(pat, line)
        if m and m.group(0) not in seen_ev:
            seen_ev.add(m.group(0))
            events.append(m.group(0)[:300])

wall = None
m = re.search(r"SSH wall up.*?(https://login\.tailscale\.com/a/[A-Za-z0-9]+)", segment)
if m:
    wall = m.group(1)

flags_seen = state.get("flags_seen", {})
new_flags = []
for name, mt in flags_now.items():
    if flags_seen.get(name) != mt:
        try:
            with open(os.path.join(flags_dir, name)) as f:
                content = f.read(600)
        except Exception:
            content = "(unreadable)"
        new_flags.append({"name": name, "content": content})

payload = {"new_briefs": new_briefs, "requests": requests,
           "significant_events": events, "flags": new_flags,
           "wall_url": wall}
wake = bool(new_briefs or requests or events or new_flags or wall)

if not dry:
    new_woken = sorted({b for b in woken if b in briefs_now} | set(new_briefs))
    new_state = {"briefs_woken": new_woken, "log_offset": log_size,
                 "flags_seen": flags_now}
    with open(state_path, "w") as f:
        json.dump(new_state, f)

print(json.dumps({"wake": wake, "payload": payload}))
PYEOF
)"

WAKE="$(printf '%s' "$RESULT" | jq -r '.wake')"
PAYLOAD="$(printf '%s' "$RESULT" | jq -c '.payload')"

if [ "$WAKE" = "true" ]; then
  log "inbox items need judgment" "$PAYLOAD"
  wake "Hermes inbox has items needing Jimmy's judgment" "$PAYLOAD"
fi

silent "inbox quiet" "$PAYLOAD"
