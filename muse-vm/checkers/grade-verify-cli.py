#!/usr/bin/env python3
"""Verify-CLI grader — the verify-skill checklist as a score.

Origin: prose-to-checkers audit, Oct 25, 2026. The verify-skill standard says builders are
"graded against" the control-CLI checklist; grading was by eye. This grades
the decidable parts: skill structure (SKILL.md sections, features/README,
evidence dir), CLI surface (--help exits 0, teaches, names doctor; doctor
appears in help), and, when --doctor-cmd is given, the doctor itself: exit 0
+ JSON object output. Deeper properties (adversarial exit codes) stay with the
per-skill proof, whose doctor command the registry names.

Usage: grade-verify-cli.py <skill-dir> --cli <path> [--doctor-cmd "..."] [--json]
Exit 1 on any FAIL.
"""
import argparse, json, re, subprocess, sys
from pathlib import Path

SECTIONS = ["Launch", "Doctor", "Drive", "Evidence", "Cleanup", "Helpers"]


def run(cmd, timeout=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    ap = argparse.ArgumentParser(description="Grade a generated verify skill + control CLI against the verify-skill checklist.")
    ap.add_argument("dir")
    ap.add_argument("--cli", required=True)
    ap.add_argument("--doctor-cmd", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    d = Path(a.dir)
    out = []
    skill = d / "SKILL.md"
    if skill.exists():
        text = skill.read_text()
        missing = [s for s in SECTIONS if not re.search(rf"^##\s+.*{s}", text, re.M | re.I)]
        out.append(("FAIL" if missing else "OK", f"SKILL.md sections missing: {missing}" if missing else "SKILL.md has all six sections"))
        ph = len(re.findall(r"(?i)todo|tbd|placeholder|lorem", text))
        out.append(("WARN" if ph else "OK", f"{ph} placeholder marker(s) in SKILL.md" if ph else "no placeholder markers in SKILL.md"))
    else:
        out.append(("FAIL", "SKILL.md missing"))
    out.append(("OK" if (d / "features" / "README.md").exists() else "FAIL", "features/README.md index present" if (d / "features" / "README.md").exists() else "features/README.md missing"))
    ev = d / "evidence"
    out.append(("OK" if ev.exists() and any(ev.rglob("*")) else "WARN", "evidence/ holds artifacts" if ev.exists() and any(ev.rglob("*")) else "evidence/ missing or empty"))
    cli = Path(a.cli)
    if not cli.exists():
        out.append(("FAIL", f"control CLI not found at {a.cli}"))
    else:
        rc, help_out = run(f"python3 {cli} --help")
        out.append(("OK" if rc == 0 else "FAIL", f"--help exits {rc}"))
        out.append(("OK" if "doctor" in help_out.lower() else "FAIL", "help names a doctor subcommand" if "doctor" in help_out.lower() else "no doctor subcommand in --help"))
        out.append(("OK" if len(help_out) > 200 else "WARN", "--help teaches (substantive text)" if len(help_out) > 200 else "--help is thin"))
    if a.doctor_cmd:
        rc, doc_out = run(a.doctor_cmd)
        out.append(("OK" if rc == 0 else "FAIL", f"doctor exits {rc}"))
        try:
            obj = json.loads(doc_out.strip().splitlines()[-1] if doc_out.strip() else "")
            out.append(("OK" if isinstance(obj, dict) else "WARN", "doctor output parses as a JSON object" if isinstance(obj, dict) else "doctor output is not a JSON object"))
        except Exception:
            out.append(("WARN", "doctor output did not parse as JSON (last line)"))
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
