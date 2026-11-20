"""fleetops.distiller — distillation decisions for failed fleet tasks.

Given a failed task and its cause from fleetops.classifier, decides
the response under the improvement loop's distill policy: a lineage
at the 3-strike cap escalates to Jimmy-direct, an infrastructure
cause re-runs as-is, a task-caused failure is distilled into a
reduced-scope successor with the failure's lesson written in, an
external dependency waits, and an unclassified cause fails closed
for reviewer labeling. See distiller.py for the policy and README.md
for the log-record format."""
from .distiller import (
    ACTIONS,
    CAUSES,
    DELIVERABLE_FIRST_CLAUSE,
    DISTILL,
    ESCALATE,
    ESCALATE_TO,
    FLAG_FOR_LABELING,
    MAX_STRIKES,
    PROFILE_ROTATION,
    REPORT_BEFORE_TIMEOUT_CLAUSE,
    RERUN_AS_IS,
    TASK_CAUSED,
    WAIT,
    DistillResult,
    LogRecord,
    append_log_record,
    build_successor_body,
    canonical_cause,
    distill,
    render_log_record,
    rotated_profile,
)

__all__ = [
    "ACTIONS",
    "CAUSES",
    "DELIVERABLE_FIRST_CLAUSE",
    "DISTILL",
    "ESCALATE",
    "ESCALATE_TO",
    "FLAG_FOR_LABELING",
    "MAX_STRIKES",
    "PROFILE_ROTATION",
    "REPORT_BEFORE_TIMEOUT_CLAUSE",
    "RERUN_AS_IS",
    "TASK_CAUSED",
    "WAIT",
    "DistillResult",
    "LogRecord",
    "append_log_record",
    "build_successor_body",
    "canonical_cause",
    "distill",
    "render_log_record",
    "rotated_profile",
]
