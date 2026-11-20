"""Tests for fleetops.classifier.

Fixtures are shaped like the live kanban rows probed on Nov 20, 2026:
timed_out runs carry cap-error text and no deliverable metadata; crashed
runs split on metadata (exit_kind / pid state / protocol markers) with
heartbeat-gap evidence; blocked tasks carry their reason in worker
comments.
"""
import json

from fleetops.classifier.classifier import (
    EXTERNAL_DEPENDENCY,
    INFRASTRUCTURE,
    TASK_EXECUTION,
    TASK_SHAPE,
    UNCLASSIFIED,
    classify_failures,
    classify_run,
    first_run_value_success,
)

_T = [1000]  # mutable clock for fixture rows


def task(tid="t1", assignee="kai", status="done", **kw):
    row = {"id": tid, "title": "fixture", "assignee": assignee, "status": status,
           "created_at": 100, "completed_at": 900, "body": "fixture body"}
    row.update(kw)
    return row


def run(tid="t1", outcome="completed", profile="kai", rid=None, **kw):
    _T[0] += 100
    row = {"id": rid if rid is not None else _T[0], "task_id": tid, "profile": profile,
           "status": "finished", "outcome": outcome, "started_at": _T[0],
           "ended_at": _T[0] + 50, "max_runtime_seconds": 7200, "metadata": None}
    row.update(kw)
    return row


def comment(tid="t1", author="orchestrator", body="", cid=None, created_at=None):
    _T[0] += 10
    return {"id": cid if cid is not None else _T[0], "task_id": tid, "author": author,
            "body": body, "created_at": created_at if created_at is not None else _T[0]}


def md(**kw):
    return json.dumps(kw)


# --- timed_out -------------------------------------------------------------

def test_timed_out_plain_is_task_shape():
    t = task(status="blocked")
    r = run(outcome="timed_out", metadata=md(error="run exceeded its 2h cap"))
    assert classify_run(r, t, [r], []) == TASK_SHAPE


def test_timed_out_with_endpoint_error_is_infrastructure():
    t = task(status="blocked")
    r = run(outcome="timed_out",
            metadata=md(error="vllm endpoint connection refused after 40 min"))
    assert classify_run(r, t, [r], []) == INFRASTRUCTURE


# --- crashed ---------------------------------------------------------------

def test_crashed_dead_worker_is_infrastructure():
    t = task(status="blocked")
    r = run(outcome="crashed", metadata=md(exit_kind="signal", pid_alive=False))
    assert classify_run(r, t, [r], []) == INFRASTRUCTURE


def test_crashed_protocol_violation_is_task_execution():
    t = task(status="blocked")
    r = run(outcome="crashed",
            metadata=md(protocol_violation=True, heartbeat_gap_seconds=53))
    assert classify_run(r, t, [r], []) == TASK_EXECUTION


def test_crashed_long_heartbeat_gap_is_infrastructure():
    t = task(status="blocked")
    r = run(outcome="crashed", metadata=md(heartbeat_gap_seconds=2400))
    assert classify_run(r, t, [r], []) == INFRASTRUCTURE


def test_crashed_protocol_with_long_gap_is_infrastructure():
    t = task(status="blocked")
    r = run(outcome="crashed",
            metadata=md(protocol_violation=True, heartbeat_gap_seconds=1800))
    assert classify_run(r, t, [r], []) == INFRASTRUCTURE


def test_crashed_no_evidence_is_unclassified():
    t = task(status="blocked")
    r = run(outcome="crashed", metadata=md(total_tokens=12345))
    assert classify_run(r, t, [r], []) == UNCLASSIFIED


# --- gave_up ---------------------------------------------------------------

def test_gave_up_is_task_execution():
    t = task(status="blocked")
    r = run(outcome="gave_up")
    assert classify_run(r, t, [r], []) == TASK_EXECUTION


def test_gave_up_with_infra_error_is_infrastructure():
    t = task(status="blocked")
    r = run(outcome="gave_up", metadata=md(error="tailscale tunnel outage"))
    assert classify_run(r, t, [r], []) == INFRASTRUCTURE


# --- blocked / external dependency ------------------------------------------

def test_blocked_waiting_on_decision_is_external():
    t = task(status="blocked")
    r = run(outcome="gave_up")
    cm = comment(author="kai", body="Blocked: waiting on a decision about pricing.")
    assert classify_run(r, t, [r], [cm]) == EXTERNAL_DEPENDENCY


def test_blocked_credential_wait_is_external():
    t = task(status="blocked")
    r = run(outcome="timed_out")
    cm = comment(author="kai", body="Cannot proceed: login required, credential missing.")
    assert classify_run(r, t, [r], [cm]) == EXTERNAL_DEPENDENCY


def test_regression_approved_copy_is_not_external():
    t = task(status="blocked")
    r = run(outcome="timed_out", metadata=md(error="run exceeded its 2h cap"))
    cm = comment(author="kai", body="Draft uses the approved copy from the brief.")
    assert classify_run(r, t, [r], [cm]) == TASK_SHAPE


def test_regression_not_a_credentials_issue_is_not_external():
    t = task(status="blocked")
    r = run(outcome="gave_up")
    cm = comment(author="kai", body="Stopped: this is not a credentials issue, "
                                    "the scope needs two deliverables split.")
    assert classify_run(r, t, [r], [cm]) == TASK_EXECUTION


# --- completed runs and verdicts ---------------------------------------------

def test_completed_with_reviewer_fail_is_task_execution():
    t = task(status="done")
    r = run(outcome="completed")
    cm = comment(author="orchestrator", body="VERDICT: FAIL — deliverable incomplete.")
    assert classify_run(r, t, [r], [cm]) == TASK_EXECUTION


def test_completed_with_reviewer_pass_is_not_failed():
    t = task(status="done")
    r = run(outcome="completed")
    cm = comment(author="muse", body="VERDICT: PASS")
    assert classify_run(r, t, [r], [cm]) is None


def test_worker_self_verdict_does_not_count():
    t = task(status="done")
    r = run(outcome="completed", profile="kai")
    cm = comment(author="kai", body="VERDICT: FAIL")
    assert classify_run(r, t, [r], [cm]) is None


def test_verdict_clean_counts_as_pass():
    t = task(status="done")
    r = run(outcome="completed")
    cm = comment(author="orchestrator", body="VERDICT CLEAN, 4/4 checks pass.")
    result = first_run_value_success([t], [r], [cm])
    assert result["numerator"] == 1 and result["denominator"] == 1
    assert result["rate"] == 100.0


# --- splits ------------------------------------------------------------------

def test_classify_failures_splits_sum_and_first_runs():
    t1 = task("t1", status="blocked")
    r1 = run("t1", outcome="timed_out", rid=1, started_at=100,
             metadata=md(error="run exceeded its 2h cap"))
    r2 = run("t1", outcome="completed", rid=2, started_at=200)
    t2 = task("t2", assignee="george", status="done")
    r3 = run("t2", outcome="completed", rid=3, started_at=100, profile="george")
    cm = comment("t2", author="orchestrator", body="VERDICT: FAIL")
    out = classify_failures([t1, t2], [r1, r2, r3], [cm],
                            profiles={"kai", "george", "ned", "fred"})
    allr = out["all_runs"]
    assert allr["total_failed"] == 2
    assert sum(allr["counts"].values()) == allr["total_failed"]
    assert allr["counts"][TASK_SHAPE] == 1
    assert allr["counts"][TASK_EXECUTION] == 1
    assert allr["task_caused_share"] == 100.0
    first = out["first_runs"]
    assert first["total_failed"] == 2  # r1 (t1 first) and r3 (t2 first)


def test_orchestrator_folds_into_fred_population():
    t = task("t9", assignee="orchestrator", status="blocked")
    r = run("t9", outcome="timed_out", rid=1, started_at=100,
            metadata=md(error="run exceeded its 2h cap"))
    out = classify_failures([t], [r], [], profiles={"kai", "george", "ned", "fred"})
    assert out["all_runs"]["total_failed"] == 1


def test_population_filter_excludes_other_assignees():
    t = task("t9", assignee="muse", status="blocked")
    r = run("t9", outcome="timed_out", rid=1, started_at=100,
            metadata=md(error="run exceeded its 2h cap"))
    out = classify_failures([t], [r], [], profiles={"kai", "george", "ned", "fred"})
    assert out["all_runs"]["total_failed"] == 0


# --- value success -------------------------------------------------------------

def test_value_success_rate_and_coverage_are_separate():
    tasks, runs, comments = [], [], []
    # t1: first run completed + PASS -> numerator + denominator
    tasks.append(task("t1"))
    runs.append(run("t1", outcome="completed", rid=11, started_at=100))
    comments.append(comment("t1", author="orchestrator", body="VERDICT: PASS"))
    # t2: first run completed + FAIL -> denominator only
    tasks.append(task("t2"))
    runs.append(run("t2", outcome="completed", rid=12, started_at=100))
    comments.append(comment("t2", author="muse", body="VERDICT: FAIL"))
    # t3: first run completed, NO verdict -> neither; hurts coverage only
    tasks.append(task("t3"))
    runs.append(run("t3", outcome="completed", rid=13, started_at=100))
    # t4: first run timed out -> neither numerator nor denominator
    tasks.append(task("t4"))
    runs.append(run("t4", outcome="timed_out", rid=14, started_at=100))
    result = first_run_value_success(tasks, runs, comments)
    assert result["numerator"] == 1
    assert result["denominator"] == 2
    assert result["rate"] == 50.0
    assert result["done_tasks"] == 4
    assert result["verdict_tasks"] == 2
    assert result["verdict_coverage"] == 50.0
