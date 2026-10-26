---
name: hermes-kanban-tasks
description: "The manager side of Hermes kanban — how to write, scope, and review tasks that get good results from one-shot agent workers. Use when creating or managing kanban tasks for Hermes worker profiles, or reviewing their work."
---

# hermes-kanban-tasks

This is the **task authoring** skill — how to create tasks that one-shot workers complete well. For the worker side (claiming and executing tasks), see the worker skill (`pstack-kanban-worker` in muse-pstack, or `muse-kanban-worker` in this repo).

Kanban workers get **one prompt and no mid-task steering**. They run `work kanban task <id>` and go until done or blocked. Everything they need must be in the task. If they need a decision you didn't pre-make, they block and wait.

## The Golden Rule

**Write the task so a smart stranger with no context could complete it in one sitting.** The worker does not share your conversation history, your plans, or your assumptions. The task body is its entire world.

## Task Anatomy

Every task needs all five parts:

### 1. Title — the next concrete action

Not a topic, an action. Verb-first, specific.

```
Good: "Draft pricing page copy in positive voice"
Good: "Research 10 local IT-disposal competitors and price their pickup fees"
Bad:  "Pricing page"
Bad:  "Look into competitors"
```

### 2. Body — context the worker needs

The worker knows nothing about your project. Include:
- **Background**: what this is for, in 2-3 sentences
- **Constraints**: voice rules, technical limits, things to avoid
- **References**: links, file paths, or prior task IDs they should read first
- **Playbook routing (Oct 23, 2026)**: when the work type matches a pstack playbook deployed on the fleet, name it in the body as a Method line — measurement (Oct 23) showed the passive SOUL.md mention produced ~0 loads in 289 sessions; the task body is the routing surface that works. Map: defect fix → `pstack-bug-fix`; performance problem → `pstack-perf-issue`; review / investigation / verdict → `pstack-interrogate`; contested design choice → `pstack-arena` (generalist profile only); fan-out research → `pstack-swarm` (generalist profile only). Write it as: "Method: work this under the pstack-bug-fix playbook — load that skill first." One playbook per task; when none matches, no line.
- **Restate-first routing (Oct 24, 2026)**: for investigation, bug-fix, and report-driven tasks (a forwarded message, a user report, an ambiguous failure), the body MUST require the worker to load `pstack-in-your-own-words` and post its restatement of the problem — goal, underlying issue, what done looks like, no hypotheses smuggled in — as its first comment before any other work. The reviewer reads the restatement at review; a wrong restatement is a finding about the task, not just the worker.
- **Verify-first routing (Oct 24, 2026)**: for any task that tests, audits, reproduces, or reviews a project's behavior, check the verify-skill registry before writing the body. If the project has a proven verify skill, the body MUST name it and its doctor command, and require: doctor runs first (a red doctor is the first finding), driving happens through the project's control CLI, and the report quotes the doctor output + evidence paths. If the project has no verify skill yet, the body states that plainly and the gap is a finding in the report — never silently improvise a one-off harness when a rollout task for that project already exists in the rollout manifest. Full standard: the verify-rollout handbook.

```bash
HERMES_HOME=/home/ubuntu/.hermes hermes kanban create "Draft pricing page copy" \
  --assignee builder \
  --priority 5 \
  --body "Background: Acme ITAD is an IT asset disposal business in Boise, ID. We need a pricing page. (Worked example — substitute your own business and decisions.)

Constraints:
- Positive voice only: state what things ARE, active voice, no 'not'/'no'/'never'/'without'
- Real numbers only, no invented prices or certifications
- Pricing decisions (from the owner): free pickup for 10+ computers or any server/networking load; \$75 flat for smaller loads (waived with buyback value); CRTs \$25 each; local area free with qualifying load, \$50 trip fee beyond; loose lithium batteries declined

Reference: read the FAQ page at /home/ubuntu/.hermes/kanban/workspaces/t_xxx/faq.md for tone.

ACCEPTANCE CRITERIA:
- Markdown file covering all pricing decisions above
- Every price stated as a positive ('Free pickup for 10+ units', not 'No charge for...')
- Zero banned words: not, no, never, without, don't
- File saved to the task workspace as pricing.md"
```

### 3. Acceptance Criteria — REQUIRED

**Every task must have acceptance criteria.** This is the test for "done." Without them, the worker decides what done means, and you'll disagree. (Made default Oct 19, 2026.)

Write them as checkable statements, not aspirations:

```
Good:
- File exists at <path> with all 15 FAQ questions answered
- Each answer is 2-4 sentences in positive voice
- Zero instances of banned words (grep-verified)

Bad:
- "Good quality copy"
- "Comprehensive research"
- "Looks professional"
```

Put acceptance criteria **in the task body** (the worker reads the body; there's no separate AC field). Mark them clearly with an `ACCEPTANCE CRITERIA:` header so they're unmissable.

### 4. Deliverable location

Say exactly where the output goes — a file path, a URL, a repo commit:

```
- Save to the task workspace as pricing.md
- Deploy to example.com/pricing via GitHub
- Post result as a comment on this task
```

If you don't say where, the worker improvises and you hunt for it later.

### 5. Timed runs — deliverable-first standard + report-before-timeout (HARD RULES)

Two layers. The primary defense is the SHAPE of the work: a run loses its
findings only when they live in the worker's context until the end. The report
deadline is the backstop, not the main mechanism. (Origin: a diagnosis task was reaped at the 2h cap with nothing written
down. Same-week evidence: a checkout task died twice with zero output, then
completed on the attempt told to write the skeleton first; another task's
PR-body work survived a dead run untouched because it lived on GitHub, not
in the worker's context.)

Every timed task body MUST carry both clauses, numbers filled in:

```
DELIVERABLE-FIRST (hard rule): Your FIRST action is to create the deliverable
file at <PATH> with its full skeleton — every section heading plus what is
already known — and save it. Then write each finding into the file as you
find it. The file is the report at every moment; if this run dies, the file
is what survives. Never hold findings in your head until the end.

REPORT-BEFORE-TIMEOUT (hard rule): This run is capped at <CAP>. Post a brief
progress comment at the halfway point (<H> into this attempt). By 80% of the
cap (<X> into this attempt), the deliverable file must be in its final state
for this attempt AND a summary comment posted here — whether you are
finished or not. If not finished, the file's status line says INCOMPLETE and
states exactly what the next attempt must do first. Use the remaining time
only to extend the file. A run that hits the cap with no report on file is a
failed run, no matter how much work it did.
```

Authoring notes:
- Compute the halfway and 80% points in absolute terms the worker can use
  ("progress comment by 1h, final report by 1h36m into this attempt" for a
  2h cap; "by 2h / by 3h12m" for a 4h cap).
- The deliverable must live at a DURABLE path (`/home/ubuntu/work/...`), never
  only in the task workspace — scratch workspaces are cleaned.
- **Size tasks to their budgets.** The worker turn budget is `agent.max_turns`
  in the worker's profile (90 in our fleet): author tasks that fit in ~2/3 of
  it (~60 steps).
  If the honest time estimate exceeds ~60% of the cap you would set, the task
  is too big — split it into chained tasks and let the board be the checkpoint.
- **Cap floor: nothing substantive under 2h (HARD RULE).**
  Every substantive task gets an explicit `--max-runtime`: 2h minimum, 4h for
  diagnoses and long builds. Measured on the board (Oct 19–21, 213 runs): runs
  capped at ≤30 min timed out 71–80% of the time; runs capped ≥1h timed out
  10%. Median honest run is ~30 min and p90 is ~2h — a small cap doesn't make
  work faster, it just kills it at its median. Measure your own fleet before
  tuning these numbers; the shape of the result is what generalizes.
- When re-queueing or extending a timed task, restate both clauses with the
  new cap's numbers in a comment; the worker reads comments on claim.

### 5b. Lessons from last-mile deaths (runs that burn the full budget and file nothing)

Four runs in two days burned their full budgets and filed
nothing or skeletons — despite carrying both §5 clauses. The clauses are
necessary but not sufficient. Three additions, now standing practice:

- **Proof tasks split by evidence artifact.** An integration proof whose
  deliverable is a handful of evidence files gets split into one task per
  artifact (baseline/no-fire, firing, report assembly), each sized to finish
  in ~30–45 min. For this narrow class the cap may be 1h despite the §5 floor:
  the floor guards substantive work from starvation, and a proof piece sized
  to 2/3 of a 1h cap satisfies the sizing rule. (Origin: a 2h single-proof task
  repeated the monolith failure — baseline evidence landed, the firing proof
  never got filed.) If the user overrides this exception, it dies.
- **Salvage-first review (the reviewer's step, before any run is written off).**
  A dead run's product often exists outside the deliverable: the task's
  scratch workspace (uncommitted implementation — one dead run's complete fix
  was salvaged from its workspace and shipped as a PR), its progress comments
  (another run's findings lived only in a comment; salvaged into the report), the
  evidence dir. Review order: deliverable → workspace → comments → evidence.
  Declare "nothing filed" only after all four are checked, and say which.
- **Intervention ladder (reviewer-side, until a checkpoint plugin is live on
  the worker profiles).** Detection without intervention failed three times —
  the stalls were flagged and the runs died anyway. A stall-nudge cron (30m)
  posts the nudge itself: at ≥50% of cap with no worker comment on the run, a
  halfway nudge; at ≥80% with no summary comment and a stale deliverable, the
  final call (file what you have, INCOMPLETE + next step). One nudge per run
  per threshold, deduped in its state file. When a checkpoint plugin is
  enabled on all worker profiles, retire this cron — the plugin owns the job.

### 5c. The automatic review sweep — standing procedure

The review-sweep cron (30m safety net) and the review-trigger hook
(event path) both execute this procedure; their bodies/prompts carry
only the trigger and reporting rules and point here for the standing detail.

**Access.** `<ssh-helper> 'remote command'` reaches the Hermes box
(user/host hardcoded inside — never pass user@host). Kanban
DB: `<box-hermes-home>/kanban.db` (python3 sqlite3; no sqlite3 CLI).
CLI: `HERMES_HOME=<box-hermes-home> hermes kanban`. Comments need
`--max-len 6000`; put full verdict text in a comment, keep `--result` short
(result text has been mangled before). Remote heredocs/inline python break
on quoting: write scripts to a local file, base64 -w0, decode on the box,
run. GitHub is REST API only — use your GitHub API credential pattern.

**State file.** `<state-file>`
maps task_id → {reviewed_status, reviewed_at, action, strikes,
lineage_root} and holds a `board_snapshot` (task status counts, per-task
statuses, open PRs for your repos, engine main head). Read it first;
rewrite it at the end of every run with
the fresh snapshot. STATE-FILE WRITE DISCIPLINE (learned the hard way,
after a concurrent in-place rewrite dropped ~39 entries): never write it in
place — copy to a `.bak-<YYYYMMDD-HHMM>` sibling first, write the new state
to a temp file in the same directory, rename over the original, read back
and verify the key count did not shrink without an explicit reason.

**Finding review work.** (a) tasks in `review`; (b) tasks in `blocked`
whose latest run ended timed_out/crashed/gave_up with no review
recorded for their current run count; (c) tasks the reviewer blocked under the
retry-ladder gate with no successor yet. Review at most 2 per run, oldest
first, salvage-first per §5b (deliverable → workspace → comments →
evidence), verified mechanically.

**Acting on the verdict.**
- Complete and criteria met: full findings as a comment, then complete
  with a one-line `--result`.
- Incomplete, or the run failed on process/timing: apply the 3-STRIKE RULE
  before authoring anything. A failed run with no proven deliverable is one
  strike against the lineage; successors inherit the count (record
  lineage_root + strikes in the state file for every task touched).
  Failures caused by a pending user decision or a missing credential are
  NOT strikes — route those to the decision list. At 3 strikes: no fourth
  successor; comment "ESCALATED TO REVIEWER-DIRECT — 3 failed attempts across
  this lineage (name the run ids). No fourth retry will be queued; the reviewer
  executes this personally." and record action `escalated_reviewer_direct`.
  Under 3: author the successor under the smaller-tasks rules — one
  deliverable per task, 1h cap for narrow proofs (2h otherwise), the
  DELIVERABLE-FIRST + REPORT-BEFORE-TIMEOUT clauses from §5 verbatim,
  binary acceptance criteria, assignee = the original worker. Playbook
  routing per §2: add the Method line when the work type matches (defect →
  pstack-bug-fix; performance → pstack-perf-issue; review/investigation →
  pstack-interrogate). Comment on the blocked task naming the successor
  id(s), the strike count, and the named material change; leave the blocked
  task blocked as the record. DISPATCH RULE: never set a parent link from
  the successor to the blocked task — a todo child of a blocked-as-record
  parent never dispatches (observed repeatedly in production); the
  lineage record lives in the task body and the state file.
- Superseded/duplicate: comment naming what supersedes it, then archive.
- Needs the user's decision: comment with the exact decision needed, leave
  it, and surface it in the run report.

**Playbook-load verification.** When a reviewed task's body carried a `Method:`
line naming a pstack playbook, verify the load actually happened — never
assume it. Evidence: the worker profile's `state.db` `messages` table,
rows for the run's session (session id in `task_runs.metadata`) whose
`tool_calls` contain a `skill_view` call naming that `pstack-<name>`.
Load found → note it in the review comment. No load → the verdict still
stands on the deliverable's merits, but the review comment flags
"Method line present, no playbook load in run evidence" and the miss is
counted for the load-rate metric in the fleet reliability audit
(Method-line tasks reviewed vs loads verified).

**Dispatch check, endpoint probe first.** From the box, probe each
worker group's model endpoint:
`curl -s -m 5 http://<vllm-endpoint>/v1/models` (one endpoint per worker
group). Endpoint
down → do not dispatch for its assignees; report the escalation with the
probe result. Endpoint up + a task in `ready` >15m with no running run =
dispatch failure: one `hermes kanban dispatch` pass, then verify a worker
PID alive with a fresh heartbeat. IDLE-FLEET ESCALATION: after the pass, a
task in `ready` >30m AND zero runs running board-wide = escalate in the run
report as a reviewer action item with the evidence (ready ids + ages, dispatch
output, running count) — never a log-only note.

**Method-line backstop.** During the same dispatch pass,
check every `ready` task: if its body matches a playbook work type (defect
fix, performance, review/investigation, contested design, fan-out
research — the §2 map) but carries no `Method:` line, post a kanban
comment on it carrying the Method line for the matching playbook. There
is no body-edit command; workers read comments on claim, so the comment
is the routing surface. One backstop comment per task — check the task's
comments first and never duplicate it.

**Cap-overrun check (detection only).** For every running run, compare
elapsed against the task's max_runtime_seconds. A run past cap whose
deliverable has no final report is a failed run — name it in the run
report; its review happens under the blocked-task step once it lands.

**Compliance pack.** Once per sweep day (not every run),
run `python3 fleet-compliance.py --days 1` on the box
(source: `muse-vm/checkers/fleet-compliance.py` in this repo). It flags SILENT EXITs
(finished runs with no close-out acknowledgment — calibrated definition:
no comment at/after run end by any author and no result on the task; the
silent failure shape), restate-first misses, and deliverable-first
timing. Name new silent exits in the run report; the weekly count goes
to the fleet-reliability audit. Calibrated baseline (Oct 25): 5 silent
exits in 126 finished runs over 3 days. Checkers flag, the reviewer judges —
a flag is a prompt to verify, never a verdict by itself.

**Board-change digest.** Diff the fresh snapshot against the stored one —
status transitions, board counts, new open PRs (number, title, branch) on
either repo, engine main head changes. Real transitions fold into the run
report; a digest with no transitions and no actions is a silent run.

**Failure ledger.** `<failure-ledger>`
— read its header for the recording rule and its failure-mode classes before editing.
A review that brings a lineage to 2 strikes opens an entry (lineage + task
ids; run ids, outcomes, caps; failure-mode class; root cause if the
evidence names it, otherwise "unknown" plus the probe that would find it;
the corrective change made). When a lineage with an open entry resolves —
successor completes, escalation closes, the user kills it — close the entry
with root cause, corrective change, outcome. A strike requires no proven
deliverable: check first whether the run actually finished (a reaping
artifact is F6, not a strike). Keep the ledger's counts section current.
Ledger edits ride along with the review; they are not a separate report.

**Hard limits.** Never merge anything (PR reviews are verdicts posted as
comments only, and only when a task asks for one); never delete branches,
listings, or data; never modify live profile configs; never re-dispatch a
blocked task unchanged; never author a successor that would be a lineage's
4th attempt; max 2 reviews per run.

**Bookkeeping.** Append a short entry to `~/memory/YYYY-MM-DD.md` (daily
log). Never edit MEMORY.md.

### 6. Priority and assignee

```bash
--priority 10    # Urgent, do first
--priority 5     # Normal
--priority 1     # Whenever
```

Claims are processed `ORDER BY priority DESC, created_at ASC`. Assignees are your Hermes profile names. Assign `none` to leave unassigned for anyone to claim.

### Diversify assignees — don't default everything to one profile

The dispatcher works each profile's queue serially. Piling tasks on one profile
serializes work that could run in parallel. Pick the assignee by fit:

- **research profile** — research, competitive analysis, market scans, outreach drafts
- **writing profile** — content writing, copy, documentation, FAQ/marketing pages
- **build profile** — technical build, code, forms, infrastructure, debugging
- **generalist profile** — steering-loop tests, anything cross-cutting
- **the reviewer** — review/verification passes, research synthesis (via the inbox watcher)

When tasks are independent, spread them across profiles so they run concurrently.
When tasks are a chain (B depends on A's output), assign the same profile or use
`--parents` so the dispatcher orders them.

## Scoping for One-Shot Workers

### Decompose ambiguous work into chained tasks

One-shot workers can't ask "which approach do you want?" mid-task. So don't give them tasks that require mid-task decisions. Break them:

```
Task A (research):  "Survey 3 approaches for X, list pros/cons of each"
Task B (decide):    YOU or the reviewer picks the approach (not a kanban task)
Task C (implement): "Implement approach 2 from task A's findings"
Task D (review):    Reviewer pattern (see below)
```

Use `--parent <id>` at creation or `kanban link <parent_id> <child_id>` to chain. The child won't dispatch until the parent completes.

### What's too big — the sizing standard (from task-decomposition research)

Monolithic tasks are too much for the agents. The research (METR time horizons, Anthropic harness + multi-agent posts, Cognition, Google agent-scaling study, MAST failure taxonomy, HumanLayer 12-Factor) backs this. Author by these rules:

- **One task = one deliverable, one owner, one writer.** Never bundle research + implement + test + report in one task — split into artifact-chained children (parent links). If the body joins two deliverable types with "and also", split it.
- **Size: human-equivalent time is the primary measure (tightened Oct 25, 2026).** Author pieces a competent human could finish in **10–20 minutes**; **hard-split anything over ~30 minutes human-equivalent**. Agent turns stay a backstop: 20–40 turns targeted, progress signal at ~20 turns (deliverable file updated, or a comment), remainder re-scoped if it still looks big at ~60 turns. **The 90-turn agent cap is a safety net, never a budget — never author a task expected to need it.** Why the change: the Oct 25 evidence review found METR's measured 80%-success horizons for open-weight models are 1–12 minutes of human-equivalent work and no 27B model has a published horizon at all — the old ~45-minute authoring target was a frontier-model extrapolation. The fleet's own curve gets fitted from estimate-vs-actual logging (below) and supersedes these interim numbers when ~100 tasks are on record.
- **Scope guardrails:** ≤~5 files to edit (reads unrestricted); **3–5 acceptance criteria, each binary and machine-checkable, with the exact verification command named in the body** ("`pytest tests/test_x.py -q` passes" — never "tests pass").
- **Done is a gate, not a claim:** the named verification command must actually run inside the task's own run, output attached; code also gets a fresh-context verifier re-run (Reviewer Pattern below). "Wrote code, never ran the tests" is a verification-system failure (MAST), designed out at authoring time.
- **Phasing for anything larger:** Research → Plan → Implement as separate chained tasks, artifact + fresh context per phase. Bug fixes go test-first: the failing test is written and *seen to fail* before any fix code.
- **WIP = 1 task per agent.** Fleet concurrency is set by the reviewer's capacity, not by open slots: if 2+ tasks are waiting on verification, new implementation work waits — agents pull rework/verification first. Parallelize reads (research, diagnosis) freely; serialize writes — never two agents editing the same item or overlapping files.
- **Stall definition:** no progress signal (diff, test run, deliverable update, criterion completed) for ~15–20 turns = stall → interrupt, snapshot, diagnose: repeated action/error = loop (kill, re-scope); slow-but-real progress = split the remainder. Pairs with §5: the deliverable file IS the signal.
- **Retry ladder:** fix in place (≤3 consecutive errors) → re-scope with something material changed → escalate to the reviewer with artifacts. **Never re-dispatch an unchanged failed task** — failures are mostly task-determined (attempt correlation 0.68–0.87). Max ~3 agent-runs per task, then Jimmy reviews before any further spend. **Advisor consult at the second failure (tightened Oct 25, 2026):** when a task fails its second attempt — or repeats the identical error once — the next step is a reviewer consult (a stronger-model advisor over the worker's artifacts) before any third run is authored; evidence: Anthropic's advisor-strategy eval (small executor + strong advisor roughly doubled browse-task success at lower cost) and the Google/MIT finding that centralised validation cuts error amplification ~4× (17.2× → 4.4×).
- **Calibration (fleet data, Oct 22, 2026):** the fleet's own history now backs the defaults. From 127 first attempts (Oct 19–22): short tight specs (≤341 chars) complete first-try **80%**; runs finishing <15 min **78–82%**; everything 15 min–2h sits at 35–46%; 2h+ ~29%. 69% of first-attempt wins finish ≤40 min. **The authoring test (updated Oct 25, 2026): "could a competent human do this piece in 10–20 minutes, and is it one deliverable?" If either answer is no, split it.** (Supersedes the earlier ~45-minute test, which the Oct 25 evidence review showed was calibrated to frontier models, not this fleet.) Caps ≤30 min time out 67–86% — the 2h cap floor stands as headroom, never as the size. Still open: no per-run turn/token counts exist yet (per-run instrumentation is still landing); with ~30–50 instrumented tasks the turns curve becomes a one-query re-run of the calibration.

- **Generation tasks chain by artifact (Oct 25, 2026; origin: the Wave 1 verify-skill builds).** A task that GENERATES a multi-artifact deliverable (a skill, a tool, a package: code + docs + evidence + commit) is never authored as one shot, however clean its criteria look — the Oct 24 verify builds each carried only 4 criteria and still died at the 2h cap twice with no report, because artifact count, not criterion count, was the real size. Default shape: chained pieces, one artifact each (A1: core artifact only, exists + smoke check → A2: its proof, live run + adversarial + evidence → B: docs/map → C: end-to-end drive + assemble + commit), 1h caps. **Mechanical gate: run `check-task-size.py` (beside this skill) on every task body before `hermes kanban create` — a FAIL means decompose first.** The only override is the user's explicit call, recorded in the task body. **Amendment (Oct 25, 2026, evening):** piece A as first defined here ("core artifact + its proof") went 0-for-3 on worker completions across the Wave 1 verify builds — engine A finished Jimmy-direct, console A/A2 and sovereign A all timed out — because writing a program from scratch and proving it live are each a session-sized piece for the fleet models. Build and proof are separate pieces now (A1/A2 above), and the checker FAILs a body that bundles them without a sub-piece marker.

- **Evidence update (Oct 25, 2026 — from the task-sizing evidence review).** The six core practices were each checked against controlled studies and kept; these refinements ride along:
  - **Verify every ~10 dependent steps.** End-to-end success compounds per step (p^n: 80% over 20 unverified steps needs ≥98.9%/step). Splitting alone does not beat the math — it pays only through a verification gate + per-piece retry + fresh context. A body whose plan runs >~10 dependent steps without a named mechanical check is a sizing smell; name the step-boundary verification in the body.
  - **Criteria are pre-registered and not worker-editable.** The author writes the 3–5 binary criteria before the run; the worker may not edit, weaken, or reinterpret them (SWE-ABS: strengthened tests retroactively rejected ~20% of "passing" patches). Code tasks carry **two** named commands: the success command and a **regression (pass-to-pass) command**; the reviewer re-runs both in fresh context and attaches raw output (TDAD: naming exactly which tests a change affects cut regressions 6.08% → 1.82%; generic "write tests first" instructions alone made regressions worse).
  - **Chains stay sequential and short.** ≤~6 verified pieces per parent goal before a reviewer re-plan checkpoint (0.9^6 ≈ 53% even at strong per-piece reliability). Never parallelise sequential build work into multi-agent fan-out — measured −39…−70% on sequential tasks (Google/MIT scaling study); parallel is for independent reads/research only.
  - **Checkpoints belong at verified boundaries.** The primary state-persist trigger is a verified segment boundary (a piece proven, a check green); the agent-checkpoint plugin's 50%/80%-of-budget fires are the backstop for runs that never reach a boundary. The 50/80 timing itself is unmeasured anywhere in the literature — flagged, not hidden. A checkpoint/state file must carry: what's proven, **failed approaches**, the exact commands run with their last outputs, and the git hash.
  - **Every body carries a human-equivalent estimate** ("Human-equivalent estimate: ~N min") and reviews log actual agent time + turns against it. This log is the raw material for the fleet's own success-vs-length curve — the single highest-value measurement per the review, because no published 27B curve exists to borrow. Fit after ~100 tasks; the fitted curve then replaces the interim 10–20-minute default.

### Operational gates (enforced by the reviewer + the review sweep)

The authoring rules above only work if the board is run by them:

- **WIP = 1 per agent, review queue caps dispatch.** If 2+ completed tasks are waiting on the reviewer's verification, no new implementation task is authored or promoted for that line of work — the next task authored is rework or verification. The sweep counts tasks in `review` plus completions the reviewer hasn't verified yet.
- **Stall is a progress signal, not a heartbeat.** A run is stalled when its deliverable file hasn't changed, no test/command output has appeared, and no criterion has completed for ~15–20 turns of activity (or ~30 min of wall time on a running task) — even if heartbeats are fresh. Response: snapshot the deliverable + log tail, then diagnose loop vs slow progress before touching the run.
- **Retry ladder is a gate, not advice.** A task at 3 agent runs is done spending: it gets blocked pending the reviewer's review, and any further attempt must name the material change (narrower scope, different evidence, a split) in the task or in the reviewer's unblock comment. The sweep flags any ready/running task with ≥3 runs that lacks that gate.
- **Caps are enforced by hand if your dispatcher doesn't reap them.** Track elapsed-vs-cap on every running task (the review sweep does this); a run past cap with no final report on file is a failed run.

```
Too big:   "Build the quote calculator"
Right:     "Design quote calculator pricing logic (decision doc)"
           "Implement quote calculator frontend"
           "Write quote calculator validation tests"
```

### What's too vague

```
Too vague: "Improve the site"
Too vague: "Research the market"
Scoped:    "Audit example.com homepage for positive-voice violations, list each with suggested rewrite"
Scoped:    "Find 5 local businesses that dispose of IT equipment, record name + contact + estimated volume"
```

### Chat-first for fuzzy, kanban for crisp

Standing decision: **don't force ambiguous work into one-shot tasks.**

- **Fuzzy** ("what should our pricing strategy be?", "which framework should we use?") → talk it through with the agent in chat first, *then* write the kanban task with the decided approach baked in.
- **Crisp** ("draft the page", "research these 10 companies", "fix this bug") → straight to kanban.

## Reviewer Pattern (DEFAULT)

Every completed task goes through verification before it's truly done.

### The loop

```
Worker completes task → hands to reviewer (the bridge agent)
  → Reviewer checks deliverable against acceptance criteria
  → PASS: mark done
  → FAIL: request changes with specific feedback
```

### Setting up the handoff

**Option A — Native review flow** (preferred for agent-to-agent):

```bash
# Worker (or you) moves the task to review state
HERMES_HOME=/home/ubuntu/.hermes hermes kanban request-review <id> \
  --reviewer muse \
  --summary "Pricing page drafted at <path>. Verified: 0 banned words (grep), all 6 pricing decisions covered."
```

The `--reviewer` flag reassigns to the reviewer profile. The inbox watcher picks up tasks assigned to the reviewer and routes them for verification.

**Option B — Direct assignment:**

```bash
HERMES_HOME=/home/ubuntu/.hermes hermes kanban assign <id> muse
HERMES_HOME=/home/ubuntu/.hermes hermes kanban comment <id> "Ready for review against acceptance criteria in the task body."
```

### What "verified" means

Verification is **mechanical, not vibes**. The reviewer must:

1. Read the acceptance criteria from the task body
2. Check each criterion against the actual deliverable (open the file, run the grep, visit the URL)
3. Confirm or reject with specifics

```bash
# See what the worker actually did
HERMES_HOME=/home/ubuntu/.hermes hermes kanban log <id>

# Read the full task with comments
HERMES_HOME=/home/ubuntu/.hermes hermes kanban show <id>
```

**Reject with specifics**, not "looks off":

```
Bad:  "Can you improve this?"
Good: "AC #3 fails: found 'don't' on line 14 and 'no hidden fees' on line 31. Rewrite those two sentences in positive voice."
```

Use `request-changes` for the native flow, or comment + reassign for the direct flow.

## CLI Reference

All commands run on the Hermes box. From the Muse VM, prefix with your SSH helper:

```bash
# The pattern for every command below:
<ssh-helper> 'HERMES_HOME=/home/ubuntu/.hermes hermes kanban <command>'

# Shorthand used in this skill (assumes you're ON the box):
export HH='HERMES_HOME=/home/ubuntu/.hermes hermes kanban'
```

### Create

```bash
$HH create "Task title" \
  --assignee <profile> \
  --priority 5 \
  --body "Full task body with context, constraints, and ACCEPTANCE CRITERIA." \
  --parent <task_id>          # optional: chain after another task
  --max-runtime 2h            # optional: kill worker if it runs too long
  --idempotency-key "slug"    # optional: prevent duplicates on retry
```

### Inspect

```bash
$HH list                                    # all tasks, priority-sorted
$HH list --assignee <profile>               # one worker's tasks
$HH list --status ready                     # filter by status
$HH list --status blocked                   # what's stuck
$HH show <id>                               # full task + comments + events
$HH log <id>                                # worker's run log (tool calls, output)
$HH log <id> --tail 2000                    # last 2KB of the log
$HH stats                                   # counts by status + assignee
```

### Manage

```bash
$HH comment <id> "Note for the worker or reviewer"
$HH assign <id> <profile>                   # (re)assign to a profile
$HH assign <id> none                        # unassign
$HH link <parent_id> <child_id>             # child waits for parent
$HH unlink <parent_id> <child_id>           # remove dependency
$HH promote <id>                            # todo/blocked -> ready (recovery)
```

### Finish

```bash
$HH complete <id> --result "Did X, Y, Z"
$HH complete <id> --result "Summary" --metadata '{"files_changed": ["a.md"], "tests_run": 3}'
$HH block <id>                              # stuck, needs human input
$HH request-review <id> --reviewer muse --summary "What was done and how it was verified"
$HH archive <id>                            # done and filed away
```

### Task states

| Status | Meaning |
|--------|---------|
| `todo` | Created, not yet dispatched |
| `ready` | Ready for a worker to claim |
| `running` | Claimed, worker active |
| `review` | Implementation done, awaiting reviewer |
| `done` | Completed and accepted |
| `blocked` | Stuck, needs human input |
| `scheduled` | Parked for later (time-based, not input-based) |
| `triage` | Needs spec work before it's actionable |
| `archived` | Filed away |

## Anti-Patterns

What produces bad results, with fixes:

**1. Vague tasks**
```
Bad:  "Improve the company site"
Fix:  "Audit example.com/services for positive-voice violations; list each violation with line number and suggested rewrite"
```

**2. Missing acceptance criteria**
```
Bad:  Body describes the work but never says what "done" looks like
Fix:  Always include an ACCEPTANCE CRITERIA section with checkable statements
```

**3. Mid-task decisions**
```
Bad:  "Pick the best approach and implement it"
Fix:  Split: task 1 researches approaches, YOU pick, task 2 implements the pick.
      (No steering exists yet — workers can't ask mid-run.)
```

**4. Multiple deliverables in one task**
```
Bad:  "Write the pricing page, the FAQ update, and the email announcement"
Fix:  Three tasks, chained with --parent. Each gets its own acceptance criteria.
```

**5. Assuming shared context**
```
Bad:  "Update it with the new numbers" (which file? which numbers?)
Fix:  "Update /home/ubuntu/.hermes/kanban/workspaces/t_xxx/pricing.md: change pickup fee from $75 to $100 in all 3 places it appears"
```

**6. No deliverable location**
```
Bad:  "Research competitors" (where does the research go?)
Fix:  "Save findings as competitors.md in the task workspace, one section per company: name, URL, pickup pricing, notes"
```

**7. Timed run with no report deadline**
```
Bad:  "--max-runtime 2h" and a body that only describes the end state — the
      worker goes heads-down, the reaper fires at 2h, and two hours of
      findings vanish unwritten.
Fix:  Every capped task carries both §5 clauses: DELIVERABLE-FIRST (skeleton
      file is the worker's first action, findings written as found) and
      REPORT-BEFORE-TIMEOUT (final report + summary comment by 80% of the
      cap, done or not, INCOMPLETE reports included). Size the task to fit
      ~40 of the worker's ~60 steps, or split it.
```
