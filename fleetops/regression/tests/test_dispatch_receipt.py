"""Tests for fleetops.regression — the dispatch-receipt contract (t_00b9bd48).

The contract under test: a scoring run must record a dispatch receipt per
replay (board ground truth: a ``task_runs`` row) and assert the receipt count
== set size BEFORE any scoring; zero or short dispatch => non-zero exit and no
scores written. A dry run records one receipt per case (accounting gate),
creates nothing, and scores nothing.

The in-memory board fixtures are shaped like the live kanban schema
(``tasks`` + ``task_runs``) probed on the box. The zero-dispatch fixture is
the exact failure mode that produced t_dc281037 (0/5 replays dispatched, zero
``task_runs`` rows, yet a full score table written).
"""
import os

import pytest

from fleetops.regression.dispatch_receipt import (
    EXIT_CONTRACT,
    DispatchReceiptError,
    assert_dispatch_receipts,
    enforce_dispatch_gate,
    record_dispatch_receipts,
)
from fleetops.regression.run_regression import (
    EXIT_OK,
    emit_dry_run,
    parse_args,
)

SET_SIZE = 36
FROZEN_MANIFEST = (
    "/home/ubuntu/workspace/goals/"
    "prismatic-engine-hardening-and-portability/"
    "hidden_files/fleet-regression-set-v1.json"
)
REAL_BOARD = "/home/ubuntu/.hermes/kanban.db"


# ---------------------------------------------------------------------------
# In-memory board fixtures (shaped like the live kanban schema)
# ---------------------------------------------------------------------------

def make_board(tmp_path):
    """Create a read-only-style in-memory board DB with tasks + task_runs."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            body TEXT,
            assignee TEXT,
            status TEXT NOT NULL,
            idempotency_key TEXT
        )""")
    conn.execute("""
        CREATE TABLE task_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            profile TEXT,
            status TEXT NOT NULL,
            outcome TEXT,
            started_at INTEGER,
            ended_at INTEGER
        )""")
    conn.commit()
    return conn


def seed_replays(conn, n=SET_SIZE, with_runs=True, status="done"):
    """Seed n replays. Returns (plans, task_ids).

    with_runs=False reproduces the zero-dispatch failure: the task row exists
    (created) but the dispatcher never ran it (no task_runs row).
    """
    plans = []
    task_ids = []
    for i in range(n):
        tid = "t_%08x" % (0x1000 + i)
        key = "regr-v1-%s" % tid
        conn.execute(
            "INSERT INTO tasks (id,title,body,assignee,status,idempotency_key) "
            "VALUES (?,?,?,?,'done',?)",
            (tid, "regr- fixture %d" % i, "body", "kai", key))
        if with_runs:
            conn.execute(
                "INSERT INTO task_runs (task_id,profile,status,outcome,"
                "started_at,ended_at) VALUES (?,?, 'done','completed',1000,2000)",
                (tid, "kai"))
        plans.append({
            "idx": i,
            "original_task_id": tid,
            "title": "regr- fixture %d" % i,
            "body": "body",
            "key": key,
            "assignee": "kai",
            "family": "",
            "checker_command": "true",
        })
        task_ids.append(tid)
    conn.commit()
    return plans, task_ids


def count_tasks_runs(conn):
    import sqlite3
    t = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    r = conn.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0]
    return t, r


# ---------------------------------------------------------------------------
# The regression test: zero dispatch fails the contract before scoring
# ---------------------------------------------------------------------------

def test_zero_dispatch_fails_gate_before_scoring(tmp_path):
    """AC: with dispatch disabled, the checker exits non-zero and writes no scores.

    36 replays are CREATED (board rows exist) but the dispatcher never ran
    any of them (zero task_runs rows) — exactly t_dc281037. The gate must
    reject the run BEFORE any scoring.
    """
    conn = make_board(tmp_path)
    plans, _ = seed_replays(conn, with_runs=False)  # 0/36 dispatched

    rc, receipts, n_dispatched, msg = enforce_dispatch_gate(
        conn, plans, require_all_dispatched=True)

    # non-zero exit, loud, and explicitly no scores written
    assert rc == EXIT_CONTRACT
    assert rc != EXIT_OK
    assert n_dispatched == 0
    # every receipt is recorded (accounting is complete) but none dispatched
    assert len(receipts) == SET_SIZE
    assert all(r["run_rows"] == 0 and r["dispatched"] is False for r in receipts)
    assert "No scores written" in msg
    assert "dispatch" in msg.lower()


def test_zero_dispatch_assertion_raises(tmp_path):
    """Pure-function proof: zero dispatch trips the DispatchReceiptError."""
    conn = make_board(tmp_path)
    plans, _ = seed_replays(conn, with_runs=False)
    receipts = record_dispatch_receipts(conn, plans)
    with pytest.raises(DispatchReceiptError):
        assert_dispatch_receipts(receipts, SET_SIZE, require_all_dispatched=True)


def test_short_dispatch_fails_gate(tmp_path):
    """A run where only some replays were dispatched (e.g. 5/36) also fails."""
    conn = make_board(tmp_path)
    plans, _ = seed_replays(conn, with_runs=False)
    # dispatch exactly 5 (like the Nov-23 sample-5 run)
    for i in range(5):
        conn.execute(
            "INSERT INTO task_runs (task_id,profile,status,outcome,"
            "started_at,ended_at) VALUES (?,?, 'done','completed',1000,2000)",
            ("t_%08x" % (0x1000 + i), "kai"))
    conn.commit()
    rc, receipts, n_dispatched, msg = enforce_dispatch_gate(
        conn, plans, require_all_dispatched=True)
    assert rc == EXIT_CONTRACT
    assert n_dispatched == 5
    assert "5/36" in msg  # the shortfall is named in the gate output


# ---------------------------------------------------------------------------
# The full-dispatch case passes the contract gate
# ---------------------------------------------------------------------------

def test_full_dispatch_gate_passes(tmp_path):
    """When every replay was dispatched, the gate passes (rc 0) before scoring."""
    conn = make_board(tmp_path)
    plans, _ = seed_replays(conn, with_runs=True)  # 36/36 dispatched
    rc, receipts, n_dispatched, msg = enforce_dispatch_gate(
        conn, plans, require_all_dispatched=True)
    assert rc == EXIT_OK
    assert n_dispatched == SET_SIZE
    assert "PASSED" in msg


def test_receipt_count_mismatch_fails_accounting(tmp_path):
    """The accounting gate fails when receipts don't cover the set being run."""
    conn = make_board(tmp_path)
    plans, _ = seed_replays(conn, with_runs=True)
    receipts = record_dispatch_receipts(conn, plans)
    # deliberately under-count the expected set size
    with pytest.raises(DispatchReceiptError):
        assert_dispatch_receipts(receipts, SET_SIZE + 1,
                                 require_all_dispatched=False)


# ---------------------------------------------------------------------------
# Dry run: records receipts, passes the accounting gate, creates nothing,
# and scores nothing
# ---------------------------------------------------------------------------

def _dry_run_args(board_db, manifest):
    return parse_args(["--dry-run", "--board-db", board_db, "--manifest", manifest])


def test_dry_run_records_receipts_and_scores_nothing(tmp_path):
    """A dry run records one receipt per case, passes the accounting gate,
    exits 0, and does NOT mutate the board (creates nothing, scores nothing).
    """
    import sqlite3
    conn = make_board(tmp_path)
    plans, _ = seed_replays(conn, with_runs=True)
    board_path = str(tmp_path / "board.db")
    # materialize the in-memory board to a file for the runner's ro connect
    dst = sqlite3.connect(board_path)
    conn.backup(dst)
    dst.commit()
    dst.close()

    def file_board_counts():
        c = sqlite3.connect(board_path)
        t = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        r = c.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0]
        c.close()
        return t, r

    before = file_board_counts()

    args = _dry_run_args(board_path, "/nonexistent/manifest.json")
    # bypass manifest load: build a tiny valid manifest on disk
    import json
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps([
        {"task_id": p["original_task_id"], "title": "fixture %d" % p["idx"],
         "body": "body", "assignee_profile": "kai", "checker_command": "true"}
        for p in plans]))
    args = _dry_run_args(board_path, str(man))

    log = lambda m: None
    rc = emit_dry_run(plans, plans, str(man), args, log)

    assert rc == EXIT_OK
    # board file is unchanged: dry run created nothing and scored nothing
    assert file_board_counts() == before


def test_dry_run_real_frozen_set_records_36_receipts(capsys):
    """AC: a dry run over the frozen 36-case set records exactly 36 dispatch
    receipts before any scoring happens. Runs against the REAL frozen manifest
    and the REAL board DB (read-only), so it is the on-surface dry run.
    """
    if not os.path.isfile(FROZEN_MANIFEST):
        pytest.skip("frozen v1 manifest not present on this host")
    if not os.path.isfile(REAL_BOARD):
        pytest.skip("kanban board DB not present on this host")

    from fleetops.regression.run_regression import main, load_manifest, plan_replays
    args = parse_args(["--dry-run", "--board-db", REAL_BOARD,
                       "--manifest", FROZEN_MANIFEST])
    members, errors = load_manifest(FROZEN_MANIFEST)
    assert not errors, errors
    assert len(members) == SET_SIZE
    plans = plan_replays(members, args)

    log_lines = []
    rc = emit_dry_run(plans, members, FROZEN_MANIFEST, args, log_lines.append)
    captured = capsys.readouterr()
    log_all = "\n".join(log_lines)
    # 36 receipts recorded, accounting gate passed, nothing created/scored.
    # (The dispatched count against the live board is incidental and not
    # pinned here — the contract property is receipt-count == set size.)
    assert rc == EXIT_OK
    assert "36/36 receipts recorded" in log_all
    assert "dispatch-receipt accounting gate PASSED" in log_all
    assert "PLAN OK: 36/36" in captured.out
    assert "36 dispatch receipts recorded" in captured.out
    assert "nothing created, nothing scored" in captured.out


def test_dry_run_real_frozen_set_exits_ok():
    """Same as above but through the real entry point (main) — the exact
    command an operator runs on the box."""
    if not os.path.isfile(FROZEN_MANIFEST):
        pytest.skip("frozen v1 manifest not present on this host")
    if not os.path.isfile(REAL_BOARD):
        pytest.skip("kanban board DB not present on this host")
    from fleetops.regression.run_regression import main
    rc = main(["--dry-run", "--board-db", REAL_BOARD,
               "--manifest", FROZEN_MANIFEST])
    assert rc == EXIT_OK
