#!/usr/bin/env python3
"""Port linter — the pstack port standard as a checker.

Origin: prose-to-checkers audit, Oct 25, 2026. The port standard was verified
by eye at every review; with 7+ ports queued, reviews now start from this
output. Checks a ported skill dir (SKILL.md + references/) against the
formal-pair pattern: frontmatter carries name (pstack-*), upstream path +
pinned SHA, port_notes; body has zero Cursor-isms; references are non-empty;
a PORT-REPORT.md sits beside it in the staging shape (waived with --installed
for skills already installed in custom-skills / fleet profiles).

Usage: check-port.py <skill-dir> [--installed] [--json]
"""
import argparse, json, re, sys
from pathlib import Path

CURSORISMS = [r"cursor", r"/poteto-mode", r"\bgrok\b", r"\.cursor/"]


def main():
    ap = argparse.ArgumentParser(description="Lint a ported pstack skill dir against the port standard.")
    ap.add_argument("dir")
    ap.add_argument("--installed", action="store_true", help="skill is installed (no PORT-REPORT.md required)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    d = Path(a.dir)
    out = []
    skill = d / "SKILL.md"
    if not skill.exists():
        out.append(("FAIL", "SKILL.md missing"))
        print("FAIL — no SKILL.md"); return 1
    text = skill.read_text()
    m = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.S)
    if not m:
        out.append(("FAIL", "no frontmatter block"))
        fm, body = "", text
    else:
        fm, body = m.group(1), m.group(2)
        for field, pat in [("name pstack-*", r"name:\s*pstack-[\w-]+"),
                           ("upstream path + SHA", r"upstream:\s*\S+[\s\S]{0,200}?[0-9a-f]{7,}"),
                           ("port_notes", r"port_notes:")]:
            ok = re.search(pat, fm) is not None
            out.append(("OK" if ok else "FAIL", f"frontmatter: {field}" if ok else f"frontmatter missing: {field}"))
        desc = re.search(r"description:\s*\"?(.+)", fm)
        out.append(("OK" if desc and len(desc.group(1)) > 40 else "WARN", "frontmatter description is substantive" if desc and len(desc.group(1)) > 40 else "description missing or thin"))
    hits = [p for p in CURSORISMS if re.search(p, body, re.I)]
    out.append(("FAIL" if hits else "OK", f"Cursor-isms in body: {hits}" if hits else "body clean of Cursor-isms"))
    refs = d / "references"
    if refs.exists():
        files = [f for f in refs.rglob("*") if f.is_file()]
        empty = [str(f) for f in files if f.stat().st_size == 0]
        out.append(("FAIL" if empty else "OK", f"empty reference files: {empty}" if empty else f"{len(files)} reference file(s), all non-empty"))
    if not a.installed:
        rep = d.parent / "PORT-REPORT.md" if d.name.startswith("pstack-") else d / "PORT-REPORT.md"
        rep2 = d / "PORT-REPORT.md"
        ok = rep.exists() or rep2.exists()
        out.append(("OK" if ok else "FAIL", "PORT-REPORT.md present" if ok else "PORT-REPORT.md missing beside the port (staging shape)"))
    fails = [o for o in out if o[0] == "FAIL"]
    if a.json:
        print(json.dumps({"dir": a.dir, "ok": not fails, "checks": [{"level": l, "msg": m} for l, m in out]}, indent=2))
    else:
        for level, msg in out:
            print(f"[{level}] {msg}")
        print(f"\n{'FAIL' if fails else 'PASS'} — {len(fails)} hard failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
