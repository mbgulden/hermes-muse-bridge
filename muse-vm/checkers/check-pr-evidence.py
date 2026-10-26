#!/usr/bin/env python3
"""Plan<->PR evidence matcher.

Origin: prose-to-checkers audit, Oct 25, 2026. check-plan.py validates a
plan's structure; nothing verified that a PR's evidence is the verification
the plan actually named. This closes that gap at review time: extract the
backticked commands from the plan's Verification section; each must appear
(normalized) in the PR body. Missing = FAIL.

Usage: check-pr-evidence.py --plan <plan.md> --pr-body <file> [--json]
"""
import argparse, json, re, sys
from pathlib import Path


def norm(s):
    return re.sub(r"\s+", " ", s.strip().lower())


def main():
    ap = argparse.ArgumentParser(description="Check a PR body quotes the verification commands its plan named.")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--pr-body", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    plan = Path(a.plan).read_text()
    m = re.search(r"^##\s+Verification(.*?)(?=^##\s+|\Z)", plan, re.S | re.M)
    if not m:
        print("FAIL — plan has no ## Verification section (run check-plan.py first)")
        return 1
    raw = re.findall(r"`([^`]{4,})`", m.group(1))
    tools = ("pytest", "ruff", "python3", "python", "npm", "npx", "grep", "md5sum", "sha256sum", "curl", "git", "make", "cargo", "go test")
    cmds = sorted({c for c in raw if " " in c or c.split()[0] in tools or "|" in c})
    pr = norm(Path(a.pr_body).read_text())
    out = []
    for c in cmds:
        ok = norm(c) in pr
        out.append(("OK" if ok else "FAIL", f"plan command quoted in PR: `{c}`" if ok else f"plan command MISSING from PR body: `{c}`"))
    if not cmds:
        out.append(("WARN", "plan Verification names no backticked commands — nothing to match"))
    smoke = "smoke" in pr
    out.append(("OK" if smoke else "WARN", "PR body mentions smoke-on-head evidence" if smoke else "PR body does not mention smoke — the evidence checklist requires smoke green on the PR head"))
    fails = [o for o in out if o[0] == "FAIL"]
    if a.json:
        print(json.dumps({"ok": not fails, "checks": [{"level": l, "msg": m} for l, m in out]}, indent=2))
    else:
        for level, msg in out:
            print(f"[{level}] {msg}")
        print(f"\n{'FAIL' if fails else 'PASS'} — {len(fails)} missing command(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
