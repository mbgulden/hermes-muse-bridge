#!/usr/bin/env python3
"""Leak scan — the standard secret/PII gate for deliverables and commits.

Origin: prose-to-checkers audit, Oct 25, 2026. Reviews used to run this grep
by hand (the HD verify review did); now it is one pattern set, run the same
way everywhere: worker self-check before reporting, the reviewer at review, OKF sync
content scan (check-okf-sync.py reuses these patterns).

Usage: check-leaks.py <file-or-dir> [...] [--json]
FAIL = credential-shaped material. WARN = PII-shaped material (judge context).
Exit 1 on any FAIL.
"""
import argparse, json, re, sys
from pathlib import Path

FAIL_PATTERNS = {
    "private key block": r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "OpenAI-style key": r"\bsk-[A-Za-z0-9_-]{20,}",
    "GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    "AWS access key": r"\bAKIA[0-9A-Z]{16}\b",
    "Slack token": r"\bxox[baprs]-[A-Za-z0-9-]{10,}",
    "Tailscale key": r"\btskey-(api|auth)-[A-Za-z0-9-]{10,}",
    "Stripe live key": r"\b(sk|rk)_live_[A-Za-z0-9]{16,}",
    "credential assignment": r"(?i)\b(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']",
    "bearer token": r"(?i)\bauthorization:\s*bearer\s+[A-Za-z0-9._-]{16,}",
}
WARN_PATTERNS = {
    "unquoted credential assignment": r"(?im)^\s*[A-Za-z0-9_]*?(api[_-]?key|secret|password|passwd|token)[A-Za-z0-9_]*\s*=\s*[^\s\"']{12,}",
    "email address": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    "long digit run (account/card-like)": r"\b\d{12,19}\b",
    "US phone-like": r"\b\d{3}[-.]\d{3}[-.]\d{4}\b",
}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".venv_dev"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".tgz", ".bin", ".ico"}


def scan_text(text, label, out):
    for name, pat in FAIL_PATTERNS.items():
        for m in re.finditer(pat, text):
            line = text[:m.start()].count("\n") + 1
            out.append(("FAIL", f"{label}:{line} {name}"))
    for name, pat in WARN_PATTERNS.items():
        for m in re.finditer(pat, text):
            line = text[:m.start()].count("\n") + 1
            out.append(("WARN", f"{label}:{line} {name}"))


def files_of(path):
    p = Path(path)
    if p.is_file():
        yield p
    else:
        for f in sorted(p.rglob("*")):
            if f.is_file() and not (set(f.parts) & SKIP_DIRS) and f.suffix.lower() not in BINARY_EXT:
                yield f


def main():
    ap = argparse.ArgumentParser(description="Scan files for credential-shaped (FAIL) and PII-shaped (WARN) material.")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out, scanned = [], 0
    for path in a.paths:
        for f in files_of(path):
            try:
                text = f.read_text(errors="ignore")
            except Exception:
                continue
            scanned += 1
            scan_text(text, str(f), out)
    fails = [o for o in out if o[0] == "FAIL"]
    if a.json:
        print(json.dumps({"scanned": scanned, "ok": not fails, "findings": [{"level": l, "msg": m} for l, m in out]}, indent=2))
    else:
        for level, msg in out:
            print(f"[{level}] {msg}")
        print(f"\n{'FAIL' if fails else 'PASS'} — {scanned} files scanned, {len(fails)} credential finding(s), {sum(1 for o in out if o[0] == 'WARN')} PII warning(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
