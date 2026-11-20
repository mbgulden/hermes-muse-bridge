"""Thin command-line front end for the fleetops stocking monitor.

Usage:
    python3 -m fleetops.stocking --db /path/to/kanban.db \
        --bench bench.md [--bench more.md] [--floor 2] \
        [--profiles kai,george,ned,fred]

The database is opened read-only, the bench files are parsed in the
order given, and the stocking report is printed to stdout. Nothing is
written anywhere. Exit code 0 on success, 2 on a usage or input error.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from .bench import load_bench_file
from .board import read_board_rows
from .monitor import DEFAULT_FLOOR, build_report, render_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleetops-stocking",
        description="Report per-profile queue depth against its floor, "
        "with the next bench items for any profile below floor or idle.",
    )
    parser.add_argument(
        "--db",
        required=True,
        help="path to the kanban sqlite database (opened read-only)",
    )
    parser.add_argument(
        "--bench",
        action="append",
        default=[],
        metavar="FILE",
        help="bench file in the format documented in BENCH.md; "
        "repeatable, parsed in the order given",
    )
    parser.add_argument(
        "--floor",
        type=int,
        default=DEFAULT_FLOOR,
        help="ready tasks required behind a running profile "
        f"(default: {DEFAULT_FLOOR})",
    )
    parser.add_argument(
        "--profiles",
        default="",
        help="comma-separated extra profile names to include even when "
        "they appear on neither the board nor the bench",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.floor < 0:
        print(f"error: --floor must be >= 0, got {args.floor}", file=sys.stderr)
        return 2
    try:
        rows = read_board_rows(args.db)
    except sqlite3.Error as exc:
        print(f"error: cannot read board at {args.db}: {exc}", file=sys.stderr)
        return 2
    bench_items = []
    for bench_path in args.bench:
        try:
            bench_items.extend(load_bench_file(bench_path))
        except (OSError, ValueError) as exc:
            print(f"error: cannot load bench file: {exc}", file=sys.stderr)
            return 2
    profiles = [name.strip() for name in args.profiles.split(",") if name.strip()]
    report = build_report(
        rows,
        bench_items,
        floor=args.floor,
        profiles=profiles or None,
    )
    sys.stdout.write(render_report(report, floor=args.floor))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
