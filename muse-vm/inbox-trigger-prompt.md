# Inbox-trigger hook prompt — hermes-muse-bridge

The prompt for the `hermes-inbox-trigger` Muse hook definition (script:
`muse-vm/hermes-inbox-trigger.sh`, poll every ~60s, script timeout 600s).
The hook wakes the agent with this prompt only when the watcher cycle
produced something that needs judgment.

Placeholders to set for your deployment: `<ssh-helper>`, `<state-file>`,
`<box-hermes-home>` (Hermes home on the box, default `/home/ubuntu/.hermes`).

---

You were woken by the hermes-inbox-trigger hook (it replaced the old 1-minute inbox-watch cron). The watcher script ~/workspace/bin/inbox-watch.sh already ran this cycle: it processed the tunnel push inbox, answered new agent requests on the Hermes box, tailed kanban events, and claimed/briefed tasks assigned to the reviewer. The wake payload lists only what needs your judgment:

- payload.new_briefs: new brief files in ~/workspace/inbox/work/ for tasks claimed or briefed for jimmy. Spawn one subagent per brief (max 3 concurrent). Each subagent: read the brief; do the work; update the kanban task via your SSH helper — `HERMES_HOME=<box-hermes-home> hermes kanban complete <id> --result "<summary>"` when done, or `hermes kanban block <id>` if blocked; add a comment with key findings (`hermes kanban comment <id> "<comment>"`); write the marker ~/workspace/inbox/work/muse-task-<id>.done when finished; report back what was done.
  REVIEW briefs are different: if the brief's title line says "Kanban Task (REVIEW)", do NOT claim it and do NOT block it. Verify the work independently per the brief, then resolve exactly as the brief says: `hermes kanban complete <id> --result "<verdict + evidence>"` to approve, or `hermes kanban request-changes <id> "<exactly what must change>"`, plus a findings comment either way. The .done marker and report-back still apply.
- payload.requests: agent requests newly processed on the box (id + type); the script already wrote a handler-v1 placeholder response. Read the request at ~/workspace/inbox/work/<id>.md. For review-request / question / judgment: do the actual review or work out the answer if it is within the reviewer's remit, and write the substantive response over the placeholder on the box (<box-hermes-home>/muse-inbox/responses/<id>.response.md, uploaded via your SSH helper with the base64 pattern); surface to the user only what genuinely needs their decision. For type escalation: surface it to the user in chat with the request's content.
- payload.significant_events: kanban events with kind blocked/crashed/failed/gave_up seen this cycle. The fleet review machinery (fleet-review-trigger hook + fleet-review-sweep) owns task reviews — do not duplicate it. Surface to the user only if the event is not already covered by a review pass (check <state-file>).
- payload.flags: failure flags from the script-only timer jobs under ~/workspace/.flags/ (work-guard restarts/failures, knowledge-sync warnings). Report the flag's content to the user — for a work-guard restart, name the job and its restart count from ~/workspace/.work-guard.log — then delete the flag file once reported.
- payload.wall_url: the Tailscale SSH re-auth wall is up and remote inbox steps are paused. Surface the URL to the user so they can tap it.

Log observations to ~/memory/YYYY-MM-DD.md (daily log), never MEMORY.md. If everything in the payload turns out to be already handled on recheck, say nothing.
