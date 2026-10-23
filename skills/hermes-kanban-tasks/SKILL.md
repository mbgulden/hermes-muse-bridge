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
- **Size: 20–40 agent turns.** Progress signal at ~20 turns (deliverable file updated, or a comment). If the remainder still looks big at ~60 turns, the run stops and the work re-scopes. **The 90-turn agent cap is a safety net, never a budget — never author a task expected to need it.**
- **Scope guardrails:** ≤~5 files to edit (reads unrestricted); **3–5 acceptance criteria, each binary and machine-checkable, with the exact verification command named in the body** ("`pytest tests/test_x.py -q` passes" — never "tests pass").
- **Done is a gate, not a claim:** the named verification command must actually run inside the task's own run, output attached; code also gets a fresh-context verifier re-run (Reviewer Pattern below). "Wrote code, never ran the tests" is a verification-system failure (MAST), designed out at authoring time.
- **Phasing for anything larger:** Research → Plan → Implement as separate chained tasks, artifact + fresh context per phase. Bug fixes go test-first: the failing test is written and *seen to fail* before any fix code.
- **WIP = 1 task per agent.** Fleet concurrency is set by the reviewer's capacity, not by open slots: if 2+ tasks are waiting on verification, new implementation work waits — agents pull rework/verification first. Parallelize reads (research, diagnosis) freely; serialize writes — never two agents editing the same item or overlapping files.
- **Stall definition:** no progress signal (diff, test run, deliverable update, criterion completed) for ~15–20 turns = stall → interrupt, snapshot, diagnose: repeated action/error = loop (kill, re-scope); slow-but-real progress = split the remainder. Pairs with §5: the deliverable file IS the signal.
- **Retry ladder:** fix in place (≤3 consecutive errors) → re-scope with something material changed → escalate to the reviewer with artifacts. **Never re-dispatch an unchanged failed task** — failures are mostly task-determined (attempt correlation 0.68–0.87). Max ~3 agent-runs per task, then the reviewer reviews before any further spend.
- **Calibration (one fleet's data, Oct 2026):** fleet history backs the defaults. From 127 first attempts: short tight specs (≤341 chars) complete first-try **80%**; runs finishing <15 min **78–82%**; everything 15 min–2h sits at 35–46%; 2h+ ~29%. 69% of first-attempt wins finish ≤40 min. **The authoring test until turn data exists: "would this plausibly finish inside ~45 minutes of focused run time?" If not, split it.** Caps ≤30 min time out 67–86% — the 2h cap floor stands as headroom, never as the size. Re-run this calibration on your own board once you have ~100 first attempts.

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
