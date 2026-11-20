"""fleetops.classifier — failure-cause classification and first-run
value success for Hermes kanban fleet runs. See classifier.py for the
taxonomy and the classification rules."""
from .classifier import (
    CLASSES,
    DEFAULT_REVIEWERS,
    EXTERNAL_DEPENDENCY,
    FAILED_OUTCOMES,
    INFRASTRUCTURE,
    TASK_CAUSED,
    TASK_EXECUTION,
    TASK_SHAPE,
    UNCLASSIFIED,
    classify_failures,
    classify_run,
    first_run_value_success,
    is_failed_run,
)

__all__ = [
    "CLASSES",
    "DEFAULT_REVIEWERS",
    "EXTERNAL_DEPENDENCY",
    "FAILED_OUTCOMES",
    "INFRASTRUCTURE",
    "TASK_CAUSED",
    "TASK_EXECUTION",
    "TASK_SHAPE",
    "UNCLASSIFIED",
    "classify_failures",
    "classify_run",
    "first_run_value_success",
    "is_failed_run",
]
