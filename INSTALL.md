# Installation

## Prerequisites

**On the Hermes box:**
- Hermes agent installed and running
- Kanban database at `~/.hermes/kanban.db`
- Python 3.8+ (stdlib only, no pip packages needed)
- Cron

**On the Muse VM:**
- Muse.ai VM with cron support
- Python 3.8+ (stdlib only)
- SSH client
- `curl`

**Network:**
- The Muse VM must be able to SSH to the Hermes box (via Tailscale, direct IP, or localhost)

## Step 1: Clone

```bash
git clone https://github.com/YOUR-USER/hermes-muse-bridge.git
cd hermes-muse-bridge
```

## Step 2: Run the installer

```bash
./install.sh
```

The installer is interactive and idempotent (safe to re-run). It will ask:

1. **Transport mode** — `tailscale-ssh`, `direct-ssh`, or `localhost`
2. **Box hostname** — Tailscale IP, regular hostname, or `localhost`
3. **SSH user** — usually `ubuntu` (default)
4. **Telegram bot token** (optional) — for kanban event notifications
5. **Telegram chat ID** (optional) — where notifications go

It then:
- Generates `config.yaml` from your answers
- Copies `box/kanban-events-daemon.py` to the Hermes box
- Sets up the `@reboot` cron entry on the box
- Installs `muse-vm/` scripts on the Muse VM
- Starts the listener and tunnel
- Deploys skills to `~/workspace/custom-skills/`
- Creates the Muse cron job for the watcher

## Step 3: Verify

```bash
# On the Muse VM — check the health endpoint
curl http://127.0.0.1:8899/health/detailed
```

You should see:
```json
{
  "status": "ok",
  "tunnel": "up",
  "push_daemon": "running",
  "version": "1.0.0"
}
```

Then create a test task:
```bash
# On the Hermes box
hermes kanban create "Test task for Muse" --assignee muse
```

Within ~1 minute, Muse should claim it. Check:
```bash
hermes kanban show <task_id>
# status should be "running", then "done" after Muse finishes
```

## Step 4: Assign tasks

```bash
# When creating
hermes kanban create "Research competitor pricing" --assignee muse --priority 10

# Reassigning
hermes kanban assign <task_id> --assignee muse
```

Priority is an integer (higher = more urgent). Muse claims tasks in `priority DESC, created_at ASC` order.

## Manual setup (without install.sh)

If you prefer to set things up manually:

### Box side

```bash
# 1. Copy the daemon
cp box/kanban-events-daemon.py ~/.hermes/bin/
chmod +x ~/.hermes/bin/kanban-events-daemon.py

# 2. Configure (create ~/.hermes/bridge-config.yaml — see config.example.yaml)

# 3. Add @reboot cron
(crontab -l 2>/dev/null; echo "@reboot /usr/bin/python3 $HOME/.hermes/bin/kanban-events-daemon.py >> $HOME/.hermes/logs/kanban-events-daemon.log 2>&1") | crontab -

# 4. Start it now
nohup python3 ~/.hermes/bin/kanban-events-daemon.py >> ~/.hermes/logs/kanban-events-daemon.log 2>&1 &
```

### Muse VM side

```bash
# 1. Copy scripts
cp muse-vm/inbox-listener.py ~/workspace/bin/
cp muse-vm/tunnel.sh ~/workspace/bin/
cp muse-vm/inbox-watch.sh ~/workspace/bin/
chmod +x ~/workspace/bin/*.sh

# 2. Start the listener
nohup python3 ~/workspace/bin/inbox-listener.py >/dev/null 2>&1 &

# 3. Start the tunnel (adjust ports for your transport)
nohup ~/workspace/bin/tunnel.sh >/dev/null 2>&1 &

# 4. Set up the watcher cron (every 1 minute) via Muse's cron system
# See the Muse docs for cron setup
```

### Skills

```bash
# Deploy to Muse VM
cp -r skills/muse-kanban-worker ~/workspace/custom-skills/

# Deploy to Hermes profiles (on the box, for each profile)
cp -r skills/call-muse ~/.hermes/profiles/<profile>/skills/
```

## Updating

```bash
cd hermes-muse-bridge
git pull
./install.sh  # idempotent — updates everything in place
```

The daemon restarts automatically on the next `@reboot`, or restart manually:
```bash
# On the box
pkill -f kanban-events-daemon.py
nohup python3 ~/.hermes/bin/kanban-events-daemon.py >> ~/.hermes/logs/kanban-events-daemon.log 2>&1 &
```
