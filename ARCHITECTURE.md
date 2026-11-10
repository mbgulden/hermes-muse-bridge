# Architecture

## Overview

The bridge connects two systems that can't directly reach each other:

- **Hermes box** — Your local machine running Hermes agent profiles. Has the kanban database.
- **Muse VM** — Your Muse.ai virtual machine. Ephemeral (can be replaced on deploy), outbound network only.

The core challenge: the Muse VM can reach the box (outbound SSH), but the box cannot initiate connections to the VM. All push paths work around this.

## Event Flow

```
Kanban DB (box)
    │
    ▼ (2s poll)
kanban-events-daemon.py (box)
    ├──▶ Write JSON file ──▶ ~/.hermes/muse-inbox/kanban-events/<id>.json
    │                          │
    │                          ▼ (Muse polls via SSH, 1 min)
    │                     inbox-watch.sh picks it up
    │
    ├──▶ POST via tunnel ──▶ http://localhost:18989/kanban-event
    │                          │ (reverse SSH tunnel: box → VM)
    │                          ▼ (instant, seconds)
    │                     inbox-listener.py → ~/workspace/inbox/kanban-push/
    │
    └──▶ Telegram ──▶ @YourBot (if kind in completed/blocked/crashed/failed/gave_up)
```

**Three redundant paths, one event.** The file write is primary (always works). The tunnel POST is fastest (when tunnel is up). Telegram is for human awareness.

## Task Execution Flow

```
Kanban task assigned to "muse" (status: todo/ready)
    │
    ▼ (inbox-watch.sh, every 1 min)
Muse claims task → status: running
    │
    ▼
Writes brief → ~/workspace/inbox/work/muse-task-<id>.md
    │
    ▼ (cron worker)
Spawns subagent (max 3 concurrent, priority-ordered)
    │
    ├──▶ Success → `hermes kanban complete <id>` → status: done
    │              → Comment with results
    │              → .done marker written
    │
    ├──▶ Blocked → `hermes kanban block <id>` → status: blocked
    │              → Telegram notification to you
    │
    └──▶ Crash/timeout → No .done after 30 min
                         → consecutive_failures++
                         → Re-queue (todo) or block after 3 failures
```

## Transports

### tailscale-ssh (default)

Both machines on the same Tailscale tailnet. SSH from VM to box goes through the Tailscale network.

```
Muse VM ──SSH──▶ Tailscale ──▶ Hermes box (100.x.x.x)
     ▲                              │
     └──── reverse tunnel ──────────┘
         (box localhost:18989 → VM :8899)
```

**Setup:** Tailscale installed on both. VM uses `tailscale up`, box is already on the tailnet.

**Gotcha:** Tailscale node keys expire by default, causing SSH re-auth. Disable key expiry for the VM in the Tailscale admin console (Settings → Keys → disable expiry for the device), or use `tailscale up --force-reauth` periodically.

### direct-ssh

Box has a directly reachable IP or hostname. Plain SSH, no Tailscale.

```
Muse VM ──SSH──▶ box.example.com (or 192.168.1.x)
     ▲                    │
     └──── reverse tunnel ┘
```

**Setup:** SSH keypair. VM's public key in box's `~/.ssh/authorized_keys`.

### localhost

Both Hermes and Muse run on the same machine (e.g., testing, or Muse running locally).

```
No SSH. Daemon writes files directly. Watcher reads local files.
Tunnel not needed — listener on localhost:8899 directly.
```

## Components in Detail

### kanban-events-daemon.py (box)

Single daemon replacing the earlier split design. One poll loop, three outputs.

- **Poll:** `SELECT * FROM task_events WHERE id > watermark ORDER BY id ASC` every 2s
- **Watermark:** `~/.hermes/muse-inbox/kanban-events/.watermark` (crash-safe, per-event advance)
- **PID guard:** Won't start a second instance
- **@reboot cron:** Survives box restarts

### inbox-listener.py (Muse VM)

Stdlib-only Python HTTP server.

- `POST /kanban-event` — Validates `id`, `task_id`, `kind`. Writes JSON. Returns 200.
- `POST /inbox` — For direct agent-to-Muse messages. Writes markdown.
- `GET /health` — Simple `{"status": "ok"}`.
- `GET /health/detailed` — Tunnel status, daemon status, last event timestamp, uptime.
- **Self-healing:** Heartbeat thread for work-guard. Watcher restarts it if down.

### tunnel.sh (Muse VM)

Persistent reverse SSH tunnel. `ssh -N -R <remote_port>:localhost:<local_port>`.

- Auto-reconnects on drop (infinite loop with backoff)
- Heartbeats to work-guard
- Registered as a work-guard job for failsafe restarts

### inbox-watch.sh (Muse VM)

The orchestrator. Runs every minute via Muse cron.

1. Check tunnel-push inbox (`~/workspace/inbox/kanban-push/`)
2. Check agent request inbox (via SSH)
3. Poll kanban events (fallback for tunnel misses)
4. Retry stale tasks (no `.done` after 30 min)
5. Claim new tasks assigned to `muse` (priority-ordered, max 5/run)

### call-muse skill (Hermes profiles)

Lets Hermes agents request help from Muse. File-based (not HTTP):

```
Agent writes → ~/.hermes/muse-inbox/requests/<id>.md
Muse polls, processes, writes → ~/.hermes/muse-inbox/responses/<id>.response.md
Agent polls for response
```

Request types: `review-request`, `judgment`, `question`, `escalation`.

### muse-kanban-worker skill (Muse VM)

Documents the worker loop for Muse: claiming, subagent dispatch, state updates, retry logic, concurrency limits.

## Failure Modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Tunnel drops | Health check `tunnel: down` | tunnel.sh auto-reconnects; file+poll covers the gap |
| Daemon crashes | PID guard on restart; @reboot cron | Manual restart; watermark prevents re-processing |
| Listener dies | Watcher checks `/health`, restarts | Work-guard failsafe as backup |
| Subagent crashes | No `.done` after 30 min | Retry (max 3), then block + notify |
| Box unreachable | SSH fails | Watcher logs, retries next minute |
| VM replaced | Everything in `~/workspace` persists | Re-run install.sh (idempotent) |

## Security Notes

- No secrets in the repo. All tokens/keys come from `config.yaml` (gitignored) or environment.
- The listener binds to all interfaces but is only reachable via the tunnel or tailnet — not publicly exposed.
- Telegram bot token is read from the Hermes profile `.env`, never logged.
- SSH uses keypair auth, not passwords.
