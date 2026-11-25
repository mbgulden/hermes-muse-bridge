"""Tests for the bench-file parser (format contract: BENCH.md)."""

import pytest

from fleetops.stocking.bench import (
    DONE,
    QUEUED,
    UNASSIGNED,
    parse_bench_text,
)

from fixtures import BENCH_TEXT


def test_parses_every_entry_in_file_order():
    items = parse_bench_text(BENCH_TEXT, origin="fixture")
    assert [item.title for item in items] == [
        "Refresh the sitemap audit",
        "Draft the December newsletter",
        "Second nova item",
        "Finished nova item",
        "Quartz bench item",
    ]


def test_entry_fields_and_states():
    items = parse_bench_text(BENCH_TEXT)
    first = items[0]
    assert first.owner == "nova"
    assert first.source == "~/workspace/seo/audit-plan.md"
    assert first.state == QUEUED
    assert first.is_queued
    finished = items[3]
    assert finished.state == DONE
    assert not finished.is_queued


def test_content_before_first_heading_is_skipped():
    text = "# Bench — title\n\nAn introduction.\n\n" + BENCH_TEXT.split("\n\n", 1)[1]
    items = parse_bench_text(text)
    assert items[0].title == "Refresh the sitemap audit"


def test_note_lines_inside_an_entry_are_ignored():
    text = (
        "## Item with a note\n"
        "Owner: nova\n"
        "Source: ~/workspace/x.md\n"
        "State: QUEUED\n"
        "\n"
        "Note: waits on the dates in the source.\n"
    )
    (item,) = parse_bench_text(text)
    assert item.title == "Item with a note"
    assert item.state == QUEUED


def test_missing_source_line_is_a_format_error():
    text = "## Broken item\nOwner: nova\nState: QUEUED\n"
    with pytest.raises(ValueError, match="Source"):
        parse_bench_text(text)


def test_missing_state_line_is_a_format_error():
    text = "## Broken item\nOwner: nova\nSource: ~/workspace/x.md\n"
    with pytest.raises(ValueError, match="State"):
        parse_bench_text(text)


def test_unknown_state_is_a_format_error():
    text = (
        "## Broken item\n"
        "Owner: nova\n"
        "Source: ~/workspace/x.md\n"
        "State: WAITING\n"
    )
    with pytest.raises(ValueError, match="WAITING"):
        parse_bench_text(text)


def test_repeated_field_line_is_a_format_error():
    text = (
        "## Broken item\n"
        "Owner: nova\n"
        "Owner: orbit\n"
        "Source: ~/workspace/x.md\n"
        "State: QUEUED\n"
    )
    with pytest.raises(ValueError, match="repeats"):
        parse_bench_text(text)


def test_empty_text_yields_no_items():
    assert parse_bench_text("") == []
    assert parse_bench_text("# Just a title\n") == []


# --- The fix: an item without an Owner: line parses as unassigned ---------

def test_missing_owner_line_parses_as_unassigned():
    """An item that carries no Owner: line must still parse, as unassigned,
    rather than being dropped as a format error."""
    text = (
        "## Un-owned item\n"
        "Source: ~/workspace/seo/audit-plan.md\n"
        "State: QUEUED\n"
    )
    (item,) = parse_bench_text(text, origin="fixture")
    assert item.title == "Un-owned item"
    assert item.owner == UNASSIGNED
    assert item.source == "~/workspace/seo/audit-plan.md"
    assert item.state == QUEUED
    assert item.is_queued


def test_owned_and_unowned_items_parse_identically_except_owner():
    """Same item with and without an Owner: line yields the same item,
    differing only in the owner field. The owned shape must parse
    byte-identically to before the fix."""
    owned = (
        "## Shared item\n"
        "Owner: nova\n"
        "Source: ~/workspace/seo/audit-plan.md\n"
        "State: QUEUED\n"
    )
    unowned = (
        "## Shared item\n"
        "Source: ~/workspace/seo/audit-plan.md\n"
        "State: QUEUED\n"
    )
    (owned_item,) = parse_bench_text(owned, origin="fixture")
    (unowned_item,) = parse_bench_text(unowned, origin="fixture")
    assert owned_item.title == unowned_item.title
    assert owned_item.source == unowned_item.source
    assert owned_item.state == unowned_item.state
    assert owned_item.origin == unowned_item.origin
    assert owned_item.owner == "nova"
    assert unowned_item.owner == UNASSIGNED
