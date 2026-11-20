"""CLI for fleetops.classifier.

Reads a kanban.db snapshot read-only and prints the failure-cause split
(all runs and first runs) plus first-run value success, as JSON.

Usage:
    python -m fleetops.classifier.cli /path/to/kanban.db \
        [--profiles kai,george,ned,fred]

The population defaults to the fleet profiles kai, george, ned, fred
(orchestrator rows fold into fred). Pass --profiles '' for all tasks.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Any, Dict, List

from .classifier import classify_failures, first_run_value_success

DEFAULT_PROFILES = ("kai", "george", "ned", "fred")


def load_rows(db_path: str) -> Dict[str, List[Dict[str, Any]]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks")]
        runs = [dict(r) for r in conn.execute("SELECT * FROM task_runs")]
        comments = [dict(r) for r in conn.execute("SELECT * FROM task_comments")]
    finally:
        conn.close()
    return {"tasks": tasks, "runs": runs, "comments": comments}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fleetops.classifier")
    parser.add_argument("db", help="path to a kanban.db snapshot (opened read-only)")
    parser.add_argument(
        "--profiles",
        default=",".join(DEFAULT_PROFILES),
        help="comma-separated assignee filter (orchestrator folds into fred); "
        "empty string means all tasks",
    )
    args = parser.parse_args(argv)

    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()] or None
    rows = load_rows(args.db)
    result = {
        "population_profiles": profiles,
        "failures": classify_failures(
            rows["tasks"], rows["runs"], rows["comments"], profiles=profiles
        ),
        "first_run_value_success": first_run_value_success(
            rows["tasks"], rows["runs"], rows["comments"], profiles=profiles
        ),
    }
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
