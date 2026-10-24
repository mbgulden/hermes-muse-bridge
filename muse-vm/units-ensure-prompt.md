# Units-ensure hook prompt — hermes-muse-bridge

The prompt for the `jimmy-units-ensure` Muse hook definition (script:
`muse-vm/jimmy-units-ensure.sh`, poll every ~60s, script timeout 180s).
The hook wakes the agent with this prompt only when supervision was
restored, the installer failed, or a unit is still down.

---

You were woken by the jimmy-units-ensure hook. The wake payload contains the unit installer's output, its exit code, and any units still down. This hook replaced the old 15-minute cron that kept the jimmy-tunnel and jimmy-listener systemd services (plus the work-guard and okf-sync timers) installed and active on this VM.

Do this:
1. Re-verify with `systemctl is-active jimmy-tunnel.service jimmy-listener.service work-guard-check.timer okf-threads-sync.timer` and a `systemctl status` summary for anything down.
2. If supervision was restored (installer printed install/start/restart lines) and everything is active now: report to the user in one short message which units had been lost and were restored — supervision gaps after VM replacements are worth one line, no more.
3. If the installer failed or any unit is still down: try the self-service fixes first (re-run the unit installer (install-jimmy-units.sh), read the unit's journal with `journalctl -u <unit> -n 50`). If it still won't come up, report to the user what is down, what you tried, and the one specific thing you need from them.
4. If on recheck everything was already installed and active and the installer output is empty, say nothing.
