# fleetops.regression — fleet regression runner

Replays the frozen fleet regression set manifest as new `regr-` kanban tasks,
polls each replay to completion, and scores each replay by running the entry's
`checker_command`. Scoring policy: the runner reports ONLY checker exit codes.
It makes no judgment of replay output quality.

Ported into the repo from the standalone box script
`/home/ubuntu/work/fleet-top5/regression-runner/run_regression.py`
(t_00b9bd48 / t_8c97c027).

## Dispatch-receipt contract

The original runner scored every planned replay from its `checker_command`
exit code with **no assertion that the replay was actually dispatched before
scoring**. A replay the dispatcher never picks up stayed `ready`, hit
`timed_out_waiting`, and was still scored against the original task's
pre-existing deliverables — 0/N replays dispatched yet a full score table was
written (t_dc281037: 0/5 dispatched, zero `task_runs` rows, yet 4/4 PASS).

The fix (`dispatch_receipt.py`, enforced in `run_regression.py` before any
scoring):

* A scoring run records a dispatch receipt per replay — board ground truth, a
  `task_runs` row for the replay task — and asserts, BEFORE any scoring, that
  the receipt count equals the set size AND that every replay was dispatched.
  Zero or short dispatch => loud non-zero exit (`EXIT_CONTRACT`) and NO scores
  written (no checker runs, no `results.json`).
* A `--dry-run` records one receipt per case, asserts the receipt count equals
  the set size (accounting gate), and creates nothing and scores nothing.

Ground truth for replay state is the kanban board SQLite DB (read-only URI) —
the runner never trusts its own stdout for board state.

## Usage

```sh
# Validate the manifest, record receipts, assert the accounting gate.
# Creates nothing, scores nothing. Exit 0 on a valid manifest.
python -m fleetops.regression --manifest <frozen-set.json> --dry-run

# Full run: create replays, poll, enforce the dispatch gate, score, write results.json.
python -m fleetops.regression --manifest <frozen-set.json>
```

## Tests

```sh
python -m pytest fleetops/regression/tests/test_dispatch_receipt.py -v
```

Covers the zero-dispatch gate (non-zero, no scores), the short-dispatch gate,
the receipt-count accounting gate, the full-dispatch pass, and a dry run over
the real frozen 36-case set (36 receipts recorded, nothing created/scored).
