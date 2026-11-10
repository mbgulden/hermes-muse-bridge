# Watcher cron body — hermes-muse-bridge (RETIRED as a cron)

**Status: this job no longer runs as a cron.** The 1-minute inbox-watch
cron was retired in the token-burn modulation (Oct 23, 2026): at a
1-minute cadence it spent an agent run almost every minute to report
"nothing new" on most of them. The disabled cron definition is kept
only as a rollback path. Nothing below needs pasting into a cron job —
it documents what runs instead.

## What runs instead — the inbox-trigger hook

`muse-vm/hermes-inbox-trigger.sh` + `muse-vm/inbox-trigger-prompt.md`:
every ~60s a deterministic shell hook runs the watcher script
(`muse-vm/inbox-watch.sh`) and diffs durable state. It wakes an agent
only when something needs judgment:

- a new task brief from the fleet,
- a newly processed agent request,
- a significant kanban event,
- a failure flag from the systemd-timer jobs (work-guard check,
  knowledge sync), or
- the SSH re-auth wall.

Quiet minutes cost no agent run at all. The watcher script itself is
unchanged — only its driver changed, from an LLM cron to a script hook.

## The watcher's four jobs (now run inside the hook)

1. **Tunnel push inbox:** Check `~/workspace/inbox/kanban-push/*.json`
   for events pushed via the reverse SSH tunnel (instant delivery from
   the box). Process each: log with KANBAN prefix, then move to
   `~/workspace/inbox/kanban-push/.processed/`.

2. **Agent inbox:** Checks `<hermes_home>/muse-inbox/requests/` on the
   box via the configured SSH command, processes new requests by type
   (review-request, judgment, question, escalation), writes responses
   to `<hermes_home>/muse-inbox/responses/<id>.response.md`, and logs
   to `~/workspace/inbox/watch.log`.

3. **Kanban event tail (fallback):** Polls `task_events` in
   `<hermes_home>/kanban.db` for new lifecycle events since the
   watermark in `~/workspace/inbox/kanban-event-watermark`. Catches
   anything the tunnel missed. Significant events (completed, blocked,
   crashed, failed, claimed) are logged with KANBAN prefix.

4. **The assistant as kanban worker:** Queries for tasks where
   `assignee='<assignee>'`, ordered by priority (urgent first). Tasks
   in `todo`/`ready` are claimed (up to 5 per run) via
   `hermes kanban claim`; tasks in `review` status are briefed WITHOUT
   claiming (the kanban CLI cannot claim a review task — the reviewer
   resolves it directly). Tasks whose parent task is not yet `done`
   are skipped. Either way a brief lands at
   `~/workspace/inbox/work/muse-task-<id>.md`.

**Subagent dispatch:** If the watcher claimed or briefed any new tasks
(check for new `muse-task-*.md` files in `~/workspace/inbox/work/`
without a corresponding `.done` marker), spawn one subagent per task
(max 3 concurrent). Each subagent:

- Reads the task brief from `~/workspace/inbox/work/muse-task-<id>.md`
- Does the work described in the brief
- Updates the kanban task: mark `complete` with a result summary when
  done, or `blocked` if stuck
- Adds a comment with key findings
- Writes a `.done` marker file at
  `~/workspace/inbox/work/muse-task-<id>.done` when finished
- Reports back with what was done

**REVIEW briefs are different.** If the brief's title line says
"Kanban Task (REVIEW)", the task is in `review` status awaiting a
verdict — do NOT claim it and do NOT block it. Verify the work
independently (per the brief's own instructions) and resolve it
exactly as the brief says: `hermes kanban complete <id> --result
"<verdict + evidence>"` to approve, or `hermes kanban request-changes
<id> "<exactly what must change>"` to send it back, plus a findings
comment either way. The `.done` marker and report-back steps still
apply.

**Retry:** If a task brief has no `.done` marker after 30 minutes,
increment its retry count and re-queue it. After 3 failures, mark the
task `blocked` with a comment explaining the failures. Review tasks
are exempt from retry: a stale review brief is dropped and the task
stays in `review`.

Stay silent on quiet runs (no new requests, no significant kanban
events, no tasks claimed). Surface to the user: any `escalation`
request processed, any kanban event with kind `blocked`, `crashed`,
`failed`, or `gave_up`, or any task completed. For other types, the
response file on the box (or the log) is the delivery; no chat message
needed.

Log observations to `~/memory/YYYY-MM-DD.md` (daily log), never
`MEMORY.md`.
