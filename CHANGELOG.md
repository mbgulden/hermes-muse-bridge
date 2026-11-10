# Changelog — hermes-muse-bridge

## 1.8.1 (2026-11-09)

Bridge audit fixes (audit 2026-11-09; Michael's go with the premigration
backup held to Nov 16). No behavior change to the event, claim, or steer
paths — this release removes the silence around them.

- **Dead-letter quarantine** — `muse-vm/inbox-watch.sh`: a request with no
  usable frontmatter (no `type:` or `id:` line) is moved to the box
  `muse-inbox/quarantine/` directory, verified there before the original
  is deleted, and logged. The Nov 8 frontmatter-less file crashed every
  watcher run for ~36h before the guards landed; a bad request is now a
  countable artifact instead of a landmine.
- **Housekeeping** — new `muse-vm/bridge-maint.sh`: prunes the event
  spool to 48h on both machines (box `muse-inbox/kanban-events/`, VM
  `kanban-push/.processed/`) and rotates bridge logs past 5 MB keeping 3
  generations. First run: VM spool 94,945 → 17,907 files, box spool
  97,817 → 18,034; the 43 MB watch.log rotated.
- **Request-path canary** — new `muse-vm/fleet-request-canary.sh`:
  synthetic request round-trip through the watcher (target <300s, FAIL
  at 900s; first live run 113s), a watcher-silence check (no completed
  run in >10 min fails), and a spool/disk guard (>1 GB total spool or
  >92% box disk fails). The hourly canary cron now runs all three
  scripts — maint, event canary, request canary (template updated).
- **Cleanup:** the stale pre-migration staging tree and the .bak scatter
  in both live bin dirs are deleted; the live tree and this repo are the
  only two copies. The premigration inbox backup is held until Nov 16,
  then deleted (tracked in the muse-direct queue); the kanban-push.sh
  fallback retirement clock started Nov 9 — two clean listener-only
  weeks, earliest ~Nov 23.

## 1.8.0 (2026-11-09)

The muse-standard migration. Michael's standing standard — set when the
repo was created (2026-10-19) and restored by his call on 2026-11-09 — is
that everything in the bridge carries the `muse` name. v1.7.0 had
canonized the deployment's drifted `jimmy` naming; this release reverses
that and the live deployment migrated to match, the same evening.

- **Defaults flipped:** `BRIDGE_NAME_PREFIX` defaults to `muse` and
  `BRIDGE_ASSIGNEE` to `muse`. Inbox `muse-inbox`, briefs `muse-task-*`,
  responses written `from: muse`.
- **Renamed:** systemd units `muse-listener` / `muse-tunnel` (+ their
  templates), `install-muse-units.sh`, the `muse-units-ensure` hook
  script, the box events daemon (writes `muse-inbox`, reads
  `muse-listener-port`), the listener heartbeat file, and the fleet
  skill — `call-muse` in every profile, matching this repo.
- **Legacy drain:** briefs written under the old prefix keep working
  until they complete — `BRIDGE_LEGACY_PREFIX` (default `jimmy`) feeds
  the claim guard and the retry loop, and the wake hook globs both
  prefixes. On the reference deployment the old inbox path is a symlink
  into `muse-inbox` for the transition. The drain code comes out in
  v1.9 once no legacy briefs remain.
- **Live cutover verified end to end:** inbox moved with zero lost
  files, daemon restarted on the new code (event flow confirmed), units
  cut over with work-guard re-registered under the new names, fleet
  canary passing on the new path.

## 1.7.0 (2026-11-09)

The drift reconcile: the repository now matches the deployment it describes.

- **Watcher defaults now equal the reference deployment** (`muse-vm/inbox-watch.sh`) — the watcher was rebuilt from the live script, so its run logic is the deployed logic by construction: lockfile, exit-code-aware transport probe, and the Nov 9 frontmatter guard included. Defaults: the inbox at `<hermes_home>/jimmy-inbox`, briefs named `jimmy-task-<id>.md`, claim set `jimmy,muse`. The v1.6.x naming stays restorable with `BRIDGE_NAME_PREFIX=muse` and `BRIDGE_ASSIGNEE=muse`.
- **New knob `BRIDGE_NAME_PREFIX`** derives the inbox directory and the brief filenames; `BRIDGE_ASSIGNEE` accepts a comma-separated list.
- **Claim subprocess fix** — a multi-word `BRIDGE_SSH_CMD` is now split correctly; previously only single-path commands worked.
- **Docs and config aligned** — ARCHITECTURE.md, the cron-body mechanism note, the inbox-trigger prompt, both skills, the example config, and install.sh name the same paths the watcher uses. The listener heartbeat file is `.work-heartbeat-jimmy-inbox-listener`, as deployed. The `call-muse` box skill documents its reference-deployment name (`call-jimmy`); the skill itself is not renamed.
- **Verified by fixture** (localhost transport, stub kanban CLI): typed and frontmatter-less requests answered, a task claimed, a `jimmy-task-*` brief written, the run ends clean. The v1.6.2 watcher against the same fixture dies at the frontmatter-less request — the Nov 8 freeze, reproduced. With the knobs set to the old values, the new watcher reproduces the v1.6.x layout exactly.

## 1.6.3 (2026-11-09)

Watcher hotfix from production use:

- **A frontmatter-less inbox request can no longer kill the watcher** (`muse-vm/inbox-watch.sh`) — the inbox step parsed each request's `type:`/`from:` frontmatter with an unguarded `grep` inside a command substitution. Under `set -euo pipefail`, a request file with no frontmatter (a free-form markdown note) made the grep exit non-zero and ended every watcher run at that file — silently, before the kanban event tail and the task-claim section ever ran. One such request froze task claiming for ~36 hours (Nov 8–9, 2026); the only log trace was the same `processing <id>` line each run, with no `watch run end` after it. The parse is now guarded (`|| true`) with defaults (`type=request`, `from=unknown`), so a parse miss degrades to the generic handler instead of ending the run. TROUBLESHOOTING.md gains the failure mode, its checks, and the immediate unblock for older versions.

## 1.6.2 (2026-11-04)

The Nov 4, 2026 kanban rules, synced from the live skill (`skills/hermes-kanban-tasks/SKILL.md` only — no new files, no component changes):

- **§3c iterative work (skill)** — a task whose deliverable is built iteratively (renders, ports, migrations, generated artifacts, reconstructions) now carries four extra named items in its body, next to the acceptance criteria: the frozen spec, the fixture/coverage set, the standing category checks, and the version-metrics file — and points the worker at `pstack-loop-discipline` as the method. A body missing any of the four is under-specified.
- **§2 audit-type routing (skill)** — audit and verification-type tasks (corpus/data verification, compute audits, check-suite builds, independent verifications) are assigned to `muse` (reviewer-direct) every time, never to the fleet profiles.
- **§5c ready-starvation + TWO-FULL-CAP SHAPE RULE (skill)** — a task in `ready` for >2h with no claim is a stall the sweep ACTS on in-pass (audit tasks reassigned to `muse`; build tasks reassigned to a profile that can start them, or commented with the named blocker). And when a lineage's last 2 runs each burned ≥80% of their runtime cap with no proven deliverable, no same-shape successor at any strike count — the successor must be split smaller or routed to `muse`.

## 1.6.1 (2026-10-26)

The sharing-polish pass: plugin copies current again, cron templates
match the post-modulation reality, and a map for first-time visitors.

- **agent-checkpoint plugin copy → v0.3.0** (`plugins/agent-checkpoint/`) — the published build: uncapped-run interval checkpoints, the `config_schema` manifest (19 keys), corrected author metadata; now ships complete with `LICENSE` and `README.md`, plus the `test_uncapped.py` suite file (67 tests against the shipped file).
- **kanban-stream plugin copy → v1.0.3** (`plugins/kanban-stream/`) — the current release: the stream line reports total output tokens (`9,412 out tok`) instead of a misleading tokens/sec figure. The copy is now the full release tree: `bin/steer.py` (the steering companion daemon), `tests/smoke_test.py`, `LICENSE`, `README.md`, `.env.example`.
- **Cron templates match the live mechanisms** — `muse-vm/cron-body.md`: the 1-minute inbox-watch cron was retired in the Oct 23 token-burn modulation; the file is now a mechanism note describing the `hermes-inbox-trigger` hook that replaced it (the watcher script is unchanged — only its driver changed). `muse-vm/cron-body-review-sweep.md`: refreshed to the current live 30-minute sweep body. `muse-vm/cron-body-canary.md` verified current against the live hourly job, unchanged.
- **README "Start here" section** — which of the four public repos (this bridge, muse-pstack, agent-checkpoint, hermes-kanban-stream) to pick for what, with links.

## 1.6.0 (2026-10-26)

The kanban optimizations since 1.5.0, synced together with the fleet reliability audit that measures them (new `docs/reliability-audit-2026-10-26.md`). The audit's honest read: crashes went 44 → 15 → 0 and the checkpoint trail now exists on every v0.2.0-era failing run — but fleet-only run completion is flat (34.8% → 34.1%) and the 1h chain pieces completed 0 of 23 runs in the AFTER window. The machinery is published with its numbers, not ahead of them.

- **Sizing standard rewrite (skill, "What's too big")** — human-equivalent time is now the primary size measure: author pieces a competent human could finish in 10–20 minutes, hard-split anything over ~30; agent turns are demoted to a backstop and the authoring test changed to match. The calibration bullet is updated, and the retry ladder gains an **advisor consult at the second failure** (a stronger-model reviewer consult over the worker's artifacts before any third run).
- **Generation tasks chain by artifact (skill)** — a multi-artifact generation task is never authored as one shot: chained pieces, one artifact each, with build and proof as separate pieces (A1 build-only → A2 proof-only → B docs/map → C drive + assemble + commit), 1h caps, gated mechanically by the new checker below.
- **Evidence update (skill)** — verify every ~10 dependent steps; criteria are pre-registered and not worker-editable, and code tasks carry a success command plus a regression (pass-to-pass) command the reviewer re-runs; chains stay sequential (≤~6 verified pieces per parent goal); checkpoints belong at verified boundaries; every task body carries a human-equivalent estimate, logged against actuals.
- **§2 routing additions (skill)** — **restate-first**: investigation, bug-fix, and report-driven tasks open with an in-your-own-words restatement posted as the worker's first comment. **Verify-first**: test/audit/reproduce/review tasks run the project's proven verify skill and its doctor first; a missing verify skill is stated plainly in the body and surfaces as a finding.
- **§5c compliance pack (skill)** — once per sweep day, the review sweep runs the fleet-compliance checker and names new silent exits in the run report; the weekly count feeds the reliability audit.
- **check-task-size.py (new, beside the skill)** — the mechanical gate behind the sizing standard: multi-artifact bundles, build+proof bundles, missing or oversized human-equivalent estimates, acceptance-criteria counts, missing regression commands. Run on every task body before creation; a FAIL means decompose first.
- **agent-checkpoint plugin v0.2.0 (new, `plugins/agent-checkpoint/`)** — mid-run budget persistence: beyond the `pre_llm_call` reminders, the plugin evaluates at tool-call boundaries and via a per-session watchdog thread, and persists the checkpoint itself (checkpoint file + kanban comment) at its 50% / 80% budget tiers, so single-turn runs cannot cross their budget silently. Ships with its test suite (`tests/` + the test-only `agent_checkpoint` import shim; 60 tests passing against the shipped file).
- **Checkers (new, `muse-vm/checkers/`)** — the prose-to-checkers stack: `check-copy`, `check-leaks`, `check-okf-sync`, `check-port`, `check-pr-evidence`, `check-uncommitted`, `fleet-compliance`, `grade-verify-cli`, plus the task-size specimen set (`tests-task-size/`) and example operator-data files (`copy-allowlist.example.txt`, `repo-registry.example.tsv` — format examples only; the operator's real files are not published).

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
