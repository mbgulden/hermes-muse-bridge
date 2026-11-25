# Bench file format

A bench file lists work items that are waiting to be authored into
kanban tasks. The stocking monitor reads bench files to name the next
items for any profile that falls below its floor or sits idle. One
program keeps one bench file (or a few, passed to the monitor in
order); entries are listed in the order they should be authored.

## Entry shape

Each entry is a level-2 markdown heading carrying the item title,
followed by two required field lines and one optional field line:

- `Source: <paths or links>` — the files or links the task author
  works from when turning the item into a task body.
- `State: QUEUED` or `State: DONE` — QUEUED items are waiting; DONE
  items are written history and are never listed by the monitor.
- `Owner: <profile>` — optional: the worker profile the item is
  destined for. An entry without an `Owner:` line parses as
  unassigned (`owner == "unassigned"`) and is never offered to any
  profile by the monitor.

Rules the parser enforces:

- The Source and State field lines are required; a missing or repeated
  field line (of any kind) is a format error. A missing Owner line is
  not an error — it parses as unassigned.
- State must be exactly `QUEUED` or `DONE`; anything else is a
  format error.
- Blank lines and note lines inside an entry are ignored, so an
  entry may carry a short note under its field lines.
- Content before the first `##` heading (a `#` file title, an
  introduction) belongs to no entry and is skipped.
- Entries appear in the file in authoring order; the monitor lists
  a profile's QUEUED items in that order.

When an item is authored into a task, flip its State to DONE (or
delete the entry) so the monitor stops offering it.

## Worked example

```markdown
# Bench — example program

Pre-specced work waiting for an author. Newest priorities last.

## Refresh the sitemap audit
Owner: nova
Source: ~/workspace/seo/audit-plan.md, ~/workspace/seo/last-run.md
State: QUEUED

## Draft the December newsletter
Owner: orbit
Source: ~/workspace/mail/newsletter-brief.md
State: QUEUED

Note: waits on the gift-card dates in the brief.

## Verify the backup rotation
Owner: nova
Source: ~/workspace/ops/backup-runbook.md
State: DONE
```

Parsed, this file yields three entries. The monitor offers "Refresh
the sitemap audit" for profile nova and "Draft the December
newsletter" for profile orbit when those profiles need stocking;
the DONE entry is never offered.
