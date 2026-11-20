"""fleetops.scorecard — the weekly measurement extract for the
Hermes kanban fleet.

Computes the improvement loop's scorecard from a board snapshot:
first-run value success and the failure-cause split (both delegated
to fleetops.classifier, never re-derived), timed-out hours and
completions, estimate calibration per task class, token accounting
with recorded and estimated kept separate, and slot saturation
against a caller-supplied cap. Pure stdlib, read-only. See
scorecard.py for the definitions and README.md for usage."""
from .scorecard import (
    DEFAULT_PROFILES,
    EXAMPLE_RATE_TABLE,
    SATURATION_DEFINITION,
    SATURATION_SHARE,
    SATURATION_WINDOW_HOURS,
    TASK_CLASSES,
    build_scorecard,
    render_markdown,
    task_class,
)

__all__ = [
    "DEFAULT_PROFILES",
    "EXAMPLE_RATE_TABLE",
    "SATURATION_DEFINITION",
    "SATURATION_SHARE",
    "SATURATION_WINDOW_HOURS",
    "TASK_CLASSES",
    "build_scorecard",
    "render_markdown",
    "task_class",
]
