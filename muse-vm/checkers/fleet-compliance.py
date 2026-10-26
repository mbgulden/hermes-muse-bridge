#!/usr/bin/env python3
"""Fleet compliance pack — three worker rules that used to fail silently.

Origin: prose-to-checkers audit, Oct 25, 2026. Run ON the Hermes box against
the kanban DB (read-only). Checks the last N days of runs:
  1. Close-out completeness: a run that ended (any outcome) must be
     acknowledged on the record: any comment at/after its end (worker,
     orchestrator, or reviewer — calibrated Oct 25: board comment authors
     are profile paths and reviewer names, never assignee names, and
     timed_out-by-design proof tasks are acknowledged via orchestrator
     comments) or a result on the task. Neither = SILENT EXIT (the silent
     failure shape: caps hit, nothing on file, invisible for hours).
  2. Restate-first: tasks whose body requires the in-your-own-words
     restatement must have, as their first worker comment, a restatement
     (heuristic: comment mentions goal + what done looks like).
  3. Deliverable-first: tasks whose body names a deliverable file path must
     show that file existing with mtime within 20 min after the run start
     (or before the run — salvage). Missing file = NOT CREATED FIRST (or
     never). File checks resolve paths on this host.
Usage: fleet-compliance.py [--db ~/.hermes/kanban.db] [--days 3] [--json]
Exit 1 if any SILENT EXIT findings.
"""
import argparse, json, os, re, sqlite3, sys, time

RESTATE_MARKERS = ("in-your-own-words", "restate", "restatement")


def main():
    ap = argparse.ArgumentParser(description="Check fleet runs for close-out, restate-first, and deliverable-first compliance.")
    ap.add_argument("--db", default="~/.hermes/kanban.db")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    a.db = os.path.expanduser(a.db)
    db = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    since = int(time.time()) - a.days * 86400
    out = []
    runs = db.execute(
        "SELECT tr.id, tr.task_id, tr.status, tr.outcome, tr.started_at, tr.ended_at, t.assignee, t.body, t.result "
        "FROM task_runs tr JOIN tasks t ON t.id = tr.task_id WHERE tr.started_at >= ? AND tr.ended_at IS NOT NULL",
        (since,)).fetchall()
    for rid, tid, status, outcome, started, ended, assignee, body, result in runs:
        comments = db.execute(
            "SELECT author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY created_at",
            (tid,)).fetchall()
        worker_comments = [c for c in comments if assignee in (c[0] or "")]
        # 1. close-out (calibrated Oct 25): any comment at/after run end, or a result
        closeout = any(c[2] >= (ended or 0) - 300 for c in comments)
        if not closeout and not result:
            out.append(("FAIL", f"run {rid} ({tid}, {assignee}, outcome {outcome}) SILENT EXIT: no comment at close-out and no result"))
        # 2. restate-first
        if body and any(m in body.lower() for m in RESTATE_MARKERS):
            first = worker_comments[0] if worker_comments else None
            if first and first[2] >= started:
                txt = (first[1] or "").lower()
                ok = "goal" in txt and ("done" in txt or "looks like" in txt)
                out.append(("OK" if ok else "WARN", f"run {rid} ({tid}) restate-first: {'restatement is the first comment' if ok else 'first comment does not read as a restatement'}"))
            elif first:
                out.append(("WARN", f"run {rid} ({tid}) restate-first: first worker comment predates this run (prior attempt) — check manually"))
        # 3. deliverable-first
        if body:
            m = re.search(r"[Dd]eliverable[^:\n]*:\s*`?(/[^\s`]+)`?", body)
            if m and started:
                p = m.group(1).rstrip(".,)")
                if os.path.exists(p):
                    mt = os.path.getmtime(p)
                    if started - 3600 <= mt <= started + 1200:
                        out.append(("OK", f"run {rid} ({tid}) deliverable-first: {p} created/updated in the opening window"))
                    elif mt < started - 3600:
                        out.append(("OK", f"run {rid} ({tid}) deliverable-first: {p} predates the run (salvage/continuation)"))
                    else:
                        out.append(("WARN", f"run {rid} ({tid}) deliverable-first: {p} first touched {(mt - started) // 60} min after run start"))
                else:
                    out.append(("WARN", f"run {rid} ({tid}) deliverable-first: named deliverable {p} not found on this host"))
    fails = [o for o in out if o[0] == "FAIL"]
    if a.json:
        print(json.dumps({"runs_checked": len(runs), "ok": not fails, "findings": [{"level": l, "msg": m} for l, m in out]}, indent=2))
    else:
        for level, msg in out:
            print(f"[{level}] {msg}")
        print(f"\n{'FAIL' if fails else 'PASS'} — {len(runs)} finished runs checked, {len(fails)} silent exit(s), {sum(1 for o in out if o[0] == 'WARN')} warning(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
