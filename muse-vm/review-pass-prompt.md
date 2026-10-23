# Review-pass prompt — hermes-muse-bridge

The prompt below is the worker prompt for the `fleet-review-trigger` hook
(see `fleet-review-trigger.sh`). Put it in the hook definition's `prompt`
field. The scheduled review sweep (`cron-body-review-sweep.md`) runs the
same rules on a timer; the hook runs them on event arrival. Both share one
state file so a task is never double-reviewed.

Placeholders to set for your deployment:

- `<ssh-helper>` — your SSH command for reaching the Hermes box
  (e.g. `~/workspace/bin/mwh-safe.sh`)
- `<state-file>` — where the review state lives
  (e.g. `~/workspace/<goal-workspace>/hidden_files/review-sweep-state.json`)
- `<box-hermes-home>` — Hermes home on the box (default `/home/ubuntu/.hermes`)

---

You were woken because significant kanban lifecycle events just landed via the push inbox. The wake payload lists the triggering events and task_ids. Run ONE event-triggered review pass for the Hermes kanban fleet on the Hermes box, prioritizing the payload's task_ids, under exactly the same rules as the review-sweep cron:

Access: `<ssh-helper> 'remote command'` reaches the box (user/host hardcoded inside — never pass user@host). Kanban DB: `<box-hermes-home>/kanban.db` (python3 sqlite3; no sqlite3 CLI). CLI: `HERMES_HOME=<box-hermes-home> hermes kanban`. Comments need `--max-len 6000`; put full verdict text in a comment, keep `--result` short. Write scripts to a local file, base64 -w0, decode on the box, run — remote heredocs break on quoting.

State file: `<state-file>` — read it FIRST; it maps task_id -> {reviewed_status, reviewed_at, action, strikes, lineage_root}. Skip any payload task already reviewed for its current run count (the scheduled sweep shares this state; never double-review). Rewrite the state file at the end with every action you took.

For each payload task (at most 2 reviews this pass, oldest event first): run the salvage-first review (hermes-kanban-tasks skill section 5b) — deliverable file named in the task body, then task workspace, then task comments, then evidence dir, in that order — and verify claims mechanically. Then ACT:
- Deliverable complete and criteria met: post full findings as a comment, complete the task with a one-line --result.
- Incomplete or run failed (timed_out/crashed/gave_up): apply the 3-STRIKE RULE — a failed run with no proven deliverable is one strike against the lineage (successors inherit the count, tracked in the state file); failures caused by a pending user decision or missing credential are not strikes. At 3 strikes: do NOT author a successor; comment "ESCALATED TO JIMMY-DIRECT — 3 failed attempts across this lineage (name the run ids)" and record action "escalated_jimmy_direct". Under 3 strikes: author the re-scoped successor (one deliverable, smaller-tasks rules, DELIVERABLE-FIRST + REPORT-BEFORE-TIMEOUT clauses verbatim from hermes-kanban-tasks skill section 5, 1h cap for narrow proofs / 2h otherwise, same assignee). DISPATCH RULE: do NOT set a parent link from the successor to the blocked task — a todo child of a blocked-as-record parent never dispatches. Name the lineage and strikes in the task body and the state file instead.
- Superseded/duplicate: comment naming what supersedes it, then archive it.
- Needs the user's decision: comment with the exact decision needed.

HARD LIMITS: never merge anything; never delete branches, listings, or data; never modify live profile configs; never re-dispatch a blocked task unchanged; max 2 reviews per pass.

Report ONLY results: tasks closed, successors authored (with strike counts), 3-strike escalations, decisions the user needs to make. If every payload task was already reviewed by the sweep, say nothing.
