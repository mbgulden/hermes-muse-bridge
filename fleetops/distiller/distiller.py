"""Distillation decisions for failed Hermes kanban fleet tasks.

Given one failed task and its cause from fleetops.classifier, the
distiller decides what happens next under the improvement loop's
distill policy (H-2026-04) -- and, for task-caused failures, writes
the successor spec. The decision, in evaluation order:

1. A lineage at the 3-strike cap escalates to Jimmy-direct. No
   successor, no re-run: distillation never extends a lineage past
   the cap, whatever the cause.
2. An infrastructure cause re-runs as-is. The task was not wrong, and
   distilling it would waste a good task and muddy the data.
3. A task shape or task execution cause is distilled, never repeated
   as-is. The successor spec carries a smaller scope, a clarified
   body with the failure's lesson written into it, the
   DELIVERABLE-FIRST and REPORT-BEFORE-TIMEOUT clauses preserved,
   and an optional profile reassignment (an explicit target, or the
   kai -> george -> ned rotation folded in from H-2026-01). The
   original is marked superseded.
4. An external dependency waits. No successor; the decision is a
   wait/decision record naming the outside input.
5. An unclassified cause -- and any cause string the classifier
   would not produce -- fails closed: no successor, no re-run, a
   flag for reviewer labeling. Unclassified failures are never
   redistributed into another branch.

The distiller trusts the classifier's cause. It never re-derives a
cause from task text, so a task body that merely mentions a
credential in passing cannot flip a task-caused failure into a wait.

Every decision carries a distillation log record (see README.md for
the format): what failed, the cause, what changed. The module is
pure stdlib and performs no board writes -- it returns a decision
and a successor spec; authoring the successor onto the board stays
with the sweep / operator.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional

from fleetops.classifier.classifier import (
    EXTERNAL_DEPENDENCY,
    INFRASTRUCTURE,
    TASK_EXECUTION,
    TASK_SHAPE,
    UNCLASSIFIED,
)

# Cause classes, re-exported so callers need one import. The strings
# are the classifier's; the distiller adds no class of its own.
CAUSES = (
    INFRASTRUCTURE,
    TASK_SHAPE,
    TASK_EXECUTION,
    EXTERNAL_DEPENDENCY,
    UNCLASSIFIED,
)
TASK_CAUSED = frozenset({TASK_SHAPE, TASK_EXECUTION})

RERUN_AS_IS = "rerun_as_is"
DISTILL = "distill"
WAIT = "wait"
FLAG_FOR_LABELING = "flag_for_labeling"
ESCALATE = "escalate"

ACTIONS = (RERUN_AS_IS, DISTILL, WAIT, FLAG_FOR_LABELING, ESCALATE)

ESCALATE_TO = "jimmy-direct"

# The existing escalation cap (H-2026-04 guard): a lineage reaching
# this many strikes goes to Jimmy-direct, never to another successor.
MAX_STRIKES = 3

# The profile-switch move folded in from H-2026-01: one rotation over
# the three worker profiles. fred (the orchestrator profile) and any
# other identity never rotate.
PROFILE_ROTATION = ("kai", "george", "ned")

# Canonical clause texts, restored into a successor body when the
# original lost them. Detection is by the clause headers, matching
# the authoring checker: a body carrying the header carries the
# clause, and a clause is never duplicated.
DELIVERABLE_FIRST_CLAUSE = (
    "DELIVERABLE-FIRST (hard rule): Your FIRST action is to create "
    "the deliverable file at the path named in this task with its "
    "full skeleton — every section heading plus what is already "
    "known — and save it. Then write each finding into the file as "
    "you find it. The file is the report at every moment; if this "
    "run dies, the file is what survives. Never hold findings in "
    "your head until the end."
)

REPORT_BEFORE_TIMEOUT_CLAUSE = (
    "REPORT-BEFORE-TIMEOUT (hard rule): By 80% of this run's cap, "
    "the deliverable file must be in its final state for this "
    "attempt AND a summary comment posted on the task — whether you "
    "are finished or not. If not finished, the file's status line "
    "says INCOMPLETE and states exactly what the next attempt must "
    "do first. A run that hits the cap with no report on file is a "
    "failed run, no matter how much work it did."
)

_CLAUSES = (
    ("DELIVERABLE-FIRST", DELIVERABLE_FIRST_CLAUSE),
    ("REPORT-BEFORE-TIMEOUT", REPORT_BEFORE_TIMEOUT_CLAUSE),
)

_CAUSE_ALIASES = {
    "infrastructure": INFRASTRUCTURE,
    "infra": INFRASTRUCTURE,
    "task shape": TASK_SHAPE,
    "task_shape": TASK_SHAPE,
    "task-shape": TASK_SHAPE,
    "task execution": TASK_EXECUTION,
    "task_execution": TASK_EXECUTION,
    "task-execution": TASK_EXECUTION,
    "external dependency": EXTERNAL_DEPENDENCY,
    "external_dependency": EXTERNAL_DEPENDENCY,
    "external-dependency": EXTERNAL_DEPENDENCY,
    "external": EXTERNAL_DEPENDENCY,
    "unclassified": UNCLASSIFIED,
}


def canonical_cause(cause: Any) -> str:
    """Map a cause string to its canonical class.

    Anything unrecognized maps to unclassified, so an unknown cause
    fails closed into the labeling branch instead of erroring or
    being guessed into a live branch.
    """
    key = str(cause or "").strip().lower()
    return _CAUSE_ALIASES.get(key, UNCLASSIFIED)


def rotated_profile(profile: Optional[str]) -> Optional[str]:
    """The next profile in the kai -> george -> ned rotation.

    Returns None for profiles outside the rotation (fred, muse,
    unknown identities): those never rotate.
    """
    name = str(profile or "").strip().lower()
    if name == "orchestrator":
        name = "fred"
    if name not in PROFILE_ROTATION:
        return None
    idx = PROFILE_ROTATION.index(name)
    return PROFILE_ROTATION[(idx + 1) % len(PROFILE_ROTATION)]


def _task_assignee(task: Mapping[str, Any]) -> Optional[str]:
    value = task.get("assignee") or task.get("profile")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def build_successor_body(
    task: Mapping[str, Any],
    cause: str,
    lesson_text: str,
) -> str:
    """The clarified successor body for a distilled task.

    The original body is preserved (its clauses included); a header
    block is prepended carrying the distillation lesson and the
    scope-reduction instruction, and any of the two hard clauses the
    original is missing are appended from the canonical texts --
    present exactly once, never duplicated.
    """
    original = str(task.get("body") or "").strip()
    task_id = task.get("id")
    title = task.get("title") or ""
    header = "\n".join(
        [
            "## Distilled successor — read first",
            "",
            f"This task is the distilled successor of task {task_id} "
            f"({title!r}), which failed with cause: {cause}. The "
            "original is superseded; do not re-run it as-is.",
            "",
            "### Distillation lesson",
            "",
            lesson_text,
            "",
            "### Scope reduction",
            "",
            "Scope is reduced for this successor: complete the single "
            "primary deliverable named in the body below, end to end, "
            "before touching anything else. If the original named "
            "more than one deliverable, the rest are follow-ups for "
            "separate tasks — name them in the final report, do not "
            "start them here.",
        ]
    )
    parts = [header]
    if original:
        parts.append(original)
    for header_word, clause in _CLAUSES:
        if header_word not in original:
            parts.append(clause)
    return "\n\n".join(parts) + "\n"


@dataclass
class LogRecord:
    """One distillation log record. See README.md for the format."""

    task_id: Any
    title: str
    cause: str
    action: str
    strike_count: int
    what_failed: str
    what_changed: List[str] = field(default_factory=list)
    successor_assignee: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "cause": self.cause,
            "action": self.action,
            "strike_count": self.strike_count,
            "what_failed": self.what_failed,
            "what_changed": list(self.what_changed),
            "successor_assignee": self.successor_assignee,
        }


@dataclass
class DistillResult:
    """The distiller's decision for one failed task."""

    action: str
    task_id: Any
    title: str
    cause: str
    strike_count: int
    reason: str
    log_record: LogRecord
    successor: Optional[dict] = None
    rerun_task: Optional[dict] = None
    wait_record: Optional[dict] = None
    superseded: bool = False
    escalate_to: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "task_id": self.task_id,
            "title": self.title,
            "cause": self.cause,
            "strike_count": self.strike_count,
            "reason": self.reason,
            "successor": self.successor,
            "rerun_task": self.rerun_task,
            "wait_record": self.wait_record,
            "superseded": self.superseded,
            "escalate_to": self.escalate_to,
            "log_record": self.log_record.to_dict(),
        }


def distill(
    task: Mapping[str, Any],
    cause: Any,
    *,
    strike_count: int = 0,
    failure_summary: str = "",
    lesson: Optional[str] = None,
    reassign_to: Optional[str] = None,
    rotate_profile: bool = False,
) -> DistillResult:
    """Decide the response to one failed task.

    task is a plain dict shaped like a kanban task (id, title, body,
    assignee). cause is the classifier's cause class for the failure.
    strike_count is the lineage's strike count including this
    failure. failure_summary / lesson carry what the failure showed;
    lesson, when given, is the text written into a distilled
    successor's body. reassign_to names an explicit successor
    assignee; rotate_profile instead applies the kai -> george ->
    ned rotation. An explicit target wins over rotation.
    """
    if not isinstance(strike_count, int) or isinstance(strike_count, bool):
        raise ValueError(f"strike_count must be an int, got {strike_count!r}")
    if strike_count < 0:
        raise ValueError(f"strike_count must be >= 0, got {strike_count}")

    canonical = canonical_cause(cause)
    task_id = task.get("id")
    title = str(task.get("title") or "")
    what_failed = (
        str(failure_summary or "").strip()
        or str(lesson or "").strip()
        or f"Task failed with cause: {canonical}."
    )
    lesson_text = (
        str(lesson or "").strip()
        or str(failure_summary or "").strip()
        or (
            f"The first attempt failed (cause: {canonical}) without "
            "earning a PASS. This successor narrows the scope and "
            "clarifies the deliverable before any repeat attempt; "
            "work the reduced scope only."
        )
    )

    def result(action: str, reason: str, what_changed: List[str], **kw) -> DistillResult:
        record = LogRecord(
            task_id=task_id,
            title=title,
            cause=canonical,
            action=action,
            strike_count=strike_count,
            what_failed=what_failed,
            what_changed=what_changed,
            successor_assignee=(
                kw["successor"].get("assignee") if kw.get("successor") else None
            ),
        )
        return DistillResult(
            action=action,
            task_id=task_id,
            title=title,
            cause=canonical,
            strike_count=strike_count,
            reason=reason,
            log_record=record,
            **kw,
        )

    # 1. The cap is checked first: no branch extends a capped lineage.
    if strike_count >= MAX_STRIKES:
        return result(
            ESCALATE,
            f"Lineage is at the {MAX_STRIKES}-strike cap; escalated "
            f"to {ESCALATE_TO} instead of authoring another attempt.",
            [
                f"no successor authored: lineage reached the "
                f"{MAX_STRIKES}-strike cap",
                f"escalated to {ESCALATE_TO}",
            ],
            escalate_to=ESCALATE_TO,
        )

    # 2. Infrastructure: the task was not wrong; re-run it as-is.
    if canonical == INFRASTRUCTURE:
        return result(
            RERUN_AS_IS,
            "Infrastructure cause: the task was not wrong, so it "
            "re-runs as-is with no distillation.",
            ["no change: infrastructure cause, re-run as-is"],
            rerun_task=dict(task),
        )

    # 3. Task-caused: distill into a successor; original superseded.
    if canonical in TASK_CAUSED:
        original_assignee = _task_assignee(task)
        new_assignee = original_assignee
        changes = [
            "successor scope reduced to the single primary deliverable",
            "failure lesson written into the successor body",
            "DELIVERABLE-FIRST + REPORT-BEFORE-TIMEOUT clauses preserved",
            f"original task {task_id} marked superseded",
        ]
        if reassign_to is not None and str(reassign_to).strip():
            new_assignee = str(reassign_to).strip()
            changes.append(
                f"assignee changed {original_assignee} -> {new_assignee} "
                "(explicit reassignment)"
            )
        elif rotate_profile:
            rotated = rotated_profile(original_assignee)
            if rotated is not None and rotated != original_assignee:
                new_assignee = rotated
                changes.append(
                    f"assignee rotated {original_assignee} -> {rotated} "
                    "(kai/george/ned rotation)"
                )
        successor = {
            "title": title,
            "body": build_successor_body(task, canonical, lesson_text),
            "assignee": new_assignee,
            "supersedes": task_id,
            "distilled_from_cause": canonical,
            "strike_count": strike_count + 1,
        }
        return result(
            DISTILL,
            f"{canonical} cause: distilled into a reduced-scope "
            "successor with the failure's lesson written in; the "
            "original is superseded, never repeated as-is.",
            changes,
            successor=successor,
            superseded=True,
        )

    # 4. External dependency: wait on the outside input it names.
    if canonical == EXTERNAL_DEPENDENCY:
        wait_record = {
            "task_id": task_id,
            "title": title,
            "cause": canonical,
            "waiting_on": what_failed,
            "decision_needed": (
                "Surface the outside input this task is blocked on; "
                "author no successor until it lands."
            ),
        }
        return result(
            WAIT,
            "External dependency cause: no successor is authored; "
            "the task waits on the outside input it names.",
            ["no successor: waiting on the named external dependency"],
            wait_record=wait_record,
        )

    # 5. Unclassified (or unrecognized): fail closed for labeling.
    return result(
        FLAG_FOR_LABELING,
        "Cause is unclassified: the record cannot support a class, "
        "so no successor and no re-run; flagged for reviewer "
        "labeling, never redistributed.",
        ["no successor, no re-run: flagged for reviewer labeling"],
    )


def render_log_record(record: LogRecord | Mapping[str, Any]) -> str:
    """Render one log record as a markdown block (format: README.md)."""
    data = record.to_dict() if isinstance(record, LogRecord) else dict(record)
    lines = [
        f"## Distillation — {data.get('task_id')}",
        f"- Title: {data.get('title')}",
        f"- Cause: {data.get('cause')}",
        f"- Action: {data.get('action')}",
        f"- Strikes: {data.get('strike_count')}",
        f"- What failed: {data.get('what_failed')}",
        "- What changed:",
    ]
    for change in data.get("what_changed") or []:
        lines.append(f"  - {change}")
    if data.get("successor_assignee"):
        lines.append(f"- Successor assignee: {data['successor_assignee']}")
    return "\n".join(lines) + "\n"


def append_log_record(path: str | Path, record: LogRecord | Mapping[str, Any]) -> None:
    """Append one log record to a JSONL log file (format: README.md).

    One JSON object per line, keys sorted. The file is created when
    missing; parent directories are not created.
    """
    data = record.to_dict() if isinstance(record, LogRecord) else dict(record)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, sort_keys=True) + "\n")
