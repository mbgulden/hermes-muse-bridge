#!/usr/bin/env python3
"""Task-size checker — the mechanical gate behind the sizing standard.

Origin: Oct 25, 2026. Three verify-skill generation tasks were authored
as single shots (control CLI + feature map + SKILL.md + drive + commit in one
2h task). All three died at the cap twice with no report filed — while the
sizing standard and calibration data already on file said tasks that size land
at 35–46% first-attempt. The standard existed as prose; nothing checked it at
authoring time. This script is the check. The reviewer runs it on every task body
before `hermes kanban create`; a FAIL means decompose first, no exceptions
without the user's explicit override recorded in the task body.

Usage: check-task-size.py <body-file> [--cap-minutes N] [--json]
Exit 0 = size OK (warnings allowed); 1 = oversized / malformed.
"""
import argparse, json, re, sys
from pathlib import Path

ARTIFACTS = {
    "control CLI / script": r"control[- ]\w+\.(py|mjs)|control CLI",
    "feature map": r"feature map|features/",
    "SKILL.md / skill doc": r"SKILL\.md",
    "evidence set": r"evidence/",
    "commit / push": r"\bcommit\b|\bpush\b",
    "research / audit report": r"research report|audit report|REPORT\.md",
    "test suite": r"test suite|tests/",
}
CHAIN_MARKERS = r"piece [abc][12]?\b|one piece|this task is one|chained (task|piece)|decomposed"
SUBPIECE_MARKERS = r"piece a[12]\b|build[- ]only|proof[- ]only|sub[- ]piece"


def main():
    ap = argparse.ArgumentParser(description="Validate a kanban task body against the sizing standard.")
    ap.add_argument("body")
    ap.add_argument("--cap-minutes", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    text = Path(a.body).read_text()
    low = text.lower()
    results = []

    found = [name for name, pat in ARTIFACTS.items() if re.search(pat, low)]
    chained = re.search(CHAIN_MARKERS, low) is not None
    if len(found) >= 3 and not chained:
        results.append(("FAIL", f"multi-artifact task ({len(found)} deliverables: {', '.join(found)}) with no chain marker — decompose into chained single-artifact pieces (A/B/C shape) before creating"))
    elif len(found) >= 3 and chained:
        results.append(("OK", f"{len(found)} artifacts named but body declares itself one chained piece"))
    else:
        results.append(("OK", f"deliverable count {len(found)} ({', '.join(found) or 'none named'})"))

    # Build+proof bundle check (added Oct 25, 2026, evening): the original
    # chain rule defined piece A as "core artifact + its proof" and this
    # checker's chain-marker escape let it pass. Result: chain A went 0-for-3
    # on worker completions across the first verify builds (one piece finished
    # reviewer-direct; the rest all timed out) — building a
    # program from scratch AND proving it live are two session-sized pieces.
    # A chained piece that both builds code and runs its proof must split.
    build_present = "control CLI / script" in found and re.search(r"\b(build|write|create|implement|author)\b", low)
    proof_present = "evidence set" in found or "doctor" in low or "adversarial" in low
    subpiece = re.search(SUBPIECE_MARKERS, low) is not None
    if build_present and proof_present and not subpiece:
        results.append(("FAIL", "build+proof bundled in one piece — building the artifact and proving it are separate pieces: split into A1 (build only: artifact exists + smoke check) and A2 (proof only: live run + adversarial + evidence)"))
    elif build_present and proof_present and subpiece:
        results.append(("OK", "build+proof named but body declares a build-only/proof-only sub-piece"))

    # Human-equivalent estimate (added Oct 25, 2026, evening — evidence
    # review): every body carries one; it is the x-axis of the fleet's own
    # success-vs-length curve. Build/generation pieces sized over the
    # 10–20 min target band FAIL above 30 min human-equivalent (hard-split
    # line); read-only work only warns, per the Oct 22 calibration.
    em = re.search(r"human[- ]equivalent[^.\n]{0,50}?(\d+)\s*(?:min|minute)", low)
    if not em:
        results.append(("FAIL", "no human-equivalent estimate — add a line like 'Human-equivalent estimate: ~15 min' (the fleet's own success-vs-length curve is fitted from these)"))
    else:
        est = int(em.group(1))
        if build_present and est > 30:
            results.append(("FAIL", f"human-equivalent estimate {est} min on a build piece — hard-split line is 30 min human-equivalent; decompose"))
        elif est > 20:
            results.append(("WARN", f"human-equivalent estimate {est} min — above the 10–20 min target band for fleet pieces"))
        else:
            results.append(("OK", f"human-equivalent estimate {est} min"))

    # Regression command (same evidence pass): code-editing tasks name a
    # pass-to-pass regression command alongside the success command; the
    # reviewer re-runs both. Warning only — some scripts have no suite yet.
    edits_code = "control CLI / script" in found or "test suite" in found
    if edits_code and not re.search(r"regression|pass[- ]to[- ]pass", low):
        results.append(("WARN", "code task names no regression (pass-to-pass) command — add one beside the success command; criteria are pre-registered and not worker-editable"))

    m = re.search(r"acceptance criteria(.*?)(?:\n## |\Z)", text, re.S | re.I)
    crit = 0
    if m:
        crit = len(re.findall(r"^\s*(?:\d+\.|[-*])\s", m.group(1), re.M))
    if crit == 0:
        results.append(("FAIL", "no acceptance criteria found — 3–5 binary, machine-checkable criteria are required"))
    elif crit > 5:
        results.append(("FAIL", f"{crit} acceptance criteria — the standard is 3–5; more criteria means more than one task"))
    else:
        results.append(("OK", f"{crit} acceptance criteria"))

    if a.cap_minutes is not None:
        if a.cap_minutes >= 120:
            results.append(("WARN", f"cap is {a.cap_minutes}m — 2h is headroom, never the size (calibration: 15m–2h tasks land 35–46% first-attempt; 69% of wins finish ≤40m). If this task plausibly needs the cap, it is too big"))
        else:
            results.append(("OK", f"cap {a.cap_minutes}m"))

    if "and also" in low:
        results.append(("WARN", "body joins work with 'and also' — the classic bundle tell; check it is not two deliverables"))

    fails = [r for r in results if r[0] == "FAIL"]
    if a.json:
        print(json.dumps({"body": a.body, "ok": not fails, "checks": [{"level": l, "msg": m} for l, m in results]}, indent=2))
    else:
        print(f"# check-task-size: {a.body}")
        for level, msg in results:
            print(f"[{level}] {msg}")
        print(f"\n{'PASS' if not fails else 'FAIL'} — decompose before creating" if fails else "\nPASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
