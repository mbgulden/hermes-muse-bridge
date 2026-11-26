"""Dispatch-receipt contract for the fleet regression runner.

This module owns the one thing the original runner got wrong: it scored
replays without ever proving they were dispatched.

A "dispatch receipt" is board ground truth, not runner stdout: for each
planned replay we look up its board task (by idempotency key) and count its
``task_runs`` rows. A replay counts as **dispatched** only if the dispatcher
actually ran it (at least one ``task_runs`` row). A replay that was created but
never picked up (cap full) has a board row but zero ``task_runs`` rows and is
therefore **not** dispatched.

The contract, enforced BEFORE any scoring:

1. **Accounting gate (always):** the receipt count equals the set size being
   run (one receipt per planned replay). A missing receipt means the runner
   cannot account for a case, so it refuses to score.
2. **Dispatch gate (scoring runs):** the dispatched count equals the set size
   (every replay was actually dispatched). Zero or short dispatch => the gate
   fails loudly (non-zero exit) and **no scores are written**.

``record_dispatch_receipts`` / ``assert_dispatch_receipts`` /
``enforce_dispatch_gate`` are pure over a read-only ``sqlite3`` connection so
they are directly testable against an in-memory board.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Mapping, Sequence, Tuple

# Non-zero exit used when the dispatch-receipt contract is violated before
# scoring. Distinct from the runner's validation exit (2) so operators can tell
# "manifest is bad" from "dispatches are missing".
EXIT_CONTRACT = 3


class DispatchReceiptError(RuntimeError):
    """Raised when the dispatch-receipt contract is violated before scoring."""


# ---------------------------------------------------------------------------
# Read-only board access (receipts are ground truth, never runner stdout)
# ---------------------------------------------------------------------------

def board_connect(path: str) -> sqlite3.Connection:
    """Open the kanban board read-only. The runner never writes board state."""
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def board_find_by_key(conn: sqlite3.Connection, key: str) -> "str | None":
    """Return the board task id for an idempotency key, or None."""
    rows = conn.execute("SELECT id FROM tasks WHERE idempotency_key = ?", (key,)).fetchall()
    return rows[0][0] if rows else None


def board_task_status(conn: sqlite3.Connection, task_id: str) -> "str | None":
    """Return the task's status, or None if the row is gone."""
    rows = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchall()
    return rows[0][0] if rows else None


def board_count_runs(conn: sqlite3.Connection, task_id: str) -> int:
    """Count a task's ``task_runs`` rows — the dispatch-receipt count."""
    rows = conn.execute(
        "SELECT COUNT(*) FROM task_runs WHERE task_id = ?", (task_id,)
    ).fetchone()
    return int(rows[0]) if rows else 0


# ---------------------------------------------------------------------------
# Receipt recording + the contract gate
# ---------------------------------------------------------------------------

def record_dispatch_receipts(
    conn: sqlite3.Connection, plans: Sequence[Mapping]
) -> List[Dict]:
    """Record one dispatch receipt per planned replay from board ground truth.

    A receipt is ``{key, original_task_id, replay_task_id, board_status,
    run_rows, dispatched}`` where ``dispatched = run_rows >= 1``. A planned
    replay with no board row is recorded with ``replay_task_id=None`` and
    ``dispatched=False`` (created-but-never-dispatched is not dispatched).
    """
    receipts: List[Dict] = []
    for p in plans:
        key = p["key"]
        task_id = board_find_by_key(conn, key)
        status = board_task_status(conn, task_id) if task_id else None
        run_rows = board_count_runs(conn, task_id) if task_id else 0
        receipts.append({
            "key": key,
            "original_task_id": p.get("original_task_id"),
            "replay_task_id": task_id,
            "board_status": status,
            "run_rows": run_rows,
            "dispatched": run_rows >= 1,
        })
    return receipts


def assert_dispatch_receipts(
    receipts: Sequence[Mapping],
    expected_count: int,
    require_all_dispatched: bool = True,
) -> Tuple[int, int]:
    """Assert the dispatch-receipt contract. Returns (n_dispatched, n_receipts).

    Always enforces the accounting gate: ``len(receipts) == expected_count``.
    When ``require_all_dispatched`` is true (scoring runs) also enforces that
    every receipt is dispatched. Raises :class:`DispatchReceiptError` with a
    loud, per-case explanation on any violation.
    """
    n_receipts = len(receipts)
    n_dispatched = sum(1 for r in receipts if r.get("dispatched"))

    problems: List[str] = []
    if n_receipts != expected_count:
        problems.append(
            "accounting: %d dispatch receipts recorded, expected %d "
            "(one per case in the set being run)" % (n_receipts, expected_count)
        )
    if require_all_dispatched and n_dispatched != expected_count:
        missing = [r.get("key") for r in receipts if not r.get("dispatched")]
        problems.append(
            "dispatch: %d/%d replays dispatched; missing/never-run: %s"
            % (n_dispatched, expected_count,
               ", ".join(missing) if missing else "(none — count mismatch only)")
        )

    if problems:
        raise DispatchReceiptError("; ".join(problems))
    return n_dispatched, n_receipts


def enforce_dispatch_gate(
    conn: sqlite3.Connection,
    plans: Sequence[Mapping],
    require_all_dispatched: bool = True,
) -> Tuple[int, List[Dict], int, str]:
    """Record receipts for every plan and enforce the contract BEFORE scoring.

    Returns ``(exit_code, receipts, n_dispatched, message)``. On any
    violation the exit code is :data:`EXIT_CONTRACT` (non-zero) and the caller
    must write **no scores**. This function records receipts and logs nothing
    itself; the caller prints the receipts and the message. It runs no
    checkers and writes no scores.
    """
    receipts = record_dispatch_receipts(conn, plans)
    expected = len(plans)
    # Compute the dispatched count up front so the violation branch can report
    # it even when assert_dispatch_receipts raises before binding the return.
    n_dispatched = sum(1 for r in receipts if r.get("dispatched"))
    try:
        n_dispatched, n_receipts = assert_dispatch_receipts(
            receipts, expected, require_all_dispatched=require_all_dispatched
        )
    except DispatchReceiptError as e:
        msg = ("CONTRACT VIOLATION before scoring — %s. "
               "No scores written; exiting %d." % (e, EXIT_CONTRACT))
        return EXIT_CONTRACT, receipts, n_dispatched, msg
    msg = ("dispatch-receipt gate PASSED: %d/%d receipts, %d/%d dispatched "
           "before scoring." % (n_receipts, expected, n_dispatched, expected))
    return 0, receipts, n_dispatched, msg
