# fleetops.scorecard

The weekly measurement extract for a Hermes kanban fleet — the one
scorecard the fleet dashboard and the Friday loop driver both
consume, computed the same way every week from a board snapshot.

The scorecard holds no private copy of the measurement rules for its
first two sections: first-run value success and the failure-cause
split are computed by calling `fleetops.classifier` directly, so the
packaged definitions and the running definitions are the same code.

## What it computes

1. **First-run value success** (classifier rules): a task counts when
   its first run completed AND its deliverable earned a
   reviewer-authored PASS verdict. The extract carries the covered
   count (numerator / denominator), the total done count, and the
   rate — plus **verdict coverage** (done tasks carrying any
   parseable reviewer verdict, over all done tasks) as its own named
   metric, beside the rate, never blended into it.
2. **Failure-cause split** (classifier rules): for failed first runs
   and for all failed runs — counts and share per class
   (infrastructure / task shape / task execution / external
   dependency / unclassified), unclassified as its own slice, never
   redistributed, plus the task-caused share.
3. **Timed-out hours + completions**: the timed-out hours share over
   finished runs in the window (timed-out run hours / all finished
   run hours), the all-time figure beside it, and weekly completions
   (tasks reaching done in the window; completed-outcome runs beside
   them).
4. **Estimate calibration**: per task class, the median estimate
   overrun — actual run minutes summed over the task's finished runs,
   over the body's `Human-equivalent estimate: ~N min` — over done
   tasks carrying an estimate, with n per class, plus the overall
   median. The board carries no class field, so the class is derived
   deterministically: an explicit `Work type:` / `Task class:` line
   in the body wins; else title signals in the order drafts
   (draft/copy/email/letter/postcard/newsletter wording), research
   (research/survey/scan/investigate wording), build (`port-`,
   `skillforge-`, `build-`, `fix-` family prefix, or
   build/implement/deploy/install wording); else other.
5. **Token accounting**: recorded input/output totals from run
   metadata; runs without token metadata are estimated from the
   recorded population's per-profile median total tokens (computed
   over recorded completed runs, all-time), split in/out by the
   recorded ratio — recorded and estimated are always reported
   separately, never blended. Recorded-share is reported at run level
   and token level. Money is **replacement cost** — what the token
   volume would cost at the config rate table's per-million prices,
   not money spent — computed separately for recorded, estimated, and
   total. Net value (total replacement cost minus electricity) is
   reported only where the caller supplies a running-cost figure
   (`electricity_usd` for the window, `electricity_usd_all_time`);
   otherwise it is null, never invented.
6. **Slot saturation**: running runs now against the concurrency cap
   (passed in as data; with no cap configured the flags are null and
   saturation is never claimed). Sustained saturation is defined as:
   over the trailing 24 hours, the board sat at the cap for at least
   50% of the time, with concurrency reconstructed from run start/end
   intervals. Both thresholds are config data
   (`saturation_window_hours` / `saturation_share`), so a deployment
   can move them without a code change.

Sections 1–3 and 5 are reported for the weekly window and all-time;
estimate calibration is all-time (it calibrates authoring factors,
which are read across windows); saturation is a current-state read
over its trailing window.

## Usage

```python
from fleetops.scorecard import build_scorecard, render_markdown

extract = build_scorecard(
    {"tasks": tasks, "runs": runs, "comments": comments},
    {"now": 1800000000, "concurrency_cap": 8,
     "rate_table": {"reference_model": "my-reference-model",
                    "input_per_million": 3.0,
                    "output_per_million": 15.0}},
)
print(extract["first_run_value_success"]["all_time"])
print(render_markdown(extract))
```

CLI against a kanban.db snapshot (opened read-only):

```bash
python -m fleetops.scorecard.cli /path/to/kanban.db --cap 8 \
    --rates rates.json --format markdown
```

Config keys (all optional): `profiles` (default kai, george, ned,
fred; orchestrator folds into fred), `now`, `window_days` (default
7), `concurrency_cap`, `rate_table`, `electricity_usd`,
`electricity_usd_all_time`, `reviewers`, `saturation_window_hours`,
`saturation_share`. Without a caller-supplied rate table the extract
names the module's `EXAMPLE_RATE_TABLE` — a clearly-labeled example
($2.50/M input, $10.00/M output) — as its basis; no real rate is
hard-coded in the module.

The module is pure stdlib and read-only: it takes plain row dicts
and returns data. It never writes to the board.

## Tests

```bash
python3 -m pytest fleetops/scorecard -q
```
