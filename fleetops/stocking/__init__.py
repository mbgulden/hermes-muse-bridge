"""fleetops.stocking — the read-only fleet stocking monitor.

Reads a kanban board (sqlite, opened read-only) and bench files in the
format documented in BENCH.md, and reports per-profile queue depth
against a floor: a profile with running work needs ready tasks queued
behind it, and a profile with nothing running and nothing ready is
idle. Flagged profiles get their next QUEUED bench items listed so the
operator can author them. The monitor never creates tasks and never
writes anything except its printed report.
"""

from .bench import DONE, QUEUED, BenchItem, load_bench_file, parse_bench_text
from .board import read_board_rows
from .monitor import (
    BELOW_FLOOR,
    DEFAULT_FLOOR,
    IDLE,
    OK,
    QUEUED_ONLY,
    ProfileReport,
    build_report,
    count_profiles,
    profile_status,
    render_report,
)

__all__ = [
    "BELOW_FLOOR",
    "DEFAULT_FLOOR",
    "DONE",
    "IDLE",
    "OK",
    "QUEUED",
    "QUEUED_ONLY",
    "BenchItem",
    "ProfileReport",
    "build_report",
    "count_profiles",
    "load_bench_file",
    "parse_bench_text",
    "profile_status",
    "read_board_rows",
    "render_report",
]
