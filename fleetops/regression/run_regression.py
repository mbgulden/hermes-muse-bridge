#!/usr/bin/env python3
"""Fleet regression runner (ported into the repo as ``fleetops.regression``).

Replays the frozen fleet regression set manifest as new 'regr-' kanban tasks,
polls each replay to completion, and scores each replay by running the
entry's checker_command.

Scoring policy: the runner reports ONLY checker exit codes. It makes no
judgment of replay output quality.

Dispatch-receipt contract (the fix, t_00b9bd48)
------------------------------------------------
The original standalone runner (``/home/ubuntu/work/fleet-top5/regression-
runner/run_regression.py``) scored every planned replay from its
``checker_command`` exit code with **no assertion that the replay was actually
dispatched before scoring**. A replay the dispatcher never picks up (cap full)
stayed ``ready``, hit ``timed_out_waiting``, and was still scored against the
original task's pre-existing deliverables — 0/N replays dispatched yet a full
score table was written (t_dc281037: 0/5 dispatched, zero ``task_runs`` rows,
yet 4/4 PASS).

This port inserts a dispatch-receipt gate (see ``dispatch_receipt.py``):

* A scoring run records a dispatch receipt per replay — board ground truth, a
  ``task_runs`` row for the replay task — and asserts, BEFORE any scoring,
  that the receipt count equals the set size AND that every replay was
  dispatched. Zero or short dispatch => loud non-zero exit (``EXIT_CONTRACT``)
  and NO scores written (no checker runs, no results.json).
* A ``--dry-run`` records one receipt per case, asserts the receipt count
  equals the set size (accounting gate), and creates nothing and scores
  nothing.

Modes:
  --dry-run   Validate the manifest, record the dispatch receipts (read-only),
              assert the accounting gate, print the plan, create NOTHING,
              score nothing. Exit 0 on a valid manifest + complete receipts.
  full        Create replays (regr- titles, regr-v1- idempotency keys),
              poll the board to completion, enforce the dispatch-receipt gate
              BEFORE scoring, run each checker_command, write results.json +
              a summary table.

Ground truth for replay state is the kanban board SQLite DB (read-only
URI) — the runner never trusts its own stdout for board state.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

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

HERMES_HOME = "/home/ubuntu/.hermes"
HERMES_BIN = "hermes"
BOARD_DB = "/home/ubuntu/.hermes/kanban.db"
DEFAULT_MANIFEST = (
    "/home/ubuntu/workspace/goals/"
    "prismatic-engine-hardening-and-portability/"
    "hidden_files/fleet-regression-set-v1.json"
)
REQUIRED_FIELDS = ("task_id", "title", "body", "assignee_profile", "checker_command")
REPLAY_TITLE_PREFIX = "regr- "
REPLAY_KEY_PREFIX = "regr-v1-"
MAX_RUNTIME = 7200
CREATED_BY = "regression-runner"
TERMINAL_STATUSES = {"done", "blocked", "archived"}
ACTIVE_STATUSES = {"ready", "running", "todo", "triage"}
EXIT_OK = 0
EXIT_VALIDATION = 2


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Replay the fleet regression manifest on the kanban board "
                    "and score replays by checker exit codes, gated on a "
                    "dispatch receipt per replay (no scores without dispatch).")
    p.add_argument("--manifest", default=DEFAULT_MANIFEST,
                   help="path to the frozen v1 manifest JSON (default: %(default)s)")
    p.add_argument("--dry-run", action="store_true",
                   help="validate the manifest, record the dispatch receipts "
                        "(read-only), assert the accounting gate, print the "
                        "replay plan, create nothing, score nothing")
    p.add_argument("--limit", type=int, default=None,
                   help="replay only the first N members")
    p.add_argument("--profile-override", default=None,
                   help="assignee profile used for every replay "
                        "(overrides each entry's assignee_profile)")
    p.add_argument("--results-out", default=None,
                   help="path for results.json (default: results.json next to this script)")
    p.add_argument("--board-db", default=BOARD_DB,
                   help="kanban board SQLite path (default: %(default)s)")
    p.add_argument("--hermes-home", default=HERMES_HOME,
                   help="HERMES_HOME for the `hermes kanban create` subprocess")
    p.add_argument("--hermes-bin", default=HERMES_BIN,
                   help="hermes CLI binary (default: %(default)s)")
    p.add_argument("--poll-interval", type=int, default=60,
                   help="seconds between board polls (default: %(default)s)")
    p.add_argument("--replay-timeout", type=int,
                   default=MAX_RUNTIME * 3,
                   help="max seconds to wait for one replay to reach a terminal "
                        "status (default: %(default)s = 3x max-runtime)")
    p.add_argument("--checker-timeout", type=int, default=1800,
                   help="max seconds for one checker_command (default: %(default)s)")
    return p.parse_args(argv)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path):
    """Load + validate the manifest. Returns (members, errors)."""
    errors = []
    if not os.path.isfile(path):
        return None, ["manifest not found: %s" % path]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return None, ["manifest unreadable/invalid JSON: %s" % e]
    if not isinstance(data, list):
        return None, ["manifest top level must be a JSON list, got %s" % type(data).__name__]
    if not data:
        return None, ["manifest is empty"]
    seen = set()
    for i, e in enumerate(data):
        if not isinstance(e, dict):
            errors.append("member %d: not an object" % i)
            continue
        for field in REQUIRED_FIELDS:
            v = e.get(field)
            if not isinstance(v, str) or not v.strip():
                errors.append("member %d (%s): missing/empty field %r"
                              % (i, e.get("task_id", "?"), field))
        tid = e.get("task_id")
        if isinstance(tid, str):
            if tid in seen:
                errors.append("member %d: duplicate task_id %s" % (i, tid))
            seen.add(tid)
    return data, errors


def plan_replays(members, args):
    """Build the replay plan: one entry per manifest member."""
    plans = []
    sel = members if args.limit is None else members[: args.limit]
    for idx, m in enumerate(sel):
        plans.append({
            "idx": idx,
            "original_task_id": m["task_id"],
            "title": REPLAY_TITLE_PREFIX + m["title"],
            "body": m["body"],
            "key": REPLAY_KEY_PREFIX + m["task_id"],
            "assignee": args.profile_override or m["assignee_profile"],
            "family": m.get("family", ""),
            "checker_command": m["checker_command"],
        })
    return plans


def _print_receipts(receipts, log):
    """Print one line per dispatch receipt (board ground truth)."""
    log("DISPATCH RECEIPTS (one per case; dispatched = has a task_runs row):")
    for r in receipts:
        log("  %-24s replay=%-12s status=%-8s run_rows=%-3d dispatched=%s"
            % (r["key"], r["replay_task_id"] or "-",
               r["board_status"] or "-", r["run_rows"],
               "YES" if r["dispatched"] else "no"))


def emit_dry_run(plans, members, manifest_path, args, log):
    """Validate + record dispatch receipts (read-only). Create nothing, score nothing.

    The dry run enforces ONLY the accounting gate: one receipt per case in the
    set being run (receipt count == set size). It does NOT require dispatch
    (a dry run creates nothing, so on a fresh board 0 dispatched is expected
    and is reported, not a failure). No checker is run and no scores are
    written.
    """
    print("FLEET REGRESSION DRY RUN")
    print("manifest:        %s" % manifest_path)
    print("manifest sha256: %s" % sha256_of(manifest_path))
    print("members:         %d | selected: %d%s"
          % (len(members), len(plans),
             "" if args.limit is None else " (limit %d)" % args.limit))
    print("profile-override: %s" % (args.profile_override or "(per-member assignee)"))
    print("replay policy:   title prefix %r, idempotency key prefix %r, "
          "max-runtime %ds, created-by %r"
          % (REPLAY_TITLE_PREFIX, REPLAY_KEY_PREFIX, MAX_RUNTIME, CREATED_BY))

    # --- dispatch-receipt accounting gate (read-only, before any scoring) ---
    conn = board_connect(args.board_db)
    receipts = record_dispatch_receipts(conn, plans)
    expected = len(plans)
    _print_receipts(receipts, log)
    try:
        n_dispatched, n_receipts = assert_dispatch_receipts(
            receipts, expected, require_all_dispatched=False)
    except DispatchReceiptError as e:
        log("CONTRACT VIOLATION (dry run, accounting gate): %s. "
            "No scores written; exiting %d." % (e, EXIT_CONTRACT))
        return EXIT_CONTRACT
    log("dispatch-receipt accounting gate PASSED: %d/%d receipts recorded, "
        "%d/%d dispatched (dry run does not require dispatch)."
        % (n_receipts, expected, n_dispatched, expected))

    print()
    print("%4s  %11s  %8s  %14s  %20s  %s"
          % ("idx", "original_id", "assignee", "family", "idempotency_key", "replay title"))
    for p in plans:
        print("%4d  %11s  %8s  %14s  %20s  %s"
              % (p["idx"], p["original_task_id"], p["assignee"], p["family"],
                 p["key"], p["title"][:64]))
    print()
    covered = len(plans)
    total = min(args.limit, len(members)) if args.limit is not None else len(members)
    print("PLAN OK: %d/%d selected members covered by the replay plan; "
          "%d dispatch receipts recorded; nothing created, nothing scored "
          "(dry-run)." % (covered, total, len(receipts)))
    return EXIT_OK


def extract_id(data):
    """Pull a task id out of `hermes kanban create --json` output, whatever
    the wrapper shape is. Returns None if nothing looks like a task id."""
    if isinstance(data, str):
        s = data.strip()
        return s if s.startswith("t_") else None
    if isinstance(data, dict):
        for k in ("id", "task_id"):
            v = data.get(k)
            if isinstance(v, str) and v.startswith("t_"):
                return v
        for v in data.values():
            r = extract_id(v)
            if r:
                return r
    if isinstance(data, list):
        for v in data:
            r = extract_id(v)
            if r:
                return r
    return None


def create_replay(plan, args, conn, log):
    """Create one replay task. Returns (task_id, note). Ground truth is the
    board (idempotency-key lookup), not the CLI stdout."""
    cmd = [args.hermes_bin, "kanban", "create", plan["title"],
           "--assignee", plan["assignee"],
           "--max-runtime", str(MAX_RUNTIME),
           "--idempotency-key", plan["key"],
           "--created-by", CREATED_BY,
           "--body", plan["body"],
           "--json"]
    env = dict(os.environ, HERMES_HOME=args.hermes_home)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
    except (subprocess.TimeoutExpired, OSError) as e:
        return None, "create subprocess failed: %s" % e
    if r.returncode != 0:
        return None, "create failed rc=%d: %s" % (r.returncode, (r.stderr or r.stdout)[:300])
    json_id = None
    try:
        json_id = extract_id(json.loads(r.stdout))
    except (json.JSONDecodeError, ValueError):
        pass
    board_id = board_find_by_key(conn, plan["key"])
    if board_id:
        return board_id, "created (board-confirmed via idempotency key)"
    if json_id:
        return json_id, "created (json id; board lookup pending — dispatcher may not have committed yet)"
    return None, "create rc=0 but no id in output and no board row: %s" % r.stdout[:200]


def wait_all_terminal(plans_state, args, conn, log):
    """Poll the board until every in-flight replay reaches a terminal status
    or its per-replay timeout. Mutates plans_state in place."""
    now = time.time()
    for p in plans_state:
        if p.get("task_id") and p.get("status") not in TERMINAL_STATUSES and "deadline" not in p:
            p["deadline"] = now + args.replay_timeout
    while True:
        pending = []
        for p in plans_state:
            tid = p.get("task_id")
            if not tid:
                continue
            if p.get("status") in TERMINAL_STATUSES:
                continue
            status, outcome = board_task_state(conn, tid)
            if status is None:
                p["status"] = "missing"
                p["outcome"] = "missing"
                log("  ! %s: task row disappeared from board" % p["key"])
                continue
            p["status"] = status
            p["outcome"] = outcome
            if status in TERMINAL_STATUSES:
                log("  done %s -> %s (run outcome: %s)" % (tid, status, outcome))
            elif time.time() > p.get("deadline", float("inf")):
                p["timed_out_waiting"] = True
                log("  ! %s: replay-timeout, still %s; NOT scored until the "
                    "dispatch-receipt gate proves it was dispatched" % (tid, status))
            else:
                pending.append(p)
        if not pending:
            return
        time.sleep(max(1, args.poll_interval))


def board_task_state(conn, task_id):
    """Return (status, latest_run_outcome) for a task."""
    rows = conn.execute("""
        SELECT t.status,
               (SELECT r.outcome FROM task_runs r
                 WHERE r.task_id = t.id ORDER BY r.id DESC LIMIT 1)
        FROM tasks t WHERE t.id = ?""", (task_id,)).fetchall()
    if not rows:
        return None, None
    return rows[0][0], rows[0][1]


def run_checker(cmd, timeout, log):
    """Run a member's checker_command. Returns (exit_code, timed_out).
    The exit code IS the score — the runner adds no judgment of its own.

    NOTE: shell=True is intentional here — checker_command is a shell
    string frozen in the v1 manifest (test/grep/bash pipelines), not a
    user-supplied injection vector at this trust boundary; the manifest is
    the contract.
    """
    try:
        r = subprocess.run(cmd, shell=True, executable="/bin/bash",
                           capture_output=True, text=True, timeout=timeout,
                           cwd=None)
        log("    checker exit %d (stdout: %s | stderr: %s)"
            % (r.returncode, (r.stdout or "").strip()[:120], (r.stderr or "").strip()[:120]))
        return r.returncode, False
    except subprocess.TimeoutExpired:
        log("    checker TIMED OUT after %ds (scored as exit 124)" % timeout)
        return 124, True
    except OSError as e:
        log("    checker could not run: %s (scored as exit 127)" % e)
        return 127, False


# ---------------------------------------------------------------------------
# Full mode
# ---------------------------------------------------------------------------

def run_full(plans, args, log):
    conn = board_connect(args.board_db)
    started = utcnow()
    results = {
        "runner": "fleetops.regression (ported; dispatch-receipt contract, t_00b9bd48)",
        "manifest": args.manifest,
        "manifest_sha256": sha256_of(args.manifest),
        "started_utc": started,
        "finished_utc": None,
        "mode": "full",
        "limit": args.limit,
        "profile_override": args.profile_override,
        "members": [],
        "summary": {},
    }

    log("CREATING %d replays..." % len(plans))
    for p in plans:
        tid, note = create_replay(p, args, conn, log)
        p["task_id"] = tid
        p["create_note"] = note
        p["create_ok"] = tid is not None
        log("  %s -> %s (%s)" % (p["key"], tid, note))

    log("POLLING board for completion (interval %ds, per-replay timeout %ds)..."
        % (args.poll_interval, args.replay_timeout))
    wait_all_terminal(plans, args, conn, log)

    # ------------------------------------------------------------------
    # DISPATCH-RECEIPT GATE — BEFORE any scoring.
    # Record a receipt per replay (board ground truth: a task_runs row) and
    # assert the receipt count == set size AND that every replay was
    # dispatched. Zero or short dispatch => loud non-zero exit and NO scores
    # written (no checker runs, no results.json).
    # ------------------------------------------------------------------
    log("ENFORCING dispatch-receipt gate BEFORE scoring...")
    rc, receipts, n_dispatched, gate_msg = enforce_dispatch_gate(
        conn, plans, require_all_dispatched=True)
    _print_receipts(receipts, log)
    log(gate_msg)
    if rc != EXIT_OK:
        log("ABORTING: dispatch-receipt contract violated. No checkers run, "
            "no scores written, no results.json.")
        conn.close()
        return rc
    log("gate PASSED (%d/%d dispatched); proceeding to score."
        % (n_dispatched, len(plans)))

    log("RUNNING checkers...")
    for p in plans:
        exit_code, timed_out = run_checker(p["checker_command"], args.checker_timeout, log)
        p["checker_exit"] = exit_code
        p["checker_timed_out"] = timed_out
        p["first_attempt_pass"] = (exit_code == 0)

    members_out = []
    for p in plans:
        members_out.append({
            "idx": p["idx"],
            "original_task_id": p["original_task_id"],
            "replay_task_id": p["task_id"],
            "replay_title": p["title"],
            "idempotency_key": p["key"],
            "assignee": p["assignee"],
            "family": p["family"],
            "create_ok": p.get("create_ok", False),
            "create_note": p.get("create_note"),
            "final_status": p.get("status"),
            "run_outcome": p.get("outcome"),
            "timed_out_waiting": p.get("timed_out_waiting", False),
            "dispatch_receipt": {
                "replay_task_id": next((r["replay_task_id"] for r in receipts
                                        if r["key"] == p["key"]), None),
                "run_rows": next((r["run_rows"] for r in receipts
                                  if r["key"] == p["key"]), 0),
                "dispatched": next((r["dispatched"] for r in receipts
                                    if r["key"] == p["key"]), False),
            },
            "checker_command": p["checker_command"],
            "checker_exit": p.get("checker_exit"),
            "checker_timed_out": p.get("checker_timed_out", False),
            "first_attempt_pass": p.get("first_attempt_pass"),
        })
    results["members"] = members_out
    results["dispatch_receipts"] = {
        "expected": len(plans),
        "recorded": len(receipts),
        "dispatched": n_dispatched,
        "gate": "pass",
    }
    results["summary"] = {
        "total": len(members_out),
        "created": sum(1 for m in members_out if m["create_ok"]),
        "dispatched": n_dispatched,
        "first_attempt_pass": sum(1 for m in members_out if m["first_attempt_pass"]),
        "first_attempt_fail": sum(1 for m in members_out
                                  if m["create_ok"] and not m["first_attempt_pass"]),
    }
    results["finished_utc"] = utcnow()

    out_path = args.results_out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    log("results written to %s" % out_path)

    # Summary table
    print()
    print("SUMMARY (checker exit codes only — no quality judgment by the runner; "
          "scores are only valid because the dispatch-receipt gate passed)")
    print("%4s  %11s  %11s  %8s  %12s  %8s  %6s  %s"
          % ("idx", "original_id", "replay_id", "status", "run_outcome",
             "checker", "pass", "family"))
    for m in members_out:
        print("%4d  %11s  %11s  %8s  %12s  %8s  %6s  %s"
              % (m["idx"], m["original_task_id"], m["replay_task_id"] or "-",
                 m["final_status"] or "-", m["run_outcome"] or "-",
                 m["checker_exit"] if m["checker_exit"] is not None else "-",
                 "PASS" if m["first_attempt_pass"] else "FAIL",
                 m["family"]))
    s = results["summary"]
    print()
    print("total=%d created=%d dispatched=%d first_attempt_pass=%d first_attempt_fail=%d"
          % (s["total"], s["created"], s["dispatched"],
             s["first_attempt_pass"], s["first_attempt_fail"]))

    # Runner health, not replay scoring: the runner itself failed if it could
    # not create any replay it was asked to create. (Dispatch is enforced
    # separately by the gate above, so it already aborted before scoring.)
    if s["created"] < s["total"]:
        log("WARNING: %d/%d replays could not be created" % (s["created"], s["total"]))
        return 1
    return EXIT_OK


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    log = lambda msg: print("[run_regression %s] %s" % (utcnow(), msg), file=sys.stderr)

    members, errors = load_manifest(args.manifest)
    if errors:
        for e in errors:
            log("MANIFEST ERROR: %s" % e)
        return EXIT_VALIDATION

    plans = plan_replays(members, args)
    if args.dry_run:
        return emit_dry_run(plans, members, args.manifest, args, log)
    return run_full(plans, args, log)


if __name__ == "__main__":
    sys.exit(main())
