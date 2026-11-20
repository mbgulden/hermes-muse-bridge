"""Thin command-line front end for the fleetops distiller.

Usage:
    python3 -m fleetops.distiller --input failure.json [--log log.jsonl]
    python3 -m fleetops.distiller.cli failure.json

The input is one JSON object:

    {
      "task": {"id": "t_123", "title": "...", "body": "...",
               "assignee": "kai"},
      "cause": "task shape",
      "strike_count": 1,
      "failure_summary": "Burned the 2h cap with no deliverable.",
      "lesson": "Split the two deliverables; do the report first.",
      "reassign_to": "george",        // optional, wins over rotation
      "rotate_profile": false,        // optional
      "log_path": "distillations.jsonl"  // optional, or --log
    }

The decision is printed to stdout as JSON, including its log
record. When a log path is given (flag or input field), the record
is also appended to that JSONL file. The distiller writes nothing
else and never touches the board. Exit code 0 on a decision, 2 on
a usage or input error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .distiller import append_log_record, distill


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleetops-distiller",
        description="Decide the response to one failed fleet task "
        "under the distill policy, and print the decision as JSON.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="path to the input JSON object (or use --input)",
    )
    parser.add_argument(
        "--input",
        dest="input_flag",
        default=None,
        help="path to the input JSON object",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="append the decision's log record to this JSONL file "
        "(overrides the input's log_path field)",
    )
    return parser


def _load_input(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    task = data.get("task")
    if task is None and ("id" in data or "title" in data):
        task = {
            key: data[key]
            for key in ("id", "title", "body", "assignee", "profile")
            if key in data
        }
    if not isinstance(task, dict) or not task:
        raise ValueError("input must carry a non-empty 'task' object")
    if "cause" not in data:
        raise ValueError("input must carry a 'cause' from fleetops.classifier")
    return data


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = args.input_flag or args.input
    if not input_path:
        print("error: an input JSON path is required", file=sys.stderr)
        return 2
    try:
        data = _load_input(input_path)
    except (OSError, ValueError) as exc:
        print(f"error: cannot load input: {exc}", file=sys.stderr)
        return 2
    task = data["task"] if isinstance(data.get("task"), dict) else {
        key: data[key]
        for key in ("id", "title", "body", "assignee", "profile")
        if key in data
    }
    try:
        strike_count = data.get("strike_count", 0)
        result = distill(
            task,
            data["cause"],
            strike_count=int(strike_count),
            failure_summary=str(data.get("failure_summary") or ""),
            lesson=data.get("lesson"),
            reassign_to=data.get("reassign_to"),
            rotate_profile=bool(data.get("rotate_profile", False)),
        )
    except (TypeError, ValueError) as exc:
        print(f"error: cannot distill: {exc}", file=sys.stderr)
        return 2
    log_path = args.log or data.get("log_path")
    if log_path:
        try:
            append_log_record(log_path, result.log_record)
        except OSError as exc:
            print(f"error: cannot append log record: {exc}", file=sys.stderr)
            return 2
    print(json.dumps(result.to_dict(), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
