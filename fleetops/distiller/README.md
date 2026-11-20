# fleetops.distiller

Distillation decisions for failed Hermes kanban fleet tasks — the
response half of the improvement loop, built on the cause classes
from `fleetops.classifier`.

Given one failed task and its classified cause, the distiller
decides what happens next under the distill policy (distill on first
failure, with the infrastructure carve-out):

| Cause | Action | Successor? |
|---|---|---|
| *(lineage at 3 strikes, any cause)* | `escalate` — to Jimmy-direct | No |
| `infrastructure` | `rerun_as_is` — the task was not wrong | No — the original re-runs unchanged |
| `task shape` | `distill` | Yes — reduced scope, lesson written in |
| `task execution` | `distill` | Yes — reduced scope, lesson written in |
| `external dependency` | `wait` — a wait/decision record | No |
| `unclassified` | `flag_for_labeling` — fail closed | No |

The 3-strike cap is checked first: distillation never extends a
lineage past the existing escalation to Jimmy-direct, whatever the
cause. Unclassified causes are never redistributed into another
branch, and an unrecognized cause string is treated as
unclassified.

A distilled successor spec carries: the original title; a clarified
body — the original body preserved, a header block prepended with
the failure's lesson under a "Distillation lesson" heading and a
"Scope reduction" section limiting the run to the single primary
deliverable, and the DELIVERABLE-FIRST + REPORT-BEFORE-TIMEOUT
clauses preserved (kept verbatim when present, restored from the
canonical texts when missing, never duplicated); an assignee —
unchanged by default, an explicit reassignment when named, or the
next profile in the kai → george → ned rotation (the profile-switch
move folded in from the hang-redispatch hypothesis; fred and other
identities never rotate); `supersedes` naming the original, which
is marked superseded; and the lineage strike count incremented by
one.

The distiller trusts the classifier's cause and never re-derives
one from task text: a body that merely mentions a credential in
passing cannot flip a task-caused failure into a wait.

The module performs no board writes. It returns a decision and a
successor spec; authoring the successor onto the board stays with
the operator.

## Usage

```python
from fleetops.distiller import distill

result = distill(
    task,                      # {"id", "title", "body", "assignee"}
    cause,                     # a fleetops.classifier cause class
    strike_count=1,            # lineage strikes including this failure
    failure_summary="Burned the 2h cap with no deliverable movement.",
    rotate_profile=True,
)
print(result.action)           # "distill"
print(result.successor)        # the successor spec, or None
print(result.log_record)       # the distillation log record
```

CLI over one JSON input object (shape documented in `cli.py`):

```bash
python3 -m fleetops.distiller --input failure.json --log distillations.jsonl
```

The decision is printed as JSON. Exit code 0 on a decision, 2 on a
usage or input error.

## Distillation log format

Every decision — all five actions — produces one log record with
these fields:

- `task_id` — the failed task's id.
- `title` — the failed task's title.
- `cause` — the canonical cause class that decided the branch.
- `action` — one of `rerun_as_is`, `distill`, `wait`,
  `flag_for_labeling`, `escalate`.
- `strike_count` — the lineage strike count the decision was made
  at.
- `what_failed` — the failure summary the decision rests on.
- `what_changed` — a list of the changes the decision makes (for a
  distillation: scope reduction, lesson written in, clauses
  preserved, original superseded, any reassignment; for the other
  actions: the single no-change / wait / flag / escalation entry).
- `successor_assignee` — the successor's assignee for a
  distillation, otherwise null.

`render_log_record(record)` renders a record as a markdown block
headed `## Distillation — <task_id>` with one bullet per field.
`append_log_record(path, record)` appends a record to a log file as
one JSON object per line (JSONL, keys sorted) — the durable form
the weekly reporting reads.

## Tests

```bash
python3 -m pytest fleetops/distiller -q
```
