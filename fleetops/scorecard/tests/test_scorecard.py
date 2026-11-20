"""Tests for fleetops.scorecard.

Fixture snapshots are plain row dicts in the classifier's shapes.
The main fixture is hand-computed: every expected number below was
derived from the fixture rows by hand, not from the code. A separate
consistency test pins the scorecard's classifier-backed sections to
the classifier's own output on the same fixture, so the packaged
definitions cannot drift apart.
"""
import json

from fleetops.classifier.classifier import (
    TASK_EXECUTION,
    TASK_SHAPE,
    UNCLASSIFIED,
    classify_failures,
    first_run_value_success,
)
from fleetops.scorecard.scorecard import build_scorecard, render_markdown, task_class

NOW = 1_800_000_000
DAY = 86400


def task(tid, assignee="kai", status="done", completed_at=None, **kw):
    row = {"id": tid, "title": f"fixture {tid}", "assignee": assignee,
           "status": status, "created_at": NOW - 900000,
           "completed_at": completed_at, "body": "fixture body"}
    row.update(kw)
    return row


def run(tid, outcome="completed", profile="kai", rid=1, started_at=NOW - 2000,
        ended_at=NOW - 1000, **kw):
    row = {"id": rid, "task_id": tid, "profile": profile, "status": "finished",
           "outcome": outcome, "started_at": started_at, "ended_at": ended_at,
           "max_runtime_seconds": 7200, "metadata": None}
    row.update(kw)
    return row


def comment(tid, author="orchestrator", body="", cid=1, created_at=NOW - 500):
    return {"id": cid, "task_id": tid, "author": author, "body": body,
            "created_at": created_at}


def md(**kw):
    return json.dumps(kw)


def main_snapshot():
    tasks = [
        task("t_pass", completed_at=NOW - 1000),
        task("t_fail", assignee="george", completed_at=NOW - 1000),
        task("t_nov", assignee="ned", completed_at=NOW - 1000),
        task("t_timeout", assignee="kai", status="blocked"),
        task("t_unclass", assignee="george", status="blocked"),
        task("t_old", assignee="ned", status="blocked"),
    ]
    runs = [
        run("t_pass", rid=1, started_at=NOW - 2000, ended_at=NOW - 1000,
            metadata=md(input_tokens=1_000_000, output_tokens=100_000,
                        total_tokens=1_100_000)),
        run("t_fail", profile="george", rid=2, started_at=NOW - 3000,
            ended_at=NOW - 2000),
        run("t_nov", profile="ned", rid=3, started_at=NOW - 5000,
            ended_at=NOW - 4000),
        run("t_timeout", outcome="timed_out", rid=4, started_at=NOW - 4000,
            ended_at=NOW - 1000, metadata=md(error="run exceeded its 2h cap")),
        run("t_unclass", outcome="crashed", profile="george", rid=5,
            started_at=NOW - 2000, ended_at=NOW - 1500,
            metadata=md(note="no usable evidence")),
        run("t_old", outcome="timed_out", profile="ned", rid=6,
            started_at=NOW - 700000, ended_at=NOW - 696400,
            metadata=md(error="run exceeded its 2h cap")),
    ]
    comments = [
        comment("t_pass", body="VERDICT: PASS", cid=1),
        comment("t_fail", author="muse", body="VERDICT: FAIL", cid=2),
    ]
    return {"tasks": tasks, "runs": runs, "comments": comments}


def build(snapshot, **config):
    config.setdefault("now", NOW)
    return build_scorecard(snapshot, config)


# --- value success + coverage ------------------------------------------------

def test_value_success_and_coverage_on_main_fixture():
    extract = build(main_snapshot())
    value = extract["first_run_value_success"]["all_time"]
    assert value["numerator"] == 1
    assert value["denominator"] == 2
    assert value["covered_tasks"] == 2
    assert value["rate"] == 50.0
    assert value["done_tasks"] == 3
    assert value["total_done"] == 3
    assert value["verdict_tasks"] == 2
    assert value["verdict_coverage"] == 66.7
    coverage = extract["verdict_coverage"]
    assert coverage == {"rate": 66.7, "verdict_tasks": 2, "done_tasks": 3}


def test_board_with_no_verdicts_has_null_rate_and_zero_coverage():
    snapshot = {
        "tasks": [task("t1", completed_at=NOW - 1000)],
        "runs": [run("t1", rid=1)],
        "comments": [],
    }
    extract = build(snapshot)
    value = extract["first_run_value_success"]["all_time"]
    assert value["rate"] is None
    assert value["denominator"] == 0
    assert value["done_tasks"] == 1
    assert value["verdict_coverage"] == 0.0


# --- failure-cause split -------------------------------------------------------

def test_failure_split_counts_shares_and_task_caused():
    extract = build(main_snapshot())
    first = extract["failure_causes"]["all_time"]["first_runs"]
    assert first["total_failed"] == 4
    assert first["counts"][TASK_SHAPE] == 2
    assert first["counts"][TASK_EXECUTION] == 1
    assert first["counts"][UNCLASSIFIED] == 1
    assert sum(first["counts"].values()) == first["total_failed"]
    assert first["shares"][TASK_SHAPE] == 50.0
    assert first["shares"][UNCLASSIFIED] == 25.0
    assert first["task_caused_share"] == 75.0
    all_runs = extract["failure_causes"]["all_time"]["all_runs"]
    assert all_runs["total_failed"] == 4
    assert sum(all_runs["counts"].values()) == 4


def test_window_split_excludes_runs_that_started_before_the_window():
    extract = build(main_snapshot())
    first = extract["failure_causes"]["window"]["first_runs"]
    assert first["total_failed"] == 3  # t_old's first run predates the window
    assert first["counts"][TASK_SHAPE] == 1
    all_runs = extract["failure_causes"]["window"]["all_runs"]
    assert all_runs["total_failed"] == 3


def test_unclassified_stays_its_own_slice():
    snapshot = {
        "tasks": [task("t1", status="blocked")],
        "runs": [run("t1", outcome="crashed", rid=1,
                     metadata=md(total_tokens=0))],
        "comments": [],
    }
    extract = build(snapshot)
    first = extract["failure_causes"]["all_time"]["first_runs"]
    assert first["total_failed"] == 1
    assert first["counts"][UNCLASSIFIED] == 1
    assert first["shares"][UNCLASSIFIED] == 100.0
    assert first["task_caused_share"] == 0.0


def test_classifier_overlap_consistency_on_shared_fixture():
    snapshot = main_snapshot()
    extract = build(snapshot)
    expected_failures = classify_failures(
        snapshot["tasks"], snapshot["runs"], snapshot["comments"], profiles=None)
    for scope in ("first_runs", "all_runs"):
        got = extract["failure_causes"]["all_time"][scope]
        want = expected_failures[scope]
        assert got["total_failed"] == want["total_failed"]
        assert got["counts"] == want["counts"]
        assert got["task_caused_share"] == want["task_caused_share"]
    expected_value = first_run_value_success(
        snapshot["tasks"], snapshot["runs"], snapshot["comments"], profiles=None)
    got_value = extract["first_run_value_success"]["all_time"]
    assert got_value["rate"] == expected_value["rate"]
    assert got_value["numerator"] == expected_value["numerator"]
    assert got_value["denominator"] == expected_value["denominator"]
    assert got_value["verdict_coverage"] == expected_value["verdict_coverage"]
    assert got_value["done_tasks"] == expected_value["done_tasks"]


# --- timed-out hours + completions ----------------------------------------------

def test_timed_out_hours_share_window_and_all_time():
    extract = build(main_snapshot())
    window = extract["timed_out"]["window"]
    # finished in window: 1000+1000+1000+3000+500 = 6500s; timed out 3000s
    assert window["finished_runs"] == 5
    assert window["timed_out_hours"] == 0.83
    assert window["share_pct"] == 46.2
    all_time = extract["timed_out"]["all_time"]
    assert all_time["share_pct"] == 65.3  # +3600s timed out on t_old
    assert extract["completions"]["window_tasks_done"] == 3
    assert extract["completions"]["window_completed_runs"] == 3


# --- estimate calibration ---------------------------------------------------------

def estimate_snapshot():
    def est_task(tid, title, body, rid, seconds):
        return (
            task(tid, title=title, body=body, completed_at=NOW - 100),
            run(tid, rid=rid, started_at=NOW - 10000, ended_at=NOW - 10000 + seconds),
        )

    pairs = [
        est_task("e_build", "port-thing: build the widget",
                 "Human-equivalent estimate: ~30 min", 1, 3600),   # 60/30 = 2.0
        est_task("e_drafts", "Draft the launch email",
                 "Human-equivalent estimate: ~20 min", 2, 1800),   # 30/20 = 1.5
        est_task("e_research", "Research competitor pricing",
                 "Human-equivalent estimate: ~10 min", 3, 1800),   # 30/10 = 3.0
        est_task("e_explicit", "Misc thing",
                 "Work type: build\nHuman-equivalent estimate: ~15 min",
                 4, 900),                                          # 15/15 = 1.0
        est_task("e_other", "ops- check the thing",
                 "Human-equivalent estimate: ~40 min", 5, 2400),   # 40/40 = 1.0
    ]
    tasks = [p[0] for p in pairs] + [task("e_none", completed_at=NOW - 100)]
    runs = [p[1] for p in pairs] + [run("e_none", rid=6)]
    return {"tasks": tasks, "runs": runs, "comments": []}


def test_estimate_calibration_per_class():
    extract = build(estimate_snapshot())
    est = extract["estimate_calibration"]
    assert est["tasks_with_estimate"] == 5
    assert est["classes"]["build"] == {"n": 2, "median_overrun": 1.5}
    assert est["classes"]["drafts"] == {"n": 1, "median_overrun": 1.5}
    assert est["classes"]["research"] == {"n": 1, "median_overrun": 3.0}
    assert est["classes"]["other"] == {"n": 1, "median_overrun": 1.0}
    assert est["overall"] == {"n": 5, "median_overrun": 1.5}


def test_task_class_derivation():
    assert task_class({"title": "port-x: thing", "body": ""}) == "build"
    assert task_class({"title": "Draft the note", "body": ""}) == "drafts"
    assert task_class({"title": "Survey the market", "body": ""}) == "research"
    assert task_class({"title": "ops- routine check", "body": ""}) == "other"
    # an explicit declaration beats title signals
    assert task_class({"title": "Draft the note",
                       "body": "Work type: research"}) == "research"


# --- token accounting --------------------------------------------------------------

def token_snapshot():
    tasks = [task("k1", completed_at=NOW - 100), task("k2", completed_at=NOW - 100)]
    runs = [
        run("k1", rid=1, started_at=NOW - 2000, ended_at=NOW - 1000,
            metadata=md(input_tokens=1_800_000, output_tokens=200_000,
                        total_tokens=2_000_000)),
        run("k2", rid=2, started_at=NOW - 2000, ended_at=NOW - 1000),
    ]
    return {"tasks": tasks, "runs": runs, "comments": []}


RATES = {"reference_model": "test-model", "input_per_million": 2.0,
         "output_per_million": 10.0}


def test_token_accounting_recorded_and_estimated_stay_separate():
    extract = build(token_snapshot(), rate_table=RATES)
    tok = extract["tokens"]["all_time"]
    assert tok["recorded_input_tokens"] == 1_800_000
    assert tok["recorded_output_tokens"] == 200_000
    # unrecorded run estimated from the recorded median (2,000,000 total,
    # split by the recorded 90/10 ratio)
    assert tok["estimated_input_tokens"] == 1_800_000
    assert tok["estimated_output_tokens"] == 200_000
    assert tok["recorded_run_share"] == 50.0
    assert tok["recorded_token_share"] == 50.0
    # recorded cost: 1.8M*$2 + 0.2M*$10 = $5.60; estimated the same
    assert tok["replacement_cost_usd"] == {
        "recorded": 5.6, "estimated": 5.6, "total": 11.2}
    assert extract["tokens"]["rate_table"] == RATES


def test_net_value_only_where_a_cost_figure_is_supplied():
    plain = build(token_snapshot(), rate_table=RATES)
    assert plain["tokens"]["all_time"]["net_value_usd"] is None
    assert plain["tokens"]["window"]["net_value_usd"] is None
    costed = build(token_snapshot(), rate_table=RATES,
                   electricity_usd_all_time=1.2, electricity_usd=0.2)
    assert costed["tokens"]["all_time"]["net_value_usd"] == 10.0
    assert costed["tokens"]["window"]["net_value_usd"] == 11.0


def test_default_rate_table_is_the_labeled_example():
    extract = build(token_snapshot())
    table = extract["tokens"]["rate_table"]
    assert table["reference_model"] == "example-reference-model"


# --- saturation -----------------------------------------------------------------------

def test_saturation_flag_on_when_pinned_at_cap():
    tasks = [task("s1", status="running"), task("s2", status="running")]
    runs = [
        run("s1", rid=1, started_at=NOW - 2 * DAY, ended_at=None, status="running"),
        run("s2", rid=2, started_at=NOW - 2 * DAY, ended_at=None, status="running"),
    ]
    extract = build({"tasks": tasks, "runs": runs, "comments": []},
                    concurrency_cap=2)
    sat = extract["saturation"]
    assert sat["running_runs"] == 2
    assert sat["at_cap_now"] is True
    assert sat["at_cap_share"] == 100.0
    assert sat["sustained_saturation"] is True


def test_saturation_flag_off_below_cap():
    tasks = [task("s1", status="running")]
    runs = [run("s1", rid=1, started_at=NOW - 3600, ended_at=None, status="running")]
    extract = build({"tasks": tasks, "runs": runs, "comments": []},
                    concurrency_cap=2)
    sat = extract["saturation"]
    assert sat["running_runs"] == 1
    assert sat["at_cap_now"] is False
    assert sat["at_cap_share"] == 0.0
    assert sat["sustained_saturation"] is False


def test_saturation_flags_are_null_without_a_cap():
    extract = build(main_snapshot())
    sat = extract["saturation"]
    assert sat["concurrency_cap"] is None
    assert sat["at_cap_now"] is None
    assert sat["sustained_saturation"] is None
    assert sat["definition"]


# --- empty board + rendering -----------------------------------------------------------

def test_empty_board_extracts_nulls_not_errors():
    extract = build({"tasks": [], "runs": [], "comments": []}, concurrency_cap=8)
    value = extract["first_run_value_success"]["all_time"]
    assert value["rate"] is None and value["verdict_coverage"] is None
    first = extract["failure_causes"]["all_time"]["first_runs"]
    assert first["total_failed"] == 0 and first["task_caused_share"] is None
    assert all(share is None for share in first["shares"].values())
    assert extract["timed_out"]["window"]["share_pct"] is None
    assert extract["estimate_calibration"]["overall"] == {
        "n": 0, "median_overrun": None}
    tok = extract["tokens"]["all_time"]
    assert tok["recorded_input_tokens"] == 0 and tok["net_value_usd"] is None
    assert extract["saturation"]["running_runs"] == 0
    assert extract["completions"]["window_tasks_done"] == 0


def test_extract_is_json_serializable_and_markdown_renders():
    extract = build(main_snapshot(), concurrency_cap=8)
    json.dumps(extract)  # must not raise
    text = render_markdown(extract)
    assert "# Fleet scorecard" in text
    assert "First-run value success" in text
    assert "unclassified" in text
    assert "Verdict coverage" in text
