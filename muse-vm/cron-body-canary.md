# Canary cron body — hermes-muse-bridge

Copy the task body below into a Muse cron job that runs every 1 hour.
Install `fleet-canary.sh` to `~/workspace/bin/` first.

---

Hourly bridge health pass — three scripts, in this order:

1. `~/workspace/bin/bridge-maint.sh` — housekeeping (audit Nov 9, decision 1): prunes the event spool to 48h on both machines and rotates bridge logs past 5 MB. Silent on success (one BRIDGE-MAINT summary line). If it errors, note the line in the daily log; do not message Michael for a maint error alone.

2. `~/workspace/bin/fleet-canary.sh` — end-to-end probe of the event path: plants a synthetic event on the box and confirms it travels the real path (box POST → reverse tunnel → listener → push inbox) and that the kanban-events daemon is alive on the box. On failure the script already runs the known recovery once (install-muse-units.sh) and retries before reporting FAIL.

3. `~/workspace/bin/fleet-request-canary.sh` — end-to-end probe of the request path (audit Nov 9, decision 3): plants a synthetic request in the box muse-inbox and times the watcher's response (target <300s, FAIL at 900s), checks watcher silence (no completed run in >10 min = FAIL), and guards spool size (<1 GB total) and box disk (<92%). Its PASS line carries latency, spool MB, disk %, and the quarantine count — log that line to the daily log.

Reporting:
- All three pass: log one line to ~/memory/YYYY-MM-DD.md (include the request-canary PASS numbers) and stay silent.
- Event canary FAIL: the path is broken even after recovery. Report to Michael in one short message: which leg failed (the script's line says it), what recovery was attempted, and that fleet events are currently arriving only via the 1-minute DB tail fallback (slower, not lost). Do not attempt further repair beyond the script's built-in recovery — escalate.
- Request canary FAIL: report to Michael in one short message naming the failed leg (request path / watcher silence / spool guard / disk guard). A request-path failure means fleet agents' call-muse requests are going unanswered — the Nov 8 outage shape. Do not attempt repair beyond noting it — escalate.
---
