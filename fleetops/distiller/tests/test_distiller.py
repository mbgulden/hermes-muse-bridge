"""Tests for fleetops.distiller.

Fixtures are plain task dicts shaped like kanban tasks, paired with
a cause string in the classifier's vocabulary. The suite covers
every cause branch, the 3-strike cap, the clause-preservation rule,
the profile rotation, the log record, and the regression that a
passing mention of credentials never flips a task-caused failure
into a wait (the distiller trusts the classifier's cause; it never
re-derives one from task text).
"""
import json

import pytest

from fleetops.classifier.classifier import (
    EXTERNAL_DEPENDENCY,
    INFRASTRUCTURE,
    TASK_EXECUTION,
    TASK_SHAPE,
    UNCLASSIFIED,
)
from fleetops.distiller.distiller import (
    DISTILL,
    ESCALATE,
    ESCALATE_TO,
    FLAG_FOR_LABELING,
    MAX_STRIKES,
    RERUN_AS_IS,
    WAIT,
    append_log_record,
    canonical_cause,
    distill,
    render_log_record,
    rotated_profile,
)

BODY_WITH_CLAUSES = (
    "Build the report and save it.\n\n"
    "DELIVERABLE-FIRST (hard rule): Your FIRST action is to create the "
    "deliverable file at /home/ubuntu/work/report.md with its full "
    "skeleton.\n\n"
    "REPORT-BEFORE-TIMEOUT (hard rule): This run is capped at 2h. By "
    "80% of the cap the deliverable must be final for this attempt."
)

BODY_NO_CLAUSES = "Build the report and save it at /home/ubuntu/work/report.md."


def task(tid="t1", assignee="kai", body=BODY_WITH_CLAUSES, **kw):
    row = {"id": tid, "title": "fixture task", "assignee": assignee,
           "body": body, "status": "blocked"}
    row.update(kw)
    return row


# --- infrastructure ---------------------------------------------------------

def test_infrastructure_reruns_as_is_with_no_successor():
    t = task()
    result = distill(t, INFRASTRUCTURE, strike_count=1,
                     failure_summary="Worker died; claim expired.")
    assert result.action == RERUN_AS_IS
    assert result.successor is None
    assert result.rerun_task == t
    assert result.superseded is False
    assert result.escalate_to is None


def test_infrastructure_never_distills_even_with_rotation_requested():
    result = distill(task(), INFRASTRUCTURE, strike_count=1,
                     rotate_profile=True)
    assert result.action == RERUN_AS_IS
    assert result.successor is None


# --- task-caused distillation -------------------------------------------------

def test_task_shape_distills_with_lesson_scope_and_supersede():
    result = distill(task(), TASK_SHAPE, strike_count=1,
                     failure_summary="Burned the 2h cap, no deliverable.",
                     lesson="Two deliverables were bundled; split them.")
    assert result.action == DISTILL
    assert result.superseded is True
    successor = result.successor
    assert successor is not None
    assert successor["supersedes"] == "t1"
    assert successor["distilled_from_cause"] == TASK_SHAPE
    assert successor["strike_count"] == 2
    body = successor["body"]
    assert "Distillation lesson" in body
    assert "Two deliverables were bundled; split them." in body
    assert "Scope reduction" in body
    assert "Build the report and save it." in body  # original preserved


def test_task_execution_distills():
    result = distill(task(), TASK_EXECUTION, strike_count=1,
                     failure_summary="Deliverable earned a FAIL verdict.")
    assert result.action == DISTILL
    assert result.successor is not None
    assert "Deliverable earned a FAIL verdict." in result.successor["body"]


def test_distilled_body_keeps_existing_clauses_exactly_once():
    body = distill(task(), TASK_SHAPE, strike_count=1).successor["body"]
    assert body.count("DELIVERABLE-FIRST") == 1
    assert body.count("REPORT-BEFORE-TIMEOUT") == 1


def test_distilled_body_restores_missing_clauses():
    body = distill(task(body=BODY_NO_CLAUSES), TASK_SHAPE,
                   strike_count=1).successor["body"]
    assert body.count("DELIVERABLE-FIRST") == 1
    assert body.count("REPORT-BEFORE-TIMEOUT") == 1
    assert BODY_NO_CLAUSES in body


def test_distill_default_keeps_original_assignee():
    result = distill(task(assignee="ned"), TASK_SHAPE, strike_count=1)
    assert result.successor["assignee"] == "ned"


# --- profile reassignment -------------------------------------------------------

def test_rotation_moves_kai_to_george_george_to_ned_ned_to_kai():
    assert rotated_profile("kai") == "george"
    assert rotated_profile("george") == "ned"
    assert rotated_profile("ned") == "kai"


def test_rotation_never_moves_fred_or_unknown_profiles():
    assert rotated_profile("fred") is None
    assert rotated_profile("orchestrator") is None
    assert rotated_profile("muse") is None
    assert rotated_profile(None) is None


def test_rotate_profile_flag_applies_the_rotation():
    result = distill(task(assignee="kai"), TASK_EXECUTION, strike_count=1,
                     rotate_profile=True)
    assert result.successor["assignee"] == "george"
    assert any("rotated" in change for change in result.log_record.what_changed)


def test_rotate_profile_flag_leaves_fred_in_place():
    result = distill(task(assignee="fred"), TASK_SHAPE, strike_count=1,
                     rotate_profile=True)
    assert result.successor["assignee"] == "fred"


def test_explicit_reassignment_wins_over_rotation():
    result = distill(task(assignee="kai"), TASK_SHAPE, strike_count=1,
                     reassign_to="ned", rotate_profile=True)
    assert result.successor["assignee"] == "ned"
    assert any("explicit reassignment" in change
               for change in result.log_record.what_changed)


# --- external dependency ---------------------------------------------------------

def test_external_waits_with_no_successor():
    result = distill(task(), EXTERNAL_DEPENDENCY, strike_count=1,
                     failure_summary="Waiting on a pricing decision.")
    assert result.action == WAIT
    assert result.successor is None
    assert result.superseded is False
    assert result.wait_record is not None
    assert result.wait_record["task_id"] == "t1"
    assert result.wait_record["waiting_on"] == "Waiting on a pricing decision."


# --- unclassified: fail closed -----------------------------------------------------

def test_unclassified_flags_for_labeling_with_no_successor():
    result = distill(task(), UNCLASSIFIED, strike_count=1,
                     failure_summary="Crashed with no usable evidence.")
    assert result.action == FLAG_FOR_LABELING
    assert result.successor is None
    assert result.rerun_task is None
    assert result.wait_record is None


def test_unrecognized_cause_fails_closed_as_unclassified():
    result = distill(task(), "some new label", strike_count=1)
    assert result.action == FLAG_FOR_LABELING
    assert result.cause == UNCLASSIFIED
    assert result.successor is None


def test_canonical_cause_aliases_and_unknown():
    assert canonical_cause("Task_Shape") == TASK_SHAPE
    assert canonical_cause("external") == EXTERNAL_DEPENDENCY
    assert canonical_cause("") == UNCLASSIFIED
    assert canonical_cause(None) == UNCLASSIFIED


# --- 3-strike cap ---------------------------------------------------------------------

def test_three_strikes_escalates_a_task_caused_failure():
    result = distill(task(), TASK_SHAPE, strike_count=MAX_STRIKES,
                     failure_summary="Third failure in the lineage.")
    assert result.action == ESCALATE
    assert result.escalate_to == ESCALATE_TO == "jimmy-direct"
    assert result.successor is None


def test_three_strikes_escalates_even_an_infrastructure_failure():
    result = distill(task(), INFRASTRUCTURE, strike_count=3,
                     failure_summary="Third failure in the lineage.")
    assert result.action == ESCALATE
    assert result.successor is None
    assert result.rerun_task is None


def test_three_strikes_escalates_an_external_failure_too():
    result = distill(task(), EXTERNAL_DEPENDENCY, strike_count=5)
    assert result.action == ESCALATE
    assert result.wait_record is None


def test_two_strikes_still_distills():
    result = distill(task(), TASK_EXECUTION, strike_count=2)
    assert result.action == DISTILL
    assert result.successor["strike_count"] == 3


def test_negative_strike_count_is_an_error():
    with pytest.raises(ValueError):
        distill(task(), TASK_SHAPE, strike_count=-1)


# --- regression: passing credential mentions never flip the branch -------------------

def test_regression_credentials_mention_with_task_shape_still_distills():
    body = ("Refresh the login page copy. The credential fields keep "
            "their current labels; no sign-in work is in scope.\n\n"
            + BODY_WITH_CLAUSES)
    result = distill(task(body=body), TASK_SHAPE, strike_count=1,
                     failure_summary="Burned the cap with no deliverable.")
    assert result.action == DISTILL
    assert result.successor is not None


def test_regression_credentials_mention_with_task_execution_still_distills():
    body = ("Write the migration note mentioning the stored credential "
            "by name only.\n\n" + BODY_WITH_CLAUSES)
    result = distill(task(body=body), TASK_EXECUTION, strike_count=1,
                     failure_summary="Deliverable earned a FAIL verdict.")
    assert result.action == DISTILL
    assert result.wait_record is None


def test_regression_approved_copy_mention_still_distills():
    body = "Draft from the approved copy in the brief.\n\n" + BODY_WITH_CLAUSES
    result = distill(task(body=body), TASK_SHAPE, strike_count=1)
    assert result.action == DISTILL


# --- log record ---------------------------------------------------------------------------

def test_every_action_carries_a_complete_log_record():
    cases = [
        (INFRASTRUCTURE, RERUN_AS_IS),
        (TASK_SHAPE, DISTILL),
        (TASK_EXECUTION, DISTILL),
        (EXTERNAL_DEPENDENCY, WAIT),
        (UNCLASSIFIED, FLAG_FOR_LABELING),
    ]
    for cause, expected_action in cases:
        result = distill(task(), cause, strike_count=1,
                         failure_summary=f"Failure for {cause}.")
        record = result.log_record
        assert record.task_id == "t1"
        assert record.title == "fixture task"
        assert record.cause == cause
        assert record.action == expected_action
        assert record.strike_count == 1
        assert record.what_failed == f"Failure for {cause}."
        assert record.what_changed  # non-empty for every action
    escalated = distill(task(), TASK_SHAPE, strike_count=3)
    assert escalated.log_record.action == ESCALATE
    assert escalated.log_record.what_changed


def test_log_record_successor_assignee_only_on_distill():
    distilled = distill(task(assignee="george"), TASK_SHAPE, strike_count=1)
    assert distilled.log_record.successor_assignee == "george"
    waited = distill(task(), EXTERNAL_DEPENDENCY, strike_count=1)
    assert waited.log_record.successor_assignee is None


def test_render_log_record_is_a_markdown_block():
    result = distill(task(), TASK_SHAPE, strike_count=1,
                     failure_summary="Cap burned.")
    text = render_log_record(result.log_record)
    assert text.startswith("## Distillation — t1")
    assert "- Cause: task shape" in text
    assert "- Action: distill" in text
    assert "- What failed: Cap burned." in text
    assert "- Successor assignee: kai" in text


def test_append_log_record_writes_jsonl(tmp_path):
    path = tmp_path / "distillations.jsonl"
    first = distill(task(), INFRASTRUCTURE, strike_count=1,
                    failure_summary="Worker died.")
    second = distill(task(tid="t2"), TASK_SHAPE, strike_count=1,
                     failure_summary="Cap burned.")
    append_log_record(path, first.log_record)
    append_log_record(path, second.log_record)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["action"] == RERUN_AS_IS
    assert parsed[0]["task_id"] == "t1"
    assert parsed[1]["action"] == DISTILL
    assert parsed[1]["what_changed"]
