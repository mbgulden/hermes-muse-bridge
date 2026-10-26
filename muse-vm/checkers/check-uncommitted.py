#!/usr/bin/env python3
"""Uncommitted-fix sweep — "any local fixes always need to be committed."

Origin: prose-to-checkers audit, Oct 25, 2026 (the operator's standing rule, Oct 16, was
pure prose). Scans git repos under the given roots; flags repos whose oldest
uncommitted change is older than --hours (default 24). A "done" that lives
only on a box is not done — this makes it visible without memory.

Usage: check-uncommitted.py [--roots ~/workspace ...] [--hours 24] [--json]
Exit 1 if any repo is flagged.
"""
import argparse, json, os, subprocess, sys, time
from pathlib import Path


def repos_under(root, depth=3):
    root = Path(root).expanduser()
    found = []
    for dirpath, dirnames, _ in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        if ".git" in dirnames:
            found.append(Path(dirpath))
            dirnames[:] = []
        if len(rel.parts) >= depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", "__pycache__")]
    return found


def main():
    ap = argparse.ArgumentParser(description="Flag repos with uncommitted changes older than the threshold.")
    ap.add_argument("--roots", nargs="+", default=["~/workspace"])
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--registry", default=str(Path(__file__).parent / "repo-registry.tsv"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    import fnmatch
    registry = []
    rp = Path(a.registry).expanduser()
    if rp.exists():
        for line in rp.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                parts = line.split("\t")
                if len(parts) >= 2:
                    registry.append((parts[0], parts[1]))

    def disposition(repo):
        for pat, disp in registry:
            if fnmatch.fnmatch(str(repo), pat):
                return disp
        return "live"
    cutoff = time.time() - a.hours * 3600
    out, total = [], 0
    for root in a.roots:
        for repo in repos_under(root):
            total += 1
            disp = disposition(repo)
            r = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True)
            if r.returncode != 0:
                continue
            lines = [l for l in r.stdout.splitlines() if l.strip()]
            if not lines:
                continue
            oldest = None
            for l in lines:
                p = repo / l[3:].split(" -> ")[-1]
                if p.exists():
                    mt = p.stat().st_mtime
                    oldest = mt if oldest is None else min(oldest, mt)
            if disp != "live":
                out.append(("INFO", f"{repo}: {len(lines)} uncommitted change(s) — disposition {disp}, not gated"))
            elif oldest and oldest < cutoff:
                age_h = (time.time() - oldest) / 3600
                out.append(("FAIL", f"{repo}: {len(lines)} uncommitted change(s), oldest {age_h:.0f}h old"))
            elif lines:
                out.append(("WARN", f"{repo}: {len(lines)} uncommitted change(s), all under {a.hours}h old"))
    fails = [o for o in out if o[0] == "FAIL"]
    if a.json:
        print(json.dumps({"repos_scanned": total, "ok": not fails, "findings": [{"level": l, "msg": m} for l, m in out]}, indent=2))
    else:
        for level, msg in out:
            print(f"[{level}] {msg}")
        print(f"\n{'FAIL' if fails else 'PASS'} — {total} repos scanned, {len(fails)} stale repo(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
