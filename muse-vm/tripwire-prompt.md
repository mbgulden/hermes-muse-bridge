# Tripwire hook prompt — hermes-muse-bridge

The prompt for the `branch-protection-tripwire` Muse hook definition
(script: `muse-vm/branch-protection-tripwire.sh`, poll every ~300s, script
timeout 180s). The hook wakes the agent with this prompt only on a `trip`
verdict or after 3+ consecutive check errors.

---

You were woken by the branch-protection-tripwire hook. The wake payload carries the tripwire check's exit code and the tail of its log (the tripwire log).

Two cases:
1. Exit code 2 — the tripwire TRIPPED: a protected-branch violation was detected on your protected repo. The check script already emitted the dashboard signal and opened the revert PR. Report to the user immediately: the bad SHA and the revert PR link (both in the log tail; if the tail is truncated, read the full tripwire log and its state directory to get them). Do not merge or close anything yourself.
2. The check itself has errored 3+ consecutive times — the monitor is broken. Report to the user that the tripwire monitor is failing, with the error lines from the log tail, and what you found checking the tripwire check script.

Stay silent in all other cases.
