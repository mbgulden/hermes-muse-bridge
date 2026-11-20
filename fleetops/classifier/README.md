# fleetops.classifier

Failure-cause classification and first-run value success for Hermes
kanban fleet runs — the measurement core of the improvement loop.

Every failed run is classified into exactly one cause class from its
kanban record:

- **infrastructure** — dead worker or claim, endpoint or tunnel outage,
  VM replacement, dispatcher stall
- **task shape** — the run burned its cap with no deliverable movement;
  the task as authored could not land in one run
- **task execution** — a deliverable was produced but earned a FAIL
  verdict, or the run gave up mid-work
- **external dependency** — the task's own block text names a wait on an
  outside reply, a credential, or a decision
- **unclassified** — the record cannot support a class; shown as its own
  slice, never redistributed

The class decides the response under the distill policy (see the
`improvement-loop` skill in muse-pstack): infrastructure failures
re-run as-is; task-caused failures are distilled, never repeated as-is.

First-run value success counts a task only when its first run completed
AND its deliverable earned a reviewer-authored PASS verdict. Verdict
coverage is reported beside the rate, never blended into it.

## Usage

```python
from fleetops.classifier import classify_failures, first_run_value_success

split = classify_failures(tasks, runs, comments,
                          profiles={"kai", "george", "ned", "fred"})
print(split["first_runs"])   # counts per class + task-caused share

value = first_run_value_success(tasks, runs, comments,
                                profiles={"kai", "george", "ned", "fred"})
print(value)                 # rate + verdict coverage, separately
```

CLI against a kanban.db snapshot (opened read-only):

```bash
python -m fleetops.classifier.cli /path/to/kanban.db
```

## Tests

```bash
python3 -m pytest fleetops/classifier -q
```
