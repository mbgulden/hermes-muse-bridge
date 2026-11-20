# fleetops.stocking

A read-only monitor that reports whether every worker profile on a
Hermes kanban board has work queued behind it.

For each profile it prints the running count, the ready count, and a
floor status. The floor: a profile with at least one running task
needs at least N ready tasks queued behind it (N defaults to 2). A
profile with zero running and zero ready is IDLE. A profile below the
floor or idle is listed with its next QUEUED bench items — work items
from a bench file, in bench order — so the operator can author them
into tasks. Authoring stays with the operator; the monitor never
creates tasks.

## Usage

```bash
python3 -m fleetops.stocking \
    --db /path/to/kanban.db \
    --bench bench.md \
    --floor 2
```

- `--db` — kanban sqlite database, opened read-only (`mode=ro`).
- `--bench` — a bench file in the format documented in BENCH.md;
  repeat the flag to read several bench files in order.
- `--floor` — ready tasks required behind a running profile.
- `--profiles` — comma-separated extra profiles to include even when
  they appear on neither the board nor the bench.

## Library use

The core functions accept plain data, so callers and tests never need
a database:

- `parse_bench_text(text)` / `load_bench_file(path)` — bench entries.
- `read_board_rows(db_path)` — row dicts from a read-only board.
- `build_report(rows, bench_items, floor=2)` — ProfileReport list.
- `render_report(report, floor=2)` — the plain-text report.

## Read-only property

The board is opened through a sqlite read-only URI, bench files are
only read, and the package contains no task-creation or board-write
code path. The test suite asserts the read-only open and scans the
package source for write statements.
