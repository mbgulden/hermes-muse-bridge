"""fleetops.regression — the fleet regression replay + scoring runner.

Replays the frozen fleet regression set on the kanban board and scores each
replay by its checker exit code, gated on a **dispatch receipt per replay**:
the runner records board ground truth (a ``task_runs`` row per replay) and
asserts the receipt count equals the set size BEFORE any scoring. A scoring
run with zero or short dispatch exits non-zero and writes no scores.

This is the port of the standalone box script
``/home/ubuntu/work/fleet-top5/regression-runner/run_regression.py`` with the
checker-contract defect fixed (t_00b9bd48): the original scored every planned
replay from its ``checker_command`` exit code with no assertion that the
replay was actually dispatched first.

Pure stdlib. Board access is read-only; the only writes are the replay
``hermes kanban create`` subprocesses and the local ``results.json``.
"""
from .dispatch_receipt import (
    EXIT_CONTRACT,
    DispatchReceiptError,
    assert_dispatch_receipts,
    board_connect,
    board_find_by_key,
    board_task_status,
    enforce_dispatch_gate,
    record_dispatch_receipts,
)

__all__ = [
    "EXIT_CONTRACT",
    "DispatchReceiptError",
    "assert_dispatch_receipts",
    "board_connect",
    "board_find_by_key",
    "board_task_status",
    "enforce_dispatch_gate",
    "record_dispatch_receipts",
]
