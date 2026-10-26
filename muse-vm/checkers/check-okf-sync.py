#!/usr/bin/env python3
"""OKF sync content scan — PII protection beyond path exclusions.

Origin: prose-to-checkers audit, Oct 25, 2026. The OKF sync excludes raw
customer data by PATH; nothing scanned the CONTENT of what syncs. This runs
the standard leak patterns (check-leaks.py) over the sync's source trees.
Deliberate exclusion: document trees the operator intentionally
publishes to the hub (archived financial records, in our deployment) are
NOT scanned here — this scan covers the OKF thread tree, which
is the surface that grows by accretion.

Usage: check-okf-sync.py [--root ~/workspace/okf] [--json]
Exit 1 on any credential FAIL from the leak scan.
"""
import argparse, subprocess, sys
from pathlib import Path

HERE = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser(description="Content-scan the OKF sync source tree with the standard leak patterns.")
    ap.add_argument("--root", default="~/workspace/okf")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).expanduser()
    cmd = [sys.executable, str(HERE / "check-leaks.py"), str(root)]
    if a.json:
        cmd.append("--json")
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
