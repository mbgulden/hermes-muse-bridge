# hermes-muse-bridge

**Version 1.4.0** — see [CHANGELOG.md](CHANGELOG.md) for what's new.

Two-way integration between [Hermes](https://github.com/NousResearch/hermes-agent) (local AI agent orchestrator) and [Muse.ai](https://muse.ai) (your AI assistant). Assign kanban tasks to Muse, get them done, get notified.

## What it does

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Hermes    │────▶│ Kanban Board │────▶│  Muse.ai    │
│  profiles   │     │              │     │             │
│ (local LLM  │     │  task assigned│     │ Claims task │
│  agents)    │     │  to "muse"   │     │ Works it via│
└─────────────┘     └──────────────┘     │ subagents   │
       ▲                   │            └──────┬──────┘
       │                   │                   │
       │            ┌──────▼──────┐      ┌──────▼──────┐
       └────────────│  Telegram   │◀─────│  Kanban     │
                    │  bot pings  │      │  updated    │
                    │  on events  │      │  (done/block)│
                    └─────────────┘      └─────────────┘
```

**The loop:**
1. You (or a Hermes agent) create a kanban task assigned to `muse`
2. Muse picks it up within ~1 minute, claims it
3. Muse dispatches subagents to do the work (up to 3 concurrent, priority-ordered)
4. Muse updates the kanban: `done` with results, or `blocked` if stuck
5. You get a Telegram ping when tasks complete, block, or fail

**Transports:**
- **Instant push:** Box POSTs events through a reverse SSH tunnel → Muse's HTTP listener (seconds)
- **Poll fallback:** Muse polls the box every minute (catches anything the tunnel missed)
- **File drop:** Events also written as JSON files (belt and suspenders)

## Quick start

```bash
git clone https://github.com/mbgulden/hermes-muse-bridge.git
cd hermes-muse-bridge
./install.sh
```

See [INSTALL.md](INSTALL.md) for the full walkthrough.

## Components

| Component | Runs on | What it does |
|-----------|---------|--------------|
| `box/kanban-events-daemon.py` | Hermes box | Tails kanban DB, pushes events to Muse, sends Telegram alerts |
| `muse-vm/inbox-listener.py` | Muse VM | HTTP listener for pushed events + health checks |
| `muse-vm/tunnel.sh` | Muse VM | Persistent reverse SSH tunnel (box → VM) |
| `muse-vm/inbox-watch.sh` | Muse VM | Polls box for requests, claims tasks, handles retries |
| `muse-vm/hermes-inbox-trigger.sh` | Muse VM | Hook: runs the watcher every ~60s, wakes an agent only on items needing judgment |
| `muse-vm/inbox-trigger-prompt.md` | Muse VM | The wake prompt for the inbox-trigger hook |
| `muse-vm/jimmy-units-ensure.sh` | Muse VM | Hook: keeps the systemd units installed/active; recovery anchor after VM replacements |
| `muse-vm/units-ensure-prompt.md` | Muse VM | The wake prompt for the units-ensure hook |
| `muse-vm/branch-protection-tripwire.sh` | Muse VM | Hook: wakes an agent only when the branch-protection check trips or breaks |
| `muse-vm/tripwire-prompt.md` | Muse VM | The wake prompt for the tripwire hook |
| `muse-vm/work-guard-timer.sh` | Muse VM | systemd-timer wrapper for the work-guard check; flags failures for the inbox hook |
| `muse-vm/okf-sync-timer.sh` | Muse VM | systemd-timer wrapper for a knowledge sync + audit push; flags warnings |
| `muse-vm/systemd/` | Muse VM | Unit templates, incl. the work-guard-check and sync timers |
| `muse-vm/fleet-review-trigger.sh` | Muse VM | Hook: wakes a review pass the moment a significant kanban event lands |
| `muse-vm/cron-body-review-sweep.md` | Muse VM | Cron template: scheduled salvage reviews, 3-strike rule, dispatch checks |
| `muse-vm/review-pass-prompt.md` | Muse VM | The review-pass prompt shared by the hook and the sweep |
| `muse-vm/fleet-canary.sh` | Muse VM | Hourly end-to-end probe of the box → tunnel → listener → inbox path |
| `skills/call-muse/` | Hermes profiles | Lets Hermes agents ask Muse for reviews/judgments |
| `skills/muse-kanban-worker/` | Muse VM | Documents the worker loop for Muse |
| `skills/hermes-kanban-tasks/` | Muse VM | Task authoring: sizing, acceptance criteria, timed-run clauses, review gates |

## Transports

Configure in `config.yaml`:

- **`tailscale-ssh`** — Box and VM on the same Tailscale tailnet. SSH goes through the tailnet.
- **`direct-ssh`** — Box reachable directly (same LAN or public IP). Plain SSH.
- **`localhost`** — Both on the same machine. No SSH needed.

See [ARCHITECTURE.md](ARCHITECTURE.md) for details.

## Muse.ai specifics

This bridge is built for how Muse.ai works:

- **Event-based first** — deterministic hook scripts and systemd timers do the polling; an agent wakes only when something needs judgment (the token-burn modulation). The watcher still runs every minute — as a script inside the inbox-trigger hook, not an LLM cron.
- **Work-guard self-healing** — Background processes (tunnel, listener) register heartbeats; a failsafe restarts them if they die. See `docs/WORK-GUARD.md`.
- **Subagent dispatch** — Muse fans out kanban tasks to subagents (max 3 concurrent), triaged by priority.
- **Skills, not webhooks** — Hermes agents invoke Muse via the `call-muse` skill (file-based inbox), not HTTP callbacks.

## Docs

- [INSTALL.md](INSTALL.md) — Step-by-step setup
- [ARCHITECTURE.md](ARCHITECTURE.md) — How the pieces fit together
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — Common issues and fixes

## License

MIT — see [LICENSE](LICENSE).
