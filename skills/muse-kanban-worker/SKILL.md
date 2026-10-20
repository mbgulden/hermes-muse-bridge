---
name: muse-kanban-worker
description: "Muse's kanban worker loop — claim tasks assigned to muse, execute via subagents, update task states. Use when working kanban tasks or setting up the Muse-as-worker integration."
---

# muse-kanban-worker

Muse participates in the Hermes kanban board as a worker, not just an observer. This skill documents the full loop.

## The Loop

```
Task assigned to muse (todo/ready)
  → inbox-watch.sh finds it (every 1 min)
  → Claims via `hermes kanban claim <id>` (atomic, max 5/run)
  → Writes brief to ~/workspace/inbox/work/muse-task-<id>.md
  → Cron worker spawns subagent (max 3 concurrent)
  → Subagent does the work
  → Subagent updates kanban: complete / block / comment
  → Writes .done marker
  → Reports back
```

## Claiming

The watcher script claims tasks via the Hermes CLI (not direct DB writes):

```bash
HERMES_HOME=/home/ubuntu/.hermes hermes kanban claim <task_id>
```

This is atomic — if two workers try to claim simultaneously, only one succeeds. The CLI also writes proper `claimed` events to `task_events` and sets up a workspace.

**Why CLI over direct DB:** The CLI handles claim locks, TTLs, workspace setup, and event logging. Direct DB writes bypass all of this and risk corrupting the claim state.

## Task States

| Status | Meaning | Who sets it |
|--------|---------|-------------|
| `todo` | Created, not yet ready | Creator |
| `ready` | Ready for a worker to claim | Dispatcher or creator |
| `running` | Claimed and being worked | `hermes kanban claim` |
| `review` | Handed off, awaiting a reviewer's verdict | Worker via `hermes kanban request-review` |
| `done` | Completed | `hermes kanban complete` |
| `blocked` | Stuck, needs input | `hermes kanban block` |
| `scheduled` | Parked for later | `hermes kanban schedule` |
| `archived` | Done and archived | `hermes kanban archive` |

## Completing a Task

```bash
# Mark done with a result summary
HERMES_HOME=/home/ubuntu/.hermes hermes kanban complete <id> --result "Did X, Y, Z"

# With structured metadata
HERMES_HOME=/home/ubuntu/.hermes hermes kanban complete <id> \
  --result "Summary" \
  --metadata '{"files_changed": ["a.py"], "tests_run": 12}'
```

## Blocking a Task

```bash
HERMES_HOME=/home/ubuntu/.hermes hermes kanban block <id>
# Then add a comment explaining why:
HERMES_HOME=/home/ubuntu/.hermes hermes kanban comment <id> "Blocked: waiting on API key from Michael"
```

Blocked tasks trigger a notification to Michael via the kanban event tail.

## Reviews (status = review)

A task handed off for review is NOT claimed — `hermes kanban claim` refuses review tasks by design. The watcher includes `review` tasks in its query and writes a **REVIEW brief** instead (title line: `Kanban Task (REVIEW)`). The reviewer verifies the work independently — files, live state, attachments, never just the handoff summary — and resolves the task directly:

- **Approve:** `HERMES_HOME=/home/ubuntu/.hermes hermes kanban complete <id> --result "<verdict + evidence>"`
- **Send back:** `HERMES_HOME=/home/ubuntu/.hermes hermes kanban request-changes <id> "<exactly what must change>"`
- Either way, add a comment with the key findings.

Review briefs are exempt from the retry loop below: a stale review brief is dropped and the task stays in `review` — it is never re-queued or auto-blocked.

**Parent gate:** the watcher skips any task whose parent is not yet `done`. The kanban DB's terminal status is `done` — gating on any other value (an earlier version used `completed`) silently skips every task that has a parent, forever.

## Commenting

```bash
HERMES_HOME=/home/ubuntu/.hermes hermes kanban comment <id> "Key finding: ..."
```

Comments are visible in `hermes kanban show <id>` and in the web UI.

## Retry on Failure

If a subagent is dispatched but never writes a `.done` marker (crash, timeout, VM restart), the watcher detects the stale brief on its next run:

1. **Grace period:** 30 minutes from brief creation — the subagent might still be working.
2. **After 30 min with no `.done`:** increment `consecutive_failures` on the task, reset status to `todo` for retry. The stale brief is deleted so a fresh one is written on re-claim.
3. **After 3 consecutive failures:** set status to `blocked`, write a `blocked` event explaining the failures. The watcher's event tail surfaces `blocked` events to you.

```sql
-- What the watcher does on stale brief (pseudocode)
UPDATE tasks SET status='todo', consecutive_failures = consecutive_failures + 1
WHERE id = '<task_id>' AND consecutive_failures < 2;

UPDATE tasks SET status='blocked', consecutive_failures = consecutive_failures + 1
WHERE id = '<task_id>' AND consecutive_failures >= 2;
```

The `consecutive_failures` counter resets when a task completes successfully (handled by the kanban CLI on `complete`). Review tasks never enter this loop — see Reviews above.

## Concurrency

- **Max 5 claims per watcher run** — prevents Muse from grabbing the entire board at once.
- **Max 3 concurrent subagents** — set in the cron body. Each subagent handles one task.
- **Priority ordering** — tasks are claimed `ORDER BY priority DESC, created_at ASC`, so urgent tasks jump the queue.
- If more than 5 tasks are assigned, the remainder are picked up on the next run.

## Assignment

Tasks are assigned to Muse by setting `assignee='muse'`:

```bash
# When creating
HERMES_HOME=/home/ubuntu/.hermes hermes kanban create "Task title" --assignee muse

# Reassigning existing
HERMES_HOME=/home/ubuntu/.hermes hermes kanban assign <id> --assignee muse
```

You, or any agent or any agent with kanban access can assign tasks to Muse.

## Files

- Watcher script: `~/workspace/bin/inbox-watch.sh` (worker section)
- Task briefs: `~/workspace/inbox/work/muse-task-<id>.md`
- Done markers: `~/workspace/inbox/work/muse-task-<id>.done`
- Cron: `hermes-inbox-watch` (every 1 min; tunnel push is primary, poll is fallback)
