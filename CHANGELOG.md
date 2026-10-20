# Changelog — hermes-muse-bridge

## 1.2.0 (2026-10-20)

Watcher fixes from production use:

- **Parent-gate fix** — the claim query gated on parent status `!= 'completed'`, but the kanban DB's terminal status is `done`, so every task WITH a parent was silently skipped forever. The query now gates on `!= 'done'`.
- **Review tasks now flow** — tasks in `review` status are picked up and briefed WITHOUT claiming (`hermes kanban claim` refuses review tasks by design). The reviewer verifies the work independently and resolves the task directly: `complete` to approve, `request-changes` to send it back. Review briefs are exempt from the retry loop — a stale brief is dropped and the task stays in `review`.
- **SSH re-auth wall probe** — when the SSH transport hits an interactive re-auth wall (e.g. Tailscale SSH `check`), the watcher detects it once, logs the login URL, and skips the remote steps for that run instead of mis-parsing the wall message as data and aborting mid-run.
- **Vanished-request guard** — an inbox request file deleted between listing and download is skipped cleanly instead of aborting the run.
- Cron body and `muse-kanban-worker` skill updated for the review flow.

## 1.1.0 (2026-10-19)

Improvements over the initial 1.0.0 release:

- **Unified event daemon** — merged the separate kanban-push and Telegram-notify daemons into a single `kanban-events-daemon.py`. One DB poll, three outputs per event (JSON file, tunnel POST, Telegram alert).
- **Priority-ordered task claiming** — the watcher now claims tasks `ORDER BY priority DESC, created_at ASC` instead of creation order alone.
- **Retry on failure** — tasks that fail without completing are re-queued up to 3 times, then marked `blocked` with an explanatory comment and a Telegram alert.
- **Detailed health endpoint** — `GET /health/detailed` on the listener reports tunnel status, daemon status, last event timestamp, and uptime.
- **Tunnel + poll hybrid transport** — reverse SSH tunnel for instant push, 1-minute poll as fallback. No event loss if the tunnel drops.
- **verify.sh** — automated post-install verification (tunnel, daemon, listener, config, skills).
- **uninstall.sh** — clean removal of all components.
- **install.sh honesty fix** — no longer claims to create Muse crons from shell. Prints the exact instructions to paste to your Muse agent, with the cron body in `muse-vm/cron-body.md`.

## 1.0.0 (2026-10-19)

Initial public release:

- Bidirectional Hermes ↔ Muse.ai bridge
- Kanban task integration (assign to `muse`, auto-claim, work, update)
- Event push via reverse SSH tunnel + file-drop fallback
- Telegram notifications on task events
- `call-muse` skill for Hermes profiles
- `muse-kanban-worker` skill for Muse agents
- Interactive `install.sh` with transport modes (tailscale-ssh, direct-ssh, localhost)
