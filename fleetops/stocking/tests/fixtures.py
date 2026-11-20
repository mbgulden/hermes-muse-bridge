"""Fixture board snapshots and bench text for the stocking tests.

Board snapshots are lists of row dicts in the shape read_board_rows
returns: {"assignee": <profile>, "status": <status>}. Profile names
are example data.
"""

from __future__ import annotations


def rows(profile: str, status: str, count: int) -> list[dict]:
    return [{"assignee": profile, "status": status} for _ in range(count)]


# (1) Empty board: no tasks at all. Profiles are known only from the
# bench, and every one of them must report IDLE with its bench items.
EMPTY_BOARD: list[dict] = []

# (2) One idle profile among stocked profiles: nova has nothing,
# orbit, pixel, and quartz sit at or above the default floor of 2.
ONE_IDLE_BOARD: list[dict] = (
    rows("orbit", "running", 1)
    + rows("orbit", "ready", 2)
    + rows("pixel", "running", 2)
    + rows("pixel", "ready", 3)
    + rows("pixel", "done", 5)
    + rows("quartz", "running", 1)
    + rows("quartz", "ready", 2)
)

# (3) All profiles stocked at or above the default floor: no flags.
ALL_STOCKED_BOARD: list[dict] = (
    rows("nova", "running", 1)
    + rows("nova", "ready", 2)
    + rows("orbit", "running", 3)
    + rows("orbit", "ready", 4)
    + rows("pixel", "running", 1)
    + rows("pixel", "ready", 2)
    + rows("quartz", "running", 2)
    + rows("quartz", "ready", 2)
)

# (4) Below floor: nova is running with only one ready behind it
# (floor is 2), and quartz has ready work but nothing running.
BELOW_FLOOR_BOARD: list[dict] = (
    rows("nova", "running", 2)
    + rows("nova", "ready", 1)
    + rows("orbit", "running", 1)
    + rows("orbit", "ready", 2)
    + rows("quartz", "ready", 2)
)

BENCH_TEXT = """\
# Bench — fixture program

Work waiting for an author.

## Refresh the sitemap audit
Owner: nova
Source: ~/workspace/seo/audit-plan.md
State: QUEUED

## Draft the December newsletter
Owner: orbit
Source: ~/workspace/mail/newsletter-brief.md
State: QUEUED

## Second nova item
Owner: nova
Source: ~/workspace/seo/second.md
State: QUEUED

## Finished nova item
Owner: nova
Source: ~/workspace/seo/finished.md
State: DONE

## Quartz bench item
Owner: quartz
Source: ~/workspace/ops/quartz.md
State: QUEUED
"""
