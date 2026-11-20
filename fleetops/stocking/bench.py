"""Bench-file parsing for the fleetops stocking monitor.

A bench file is a markdown file holding work items waiting to be
authored into tasks. The format is documented in BENCH.md next to this
module; the parser here implements exactly what BENCH.md documents:

- A level-2 heading (``## Title``) starts an entry.
- Three field lines follow the heading, in any order:
  ``Owner: <profile>``, ``Source: <paths or links>``, and
  ``State: QUEUED`` or ``State: DONE``.
- Blank lines and note lines inside an entry are ignored.
- An entry missing a field line, repeating a field line, or carrying a
  State other than QUEUED or DONE is a format error and raises
  ValueError naming the file and the entry.

Content before the first level-2 heading (for example a level-1 file
title or an introduction) is not part of any entry and is skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

QUEUED = "QUEUED"
DONE = "DONE"
STATES = (QUEUED, DONE)

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_FIELD_RE = re.compile(r"^(Owner|Source|State):\s*(.*?)\s*$")
_FIELDS = ("Owner", "Source", "State")


@dataclass(frozen=True)
class BenchItem:
    """One bench entry: a work item waiting to be authored."""

    title: str
    owner: str
    source: str
    state: str
    origin: str = ""

    @property
    def is_queued(self) -> bool:
        return self.state == QUEUED


def parse_bench_text(text: str, origin: str = "<bench>") -> list[BenchItem]:
    """Parse bench-file text into BenchItems, in file order.

    Raises ValueError on the first malformed entry.
    """
    items: list[BenchItem] = []
    current: dict[str, str] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        title = current["title"]
        for field in _FIELDS:
            if field not in current:
                raise ValueError(
                    f"{origin}: bench entry {title!r} is missing its "
                    f"{field}: line"
                )
        state = current["State"]
        if state not in STATES:
            raise ValueError(
                f"{origin}: bench entry {title!r} has State {state!r}; "
                f"expected one of {', '.join(STATES)}"
            )
        items.append(
            BenchItem(
                title=title,
                owner=current["Owner"],
                source=current["Source"],
                state=state,
                origin=origin,
            )
        )
        current = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            current = {"title": heading.group(1)}
            continue
        if line.startswith("#"):
            # Any other heading level closes the current entry.
            flush()
            continue
        if current is None:
            continue
        field = _FIELD_RE.match(line)
        if field:
            name, value = field.group(1), field.group(2)
            if name in current:
                raise ValueError(
                    f"{origin}: bench entry {current['title']!r} repeats "
                    f"its {name}: line"
                )
            current[name] = value
    flush()
    return items


def load_bench_file(path: str | Path) -> list[BenchItem]:
    """Read and parse one bench file, tagging items with its path."""
    path = Path(path)
    return parse_bench_text(path.read_text(encoding="utf-8"), origin=str(path))
