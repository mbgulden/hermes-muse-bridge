# Changelog — hermes-muse-bridge

## 1.5.0 (2026-10-24)

The §5c review-sweep procedure gains the two playbook-adoption steps:

- **Playbook-load verification (§5c)** — when a reviewed task carried a `Method:` line naming a pstack playbook, the review now verifies the load actually happened: the worker profile's `state.db` `messages` rows for the run's session are checked for a `skill_view` call naming that playbook. A miss does not change the verdict, but the review comment flags "Method line present, no playbook load in run evidence" and the miss is counted for the reliability audit's load-rate metric (Method-line tasks reviewed vs loads verified).
- **Method-line backstop (§5c)** — during the dispatch pass, any `ready` task whose body matches a §2 playbook work type but carries no `Method:` line gets the Method line posted as a kanban comment (there is no body-edit command; workers read comments on claim). One backstop comment per task, never duplicated.

## 1.4.0 (2026-10-23)

Token-burn modulation: polling LLM crons became deterministic scripts that wake an agent only when something needs judgment. (Measured motive: ~2,900 scheduled agent runs/day at ~270K tokens each, most of them reporting "nothing new"; the modulation projects ~700–900 runs/day.)

- **Inbox trigger hook** — new `muse-vm/hermes-inbox-trigger.sh` + `muse-vm/inbox-trigger-prompt.md`: replaces the 1-minute inbox-watch cron. Every ~60s it runs the watcher script (unchanged — polling, answering, claiming, briefing all happen in shell), diffs durable state, and wakes an agent only on a new task brief, a newly processed agent request, a significant kanban event, a failure flag from the timer jobs, or the SSH re-auth wall.
- **Units-ensure hook** — new `muse-vm/jimmy-units-ensure.sh` + `muse-vm/units-ensure-prompt.md`: replaces the 15-minute units cron. Runs the unit installer every ~60s and stays silent while everything is installed and active. Deliberately a hook, not a systemd timer: a timer cannot reinstall itself after a VM replacement wipes /etc, so this hook is the recovery anchor for all units.
- **Tripwire hook** — new `muse-vm/branch-protection-tripwire.sh` + `muse-vm/tripwire-prompt.md`: replaces the 5-minute cron. Runs the branch-protection check every ~300s; wakes only on a trip verdict or 3+ consecutive check errors.
- **Script-only timers** — the work-guard check (15m) and the knowledge sync (1h) moved from LLM crons to systemd timers: new wrappers `muse-vm/work-guard-timer.sh` / `muse-vm/okf-sync-timer.sh` and unit templates under `muse-vm/systemd/`. Notable outcomes (a restarted job, a failed check, sync warnings) drop flag files that the inbox-trigger hook wakes an agent on; routine runs stay silent.
- **Installer covers the timers** — `install-jimmy-units.sh` now renders and installs the timer services + timers alongside the tunnel/listener, and enables the timers.
- **Review procedure moved into the skill** — the review sweep's standing procedure now lives in `skills/hermes-kanban-tasks/SKILL.md` §5c (access, state-file write discipline, verdict actions, dispatch + failure-ledger mechanics); `muse-vm/cron-body-review-sweep.md` is now a slim 30-minute safety-net body that points to §5c. The skill also gains the §2 playbook-routing Method line.

## 1.3.0 (2026-10-23)

Automatic review: work is reviewed the moment it lands, not when someone remembers to ask.

- **Event-triggered reviews** — new Muse hook (`muse-vm/fleet-review-trigger.sh` + `muse-vm/review-pass-prompt.md`): polls the push inbox every ~20s and wakes a review pass on significant lifecycle events (completed/blocked/crashed/failed/gave_up/timed_out, or status `review`), instead of waiting for the next scheduled sweep.
- **Review sweep** — new cron body template (`muse-vm/cron-body-review-sweep.md`): salvage-first reviews of the review/blocked pile with mechanical verification, plus three action triggers — the **3-strike rule** (three failed runs in a lineage escalate to the reviewer personally; no fourth retry is ever queued), **idle-fleet escalation** (ready >30m with zero runs board-wide is escalated with evidence, not logged), and a **vLLM endpoint probe** before any dispatch. Also carries cap-overrun detection and the board-change digest.
- **DISPATCH RULE** — a successor task is never parent-linked to a blocked-as-record task: a todo child of such a parent never dispatches (observed repeatedly in production). Lineage and strike counts live in the task body and the shared review state file instead.
- **Inbox canary** — new `muse-vm/fleet-canary.sh` + `muse-vm/cron-body-canary.md`: an hourly synthetic event travels the real path (box POST → tunnel → listener → push inbox) and the events daemon is checked alive; on failure the script runs the unit recovery once and retries before reporting. The canary reads the tunnel's current remote port from the port file the tunnel publishes to the box — never hardcode the port.
- **Task-authoring skill** — new `skills/hermes-kanban-tasks/SKILL.md`: the manager side of the board. One deliverable per task, 20–40 turns, binary acceptance criteria with the verification command named, DELIVERABLE-FIRST + REPORT-BEFORE-TIMEOUT clauses for every timed run, the 2h cap floor, salvage-first review order, the retry ladder, and the operational gates (WIP=1, review queue caps dispatch) — with the fleet calibration behind the numbers.
- **Watcher hardening (from production)** — `inbox-watch.sh` now takes an flock lockfile so overlapping cron instances exit quietly instead of aborting each other at the SSH probe; closes the lock fd (`9>&-`) before respawning the listener so the listener can't inherit the lock; and the SSH probe captures the transport's exit code, so a non-wall transport failure (proxy flap, timeout) skips the remote steps cleanly instead of mis-parsing error text as data mid-run.

## 1.2.0 (2026-10-20)

Watcher fixes from production use:

- **Parent-gate fix** — the claim query gated on parent status `!= 'completed'`, but the kanban DB's terminal status is `done`, so every task WITH a parent was silently skipped forever. The query now gates on `!= 'done'`.
- **Review tasks now flow** — tasks in `review` status are picked up and briefed WITHOUT claiming (`hermes kanban claim` refuses review tasks by design). The reviewer verifies the work independently and resolves the task directly: `complete` to approve, `request-changes` to send it back. Review briefs are exempt from the retry loop — a stale brief is dropped and the task stays in `review`.
- **SSH re-auth wall probe** — when the SSH transport hits an interactive re-auth wall (e.g. Tailscale SSH `check`), the watcher detects it once, logs the login URL, and skips the remote steps for that run instead of mis-parsing the wall message as data and aborting mid-run.
- **Vanished-request guard** — an inbox request file deleted between listing and download is skipped cleanly instead of aborting the run.
- Cron body and `muse-kanban-worker` skill updated for the review flow.

## 1.1.0 (2026-10-19)

Improvements over the initial 1.0.0 release:

- **Unified event daemon** — merged the separate kanban-push and Telegram-notify daemons into a single `kanban-events-daemon.py`. One DB poll, three outputs per event (JSON file, tunnel POST, Telegram alert).
- **Priority-ordered task claiming** — the watcher now claims tasks `ORDER BY priority DESC, created_at ASC` instead of creation order alone.
- **Retry on failure** — tasks that fail without completing are re-queued up to 3 times, then marked `blocked` with an explanatory comment and a Telegram alert.
- **Detailed health endpoint** — `GET /health/detailed` on the listener reports tunnel status, daemon status, last event timestamp, and uptime.
- **Tunnel + poll hybrid transport** — reverse SSH tunnel for instant push, 1-minute poll as fallback. No event loss if the tunnel drops.
- **verify.sh** — automated post-install verification (tunnel, daemon, listener, config, skills).
- **uninstall.sh** — clean removal of all components.
- **install.sh honesty fix** — no longer claims to create Muse crons from shell. Prints the exact instructions to paste to your Muse agent, with the cron body in `muse-vm/cron-body.md`.

## 1.0.0 (2026-10-19)

Initial public release:

- Bidirectional Hermes ↔ Muse.ai bridge
- Kanban task integration (assign to `muse`, auto-claim, work, update)
- Event push via reverse SSH tunnel + file-drop fallback
- Telegram notifications on task events
- `call-muse` skill for Hermes profiles
- `muse-kanban-worker` skill for Muse agents
- Interactive `install.sh` with transport modes (tailscale-ssh, direct-ssh, localhost)
