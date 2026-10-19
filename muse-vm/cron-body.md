# Watcher cron body — hermes-muse-bridge

Copy the task body below into a Muse cron job that runs every 1 minute.
The installer prints this path at the end of setup.

---

Poll the Hermes box for agent requests, kanban board events, and tasks assigned to Muse.

Run `~/workspace/bin/inbox-watch.sh` (installed by `./install.sh`). It does four things:

1. **Tunnel push inbox:** Check `~/workspace/inbox/kanban-push/*.json` for events pushed via the reverse SSH tunnel (instant delivery from the box). Process each: log with KANBAN prefix, then move to `~/workspace/inbox/kanban-push/.processed/`.

2. **Agent inbox:** Checks `<hermes_home>/muse-inbox/requests/` on the box via the configured SSH command, processes new requests by type (review-request, judgment, question, escalation), writes responses to `<hermes_home>/muse-inbox/responses/<id>.response.md`, and logs to `~/workspace/inbox/watch.log`.

3. **Kanban event tail (fallback):** Polls `task_events` in `<hermes_home>/kanban.db` for new lifecycle events since the watermark in `~/workspace/inbox/kanban-event-watermark`. Catches anything the tunnel missed. Significant events (completed, blocked, crashed, failed, claimed) are logged with KANBAN prefix.

4. **Muse as kanban worker:** Queries for tasks where `assignee='<assignee>'` AND `status IN ('todo','ready')`, ordered by priority (urgent first). Claims up to 5 per run, writes a task brief to `~/workspace/inbox/work/muse-task-<id>.md`.

**Subagent dispatch:** If the watcher script claimed any new tasks this run (check for new `muse-task-*.md` files in `~/workspace/inbox/work/` without a corresponding `.done` marker), spawn one subagent per task (max 3 concurrent). Each subagent:
- Read the task brief from `~/workspace/inbox/work/muse-task-<id>.md`
- Do the work described in the brief
- Update the kanban task: mark `complete` with a result summary when done, or `blocked` if stuck
- Add a comment with key findings
- Write a `.done` marker file at `~/workspace/inbox/work/muse-task-<id>.done` when finished
- Report back with what was done

**Retry:** If a task brief has no `.done` marker after 30 minutes, increment its retry count and re-queue it. After 3 failures, mark the task `blocked` with a comment explaining the failures.

Stay silent on quiet runs (no new requests, no significant kanban events, no tasks claimed). Surface to the user: any `escalation` request processed, any kanban event with kind `blocked`, `crashed`, `failed`, or `gave_up`, or any task completed. For other types, the response file on the box (or the log) is the delivery; no chat message needed.

Log observations to `~/memory/YYYY-MM-DD.md` (daily log), never `MEMORY.md`.
