# verify-widget A1: build the control CLI (build-only sub-piece)
This task is one chained piece (piece a1). Build scripts/control-widget.py with a doctor subcommand.
Human-equivalent estimate: ~45 min
Regression command: python3 -m py_compile scripts/control-widget.py
## Acceptance criteria
1. scripts/control-widget.py exists
2. --help exits 0
3. py_compile passes
