# verify-widget A: salvage, control CLI + doctor proof
This task is one chained piece (piece A of the verify-widget chain).
Build scripts/control-widget.py with a doctor subcommand, prove doctor exit 0 live plus one adversarial wrong-port run, and fill skills/verify-widget/evidence/doctor/report.md to zero TBD.
Human-equivalent estimate: ~25 min
## Acceptance criteria
1. scripts/control-widget.py exists and --help exits 0
2. doctor exits 0 live, output in evidence/
3. adversarial doctor exits non-zero
4. report.md has zero TBD markers
