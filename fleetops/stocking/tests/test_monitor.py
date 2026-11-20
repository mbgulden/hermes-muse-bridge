"""Tests for the stocking report over the fixture board snapshots."""

from fleetops.stocking.bench import parse_bench_text
from fleetops.stocking.monitor import (
    BELOW_FLOOR,
    IDLE,
    OK,
    QUEUED_ONLY,
    build_report,
    profile_status,
    render_report,
)

from fixtures import (
    ALL_STOCKED_BOARD,
    BELOW_FLOOR_BOARD,
    BENCH_TEXT,
    EMPTY_BOARD,
    ONE_IDLE_BOARD,
)

BENCH = parse_bench_text(BENCH_TEXT)


def by_profile(report):
    return {entry.profile: entry for entry in report}


def test_empty_board_reports_every_profile_idle_with_bench_items():
    report = build_report(EMPTY_BOARD, BENCH)
    profiles = by_profile(report)
    assert set(profiles) == {"nova", "orbit", "quartz"}
    for entry in report:
        assert entry.status == IDLE
        assert entry.running == 0
        assert entry.ready == 0
    assert [i.title for i in profiles["nova"].bench_items] == [
        "Refresh the sitemap audit",
        "Second nova item",
    ]
    assert [i.title for i in profiles["orbit"].bench_items] == [
        "Draft the December newsletter"
    ]


def test_empty_board_with_explicit_profiles_and_no_bench():
    report = build_report(EMPTY_BOARD, [], profiles=["nova"])
    (entry,) = report
    assert entry.status == IDLE
    assert entry.bench_items == []
    assert "none on the bench" in render_report(report)


def test_one_idle_profile_among_stocked_profiles():
    report = build_report(ONE_IDLE_BOARD, BENCH)
    profiles = by_profile(report)
    assert profiles["nova"].status == IDLE
    assert [i.title for i in profiles["nova"].bench_items] == [
        "Refresh the sitemap audit",
        "Second nova item",
    ]
    flagged = [entry.profile for entry in report if entry.flagged]
    assert flagged == ["nova"]
    for name in ("orbit", "pixel", "quartz"):
        assert profiles[name].status == OK
        assert profiles[name].bench_items == []
        assert not profiles[name].flagged


def test_all_stocked_profiles_have_no_flags():
    report = build_report(ALL_STOCKED_BOARD, BENCH)
    assert report, "expected profiles in the report"
    for entry in report:
        assert entry.status == OK
        assert not entry.flagged
        assert entry.bench_items == []


def test_below_floor_lists_queued_items_in_bench_order_done_excluded():
    report = build_report(BELOW_FLOOR_BOARD, BENCH)
    profiles = by_profile(report)
    nova = profiles["nova"]
    assert nova.status == BELOW_FLOOR
    assert nova.running == 2
    assert nova.ready == 1
    assert [i.title for i in nova.bench_items] == [
        "Refresh the sitemap audit",
        "Second nova item",
    ]
    assert profiles["orbit"].status == OK


def test_ready_without_running_is_queued_not_flagged():
    report = build_report(BELOW_FLOOR_BOARD, BENCH)
    quartz = by_profile(report)["quartz"]
    assert quartz.status == QUEUED_ONLY
    assert quartz.running == 0
    assert quartz.ready == 2
    assert not quartz.flagged
    assert quartz.bench_items == []


def test_floor_is_configurable():
    rows = [{"assignee": "nova", "status": "running"},
            {"assignee": "nova", "status": "ready"}]
    assert build_report(rows, [], floor=1)[0].status == OK
    assert build_report(rows, [], floor=2)[0].status == BELOW_FLOOR
    assert profile_status(1, 0, 0) == OK


def test_done_and_other_statuses_do_not_count():
    rows = [
        {"assignee": "nova", "status": "done"},
        {"assignee": "nova", "status": "blocked"},
        {"assignee": "nova", "status": "archived"},
    ]
    (entry,) = build_report(rows, [])
    assert entry.running == 0
    assert entry.ready == 0
    assert entry.status == IDLE


def test_rendered_report_shape():
    text = render_report(build_report(BELOW_FLOOR_BOARD, BENCH), floor=2)
    assert "nova: running=2 ready=1 [BELOW FLOOR]" in text
    assert "orbit: running=1 ready=2 [OK]" in text
    assert "- Refresh the sitemap audit (Source: ~/workspace/seo/audit-plan.md)" in text
