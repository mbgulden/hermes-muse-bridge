"""Stocking report logic for the fleetops stocking monitor.

The monitor answers one question per worker profile: is enough work
queued behind what is running? A profile with at least one running
task needs at least ``floor`` ready tasks queued behind it (floor
defaults to 2). A profile with zero running and zero ready tasks is
IDLE. Profiles below the floor or idle get their next QUEUED bench
items listed, in bench-file order, so the operator can author them.

Everything here works on plain row dicts ({"assignee", "status"}) and
BenchItem objects, so tests feed fixtures and never touch a database.
Statuses other than "running" and "ready" do not count toward either
number, but a row's assignee still registers the profile as known.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bench import BenchItem

OK = "OK"
BELOW_FLOOR = "BELOW FLOOR"
IDLE = "IDLE"
QUEUED_ONLY = "QUEUED"

DEFAULT_FLOOR = 2


@dataclass(frozen=True)
class ProfileReport:
    """One profile's stocking state."""

    profile: str
    running: int
    ready: int
    status: str
    bench_items: list[BenchItem] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        """True when the profile needs operator attention."""
        return self.status in (BELOW_FLOOR, IDLE)


def _row_profile(row: dict) -> str | None:
    profile = row.get("assignee") or row.get("profile")
    if profile is None:
        return None
    profile = str(profile).strip()
    return profile or None


def count_profiles(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Count running and ready tasks per profile from board rows."""
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        profile = _row_profile(row)
        if profile is None:
            continue
        entry = counts.setdefault(profile, {"running": 0, "ready": 0})
        status = str(row.get("status", "")).strip().lower()
        if status in entry:
            entry[status] += 1
    return counts


def profile_status(running: int, ready: int, floor: int) -> str:
    """Classify one profile against the floor."""
    if running == 0 and ready == 0:
        return IDLE
    if running == 0:
        return QUEUED_ONLY
    if ready < floor:
        return BELOW_FLOOR
    return OK


def build_report(
    rows: list[dict],
    bench_items: list[BenchItem] | tuple[BenchItem, ...] = (),
    floor: int = DEFAULT_FLOOR,
    profiles: list[str] | tuple[str, ...] | None = None,
) -> list[ProfileReport]:
    """Build the per-profile stocking report.

    The profile set is the union of board assignees, bench owners, and
    any profiles passed explicitly. Reports come back sorted by
    profile name. Flagged profiles (BELOW FLOOR or IDLE) carry their
    QUEUED bench items in bench order; other profiles carry none.
    """
    if floor < 0:
        raise ValueError(f"floor must be >= 0, got {floor}")
    counts = count_profiles(rows)
    known = set(counts)
    known.update(item.owner for item in bench_items)
    if profiles:
        known.update(profiles)
    report = []
    for profile in sorted(known):
        entry = counts.get(profile, {"running": 0, "ready": 0})
        status = profile_status(entry["running"], entry["ready"], floor)
        items: list[BenchItem] = []
        if status in (BELOW_FLOOR, IDLE):
            items = [
                item
                for item in bench_items
                if item.owner == profile and item.is_queued
            ]
        report.append(
            ProfileReport(
                profile=profile,
                running=entry["running"],
                ready=entry["ready"],
                status=status,
                bench_items=items,
            )
        )
    return report


def render_report(report: list[ProfileReport], floor: int = DEFAULT_FLOOR) -> str:
    """Render a stocking report as plain text."""
    lines = [
        "Stocking report",
        f"Floor: a profile with running work needs at least {floor} "
        "ready tasks queued behind it.",
        "",
    ]
    if not report:
        lines.append("No profiles known: the board is empty and no bench "
                     "items were supplied.")
        return "\n".join(lines) + "\n"
    for entry in report:
        lines.append(
            f"{entry.profile}: running={entry.running} "
            f"ready={entry.ready} [{entry.status}]"
        )
        if entry.flagged:
            if entry.bench_items:
                lines.append(f"  next bench items for {entry.profile}:")
                for item in entry.bench_items:
                    lines.append(f"  - {item.title} (Source: {item.source})")
            else:
                lines.append(
                    f"  next bench items for {entry.profile}: none on the bench"
                )
    return "\n".join(lines) + "\n"
