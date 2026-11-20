"""CLI for fleetops.scorecard.

Reads a kanban.db snapshot read-only and prints the weekly
measurement extract as JSON (default) or as a markdown scorecard.

Usage:
    python -m fleetops.scorecard.cli /path/to/kanban.db \
        [--profiles kai,george,ned,fred] [--now EPOCH] \
        [--window-days 7] [--cap 8] [--rates rates.json] \
        [--electricity-usd 12.34] [--format json|markdown]

The population defaults to the fleet profiles kai, george, ned, fred
(orchestrator rows fold into fred). Pass --profiles '' for all tasks.
--rates is a JSON file holding the replacement-cost rate table:
{"reference_model": str, "input_per_million": float,
"output_per_million": float}. Without it, the extract names the
module's clearly-labeled EXAMPLE table as its basis. Exit code 0 on
success, 2 on a usage or input error.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

from .scorecard import DEFAULT_PROFILES, build_scorecard, render_markdown


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleetops.scorecard")
    parser.add_argument("db", help="path to a kanban.db snapshot (opened read-only)")
    parser.add_argument(
        "--profiles",
        default=",".join(DEFAULT_PROFILES),
        help="comma-separated assignee filter (orchestrator folds into fred); "
        "empty string means all tasks",
    )
    parser.add_argument("--now", type=float, default=None,
                        help="epoch seconds the windows anchor to (default: now)")
    parser.add_argument("--window-days", type=float, default=7,
                        help="weekly window length in days (default: 7)")
    parser.add_argument("--cap", type=int, default=None,
                        help="board concurrency cap for the saturation section")
    parser.add_argument("--rates", default=None,
                        help="path to a JSON rate table (see module docstring)")
    parser.add_argument("--electricity-usd", type=float, default=None,
                        help="running cost for the window; enables window net value")
    parser.add_argument("--electricity-usd-all-time", type=float, default=None,
                        help="running cost all-time; enables all-time net value")
    parser.add_argument("--format", choices=("json", "markdown"), default="json",
                        help="output format (default: json)")
    return parser


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()] or None
    config: Dict[str, Any] = {
        "profiles": profiles,
        "window_days": args.window_days,
        "concurrency_cap": args.cap,
        "electricity_usd": args.electricity_usd,
        "electricity_usd_all_time": args.electricity_usd_all_time,
    }
    if args.now is not None:
        config["now"] = args.now
    if args.rates:
        try:
            rate_table = json.loads(Path(args.rates).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"error: cannot load rate table: {exc}", file=sys.stderr)
            return 2
        if not isinstance(rate_table, dict):
            print("error: rate table must be a JSON object", file=sys.stderr)
            return 2
        config["rate_table"] = rate_table
    try:
        rows = load_rows(args.db)
    except sqlite3.Error as exc:
        print(f"error: cannot read board at {args.db}: {exc}", file=sys.stderr)
        return 2
    try:
        extract = build_scorecard(rows, config)
    except (TypeError, ValueError) as exc:
        print(f"error: cannot build scorecard: {exc}", file=sys.stderr)
        return 2
    if args.format == "markdown":
        sys.stdout.write(render_markdown(extract))
    else:
        print(json.dumps(extract, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
