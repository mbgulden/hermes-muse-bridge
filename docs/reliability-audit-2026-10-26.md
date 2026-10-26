# Fleet reliability audit — summary (Oct 26, 2026)

A faithful summary of the Oct 26, 2026 fleet reliability audit: a
before/after measurement of the agent-checkpoint plugin and the sizing and
review levers around it, run over the kanban board's full run history. Every
number below is the audit's own; the honest read is the audit's headline
read, not a gloss.

## Method

Three windows, cut by run `started_at` (tasks by `created_at`):

| Period | Window | Length |
|---|---|---|
| BEFORE | Oct 19–21, 2026 (baseline, reused — not re-derived) | 3 days |
| BUILD | Oct 22, 00:00 local → the enable moment | 40.3 h |
| AFTER | The enable moment → Oct 26, 09:12 local | 65.2 h |

**The enable moment** is 2026-10-23T22:02:12Z — when the agent-checkpoint
plugin's per-profile installs were mechanically verified live (matching
file hashes, plugin present in every worker profile's enabled list).
Inside AFTER, plugin v0.2.0 shipped Oct 25 ~19:15Z; a run that started
before that carried v0.1.0 for its whole life, and its silence is never
counted against v0.2.0.

Two cuts are shown throughout. **All-profile** is the baseline method.
**Fleet-only** (the four worker profiles) is the honest measure of the
levers, because AFTER's all-profile numbers are composition-driven: 114 of
the 184 tasks created in AFTER were bulk tasks assigned to the orchestrator
profile, which completes at 93%, while the fleet's own work is the chain
experiment described below. Small-sample caution: AFTER fleet runs = 44
ended, 23 of them the 3600-second chain pieces.

## Headline table

| Metric | BEFORE | BUILD | AFTER |
|---|---|---|---|
| Tasks created in window | 119 | 53 | 184 |
| Run completion, all profiles | 46% (99/213) | 34.8% (40/115) | 65.4% (68/104) — composition, see method |
| Run completion, fleet profiles only | — | 34.8% (40/115) | **34.1% (15/44) — flat** |
| timed_out / crashed / gave_up (ended runs) | 45 / 44 / 4 | 52 / 15 / 1 | 33 / **0** / **0** |
| First-attempt task completion | 65% (77/118) | 47.1% (16/34) | 89.7% all; fleet-only 80% (12/15 — censored: only 15 of 68 fleet tasks created in AFTER had resolved) |
| Cap timeout rate, 3600s bucket | 35% | 40% (4/10) | **96% (22/23)** |
| Fleet-only, 3600s runs completed | — | — | **0 of 23** |
| Fleet-only, >3600s runs completed | — | — | 71% (15/21) |
| Bad runs with zero worker comments | — | 50 of 68 | 32 of 33 |
| Exited cleanly without the close-out call | 13 | 0 | 0 |
| Iteration-exhausted errors | 15 | 3 | 0 |

Checkpoint trail in AFTER: **88 checkpoint comments and 53 distinct
checkpoint files** (all in the v0.2.0 era; v0.1.0 had no comment channel),
86 distinct sessions with a tier-fire log line. **Worker-filed INCOMPLETE
reports: zero** — no worker summary follows any final checkpoint on a
failing chain task; the durable record that exists is the plugin's own
comment + file.

## The honest read

The all-profile completion jump (34.8% → 65.4%) is **composition, not fleet
improvement**: fleet-only completion is flat (34.8% → 34.1%), and the AFTER
gain comes from orchestrator-assigned batch tasks completing at 93%.

Two real improvements are visible underneath:

- **Crashes went to zero** (44 → 15 → 0), and both clean-exit error
  signatures went to zero with them.
- **The checkpoint plugin demonstrably leaves a durable trail** on every
  v0.2.0-era failing task, and reviewers used that trail to salvage and
  close pieces whose runs had timed out. The trail is the tool's current
  efficacy ceiling: it helps salvage, it does not yet lift completion.

The real regression is concentrated and named: **the 3600s chain pieces
complete 0 of 23 runs in AFTER.** Decomposing generation work into chained
1-hour pieces did not fix completion — it moved the failure into a smaller
box. Every failing chain-B task carried both standing clauses
(deliverable-first, report-before-timeout) and still filed nothing: the
workers enumerate and probe, and the write step never happens. The audit's
failure ledger classifies all nine AFTER-era entries as the same pattern —
work held in session, never written to disk.

One more measured wrinkle: **reaping latency is routine, not an edge
case** — runs end ~1,000–1,500s past their caps, so an effective cap is
roughly cap + ~20 minutes.

## What the audit says to do next

1. **Diagnose the stall before authoring more successors.** Read one full
   failing run session past the probing phase and name what the worker does
   between enumerating and the cap. The next task shape must target that
   stall, not the scope — scope is already decomposed.
2. **Give the next chain attempts a write-first gate with teeth, or a 2h
   cap** — the 3600s bucket is 0/23 while longer-capped fleet runs complete
   at 71%. If the 1h cap stays, require the first deliverable file on disk
   within 30 minutes, verified by file mtime at review.
3. **The escalated pieces go reviewer-direct** (the 3-strike path worked as
   designed; no fourth fleet attempt was queued). A fourth attempt without
   the stall diagnosis would repeat the measured 0/23 pattern.

*Source: internal fleet reliability audit, Oct 26, 2026 (baseline Oct 21;
enable-moment and window definitions as above). This summary adds no
numbers of its own.*
