# verify-widget A2: doctor proof (proof-only sub-piece)
This task is one chained piece (piece a2). Run the existing scripts/control-widget.py doctor live and one adversarial wrong-port run; write outputs into skills/verify-widget/evidence/doctor/ and fill report.md.
Human-equivalent estimate: ~15 min
Regression command: python3 scripts/control-widget.py doctor --port 9999 (must stay non-zero)
## Acceptance criteria
1. doctor exits 0 live
2. adversarial doctor exits non-zero
3. evidence files present, report.md zero TBD
