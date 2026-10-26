#!/usr/bin/env python3
"""Copy/voice checker — the operator's voice rules as a linter.

Origin: prose-to-checkers audit, Oct 25, 2026. The rules lived as prose notes
and shipped wrong at least once (an unbackable certification claim went
live). Surfaces:
  site  — your public site copy: positive voice,
          no negative constructions, no apologetic filler, no certification
          claims we cannot back.
  email — customer email voice: the operator's real sent-mail patterns,
          the banned phrases list.
  owner — documents written for/to the operator: no framework jargon,
          no em-dash prose, no honesty framing (the operator's standing rule).
All surfaces: the repeated-six digit sequence is banned anywhere (checked as
a pattern: the same digit six times in a row).

Usage: check-copy.py <file> [...] --surface site|email|owner [--json]
Exit 1 on any FAIL. WARNs are judgment calls, printed for the human.
"""
import argparse, json, re, sys
from pathlib import Path

COMMON_FAIL = {
    "repeated-six digit sequence": r"(\d)\1{5}",
}
SURFACES = {
    "site": {
        "fail": {
            "apologetic filler": r"(?i)\b(sorry|unfortunately|we apologize|apologies)\b",
            "certification claim": r"(?i)\bcertified (data destruction|destruction|wiping)\b",
        },
        "warn": {
            "negative construction (positive voice rule; allowlist legal/terms files)": r"\b(never|without|not|no)\b|\b(don|won|can)'t\b",
            "hedging word": r"(?i)\b(maybe|perhaps|just|simply)\b",
        },
    },
    "email": {
        "fail": {
            "banned phrase": r"(?i)straight from me|going all-in|on your own clock|your name kept coming up|more soon,",
            "honesty framing": r"(?i)\bhonest(ly)?\b",
        },
        "warn": {
            "superlative": r"(?i)\b(best|amazing|incredible|world-class|unbeatable|ultimate)\b",
            "insider language": r"(?i)\b(synergy|leverage|utilize|circle back)\b",
        },
    },
    "owner": {
        "fail": {
            "em dash in the operator's documents": r"—",
            "honesty framing": r"(?i)\bhonest(ly)?\b",
        },
        "warn": {
            "framework jargon (never use with the operator)": r"(?i)\b(projector|manifestor|reflector|splenic| sacral |defined center|incarnation cross)\b",
        },
    },
}


def main():
    ap = argparse.ArgumentParser(description="Lint copy against the operator's voice rules for the given surface.")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--surface", required=True, choices=list(SURFACES))
    ap.add_argument("--allowlist", default=None, help="file of basename patterns (one per line, fnmatch) exempt from WARN findings — legal/terms pages")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rules = SURFACES[a.surface]
    allow = []
    if a.allowlist:
        allow = [l.strip() for l in Path(a.allowlist).read_text().splitlines() if l.strip()]
    import fnmatch
    out = []
    for path in a.files:
        exempt_warn = any(fnmatch.fnmatch(Path(path).name, pat) for pat in allow)
        text = Path(path).read_text(errors="ignore")
        for name, pat in {**COMMON_FAIL, **rules["fail"]}.items():
            for m in re.finditer(pat, text, re.I):
                out.append(("FAIL", f"{path}:{text[:m.start()].count(chr(10)) + 1} {name}: ...{text[max(0, m.start()-30):m.end()+30].strip()}..."))
        if not exempt_warn:
            for name, pat in rules["warn"].items():
                for m in re.finditer(pat, text, re.I):
                    out.append(("WARN", f"{path}:{text[:m.start()].count(chr(10)) + 1} {name}"))
    fails = [o for o in out if o[0] == "FAIL"]
    if a.json:
        print(json.dumps({"surface": a.surface, "ok": not fails, "findings": [{"level": l, "msg": m} for l, m in out]}, indent=2))
    else:
        for level, msg in out:
            print(f"[{level}] {msg}")
        print(f"\n{'FAIL' if fails else 'PASS'} — surface {a.surface}, {len(fails)} hard finding(s), {sum(1 for o in out if o[0] == 'WARN')} warning(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
