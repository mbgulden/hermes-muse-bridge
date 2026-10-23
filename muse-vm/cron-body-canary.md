# Canary cron body — hermes-muse-bridge

Copy the task body below into a Muse cron job that runs every 1 hour.
Install `fleet-canary.sh` to `~/workspace/bin/` first.

---

Hourly end-to-end canary for the Hermes→Jimmy event path. Run `~/workspace/bin/fleet-canary.sh` — it plants a synthetic event on the box and confirms it travels the real path (box POST → reverse tunnel → listener → push inbox) and that the kanban-events daemon is alive on the box. On failure the script already runs the known recovery once (install-jimmy-units.sh) and retries before reporting FAIL.

- Output "CANARY PASS": log one line to ~/memory/YYYY-MM-DD.md and stay silent. Nothing else.
- Output "CANARY FAIL: ...": the path is broken even after recovery. Report to the user in one short message: which leg failed (the script's line says it), what recovery was attempted, and that fleet events are currently arriving only via the 1-minute DB tail fallback (slower, not lost). Do not attempt further repair beyond the script's built-in recovery — escalate.
