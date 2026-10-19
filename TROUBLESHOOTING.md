# Troubleshooting

## Daemon not processing events

**Symptom:** New kanban tasks don't trigger any output (no files, no Telegram, no POST).

**Check:**
```bash
# Is it running?
pgrep -f kanban-events-daemon.py

# Check the log
tail -20 ~/.hermes/logs/kanban-events-daemon.log

# Check the watermark (should advance with each event)
cat ~/.hermes/muse-inbox/kanban-events/.watermark
```

**Fix:** If not running, start it. If running but stuck, check for DB lock errors in the log. The daemon uses read-only SQLite connections specifically to avoid locking.

## Daemon not picking up code changes

**Symptom:** You edited `kanban-events-daemon.py` but behavior didn't change.

**Cause:** Python loads the file once at startup. Editing the file doesn't affect the running process.

**Fix:** Restart the daemon:
```bash
pkill -f kanban-events-daemon.py
sleep 2
nohup python3 ~/.hermes/bin/kanban-events-daemon.py >> ~/.hermes/logs/kanban-events-daemon.log 2>&1 &
```

This bit us during development — the daemon kept running old code after a config change.

## Tunnel is down

**Symptom:** `/health/detailed` shows `"tunnel": "down"`. Events still arrive via file+poll (slower).

**Check:**
```bash
# Is the tunnel process running?
pgrep -f tunnel.sh

# Check tunnel log
tail -20 ~/workspace/inbox/tunnel.log

# Test manually from the box
ssh <box> 'curl -s --max-time 5 http://localhost:18989/health'
```

**Fix:** The tunnel script auto-reconnects. If it's not running, start it:
```bash
nohup ~/workspace/bin/tunnel.sh >/dev/null 2>&1 &
```

If it keeps dropping, check SSH connectivity and the reverse port isn't already in use on the box.

## Tailscale SSH re-auth wall

**Symptom:** All SSH to the box fails. `mwh-safe.sh` prints a `https://login.tailscale.com/a/...` URL.

**Cause:** Tailscale node key expired. The VM needs re-authentication.

**Fix (manual):** Visit the URL in a browser to re-authenticate.

**Fix (permanent):** Disable key expiry for the VM in the Tailscale admin console:
1. Go to https://login.tailscale.com/admin/machines
2. Find your Muse VM device
3. Click `...` → Disable key expiry

Or via the Tailscale API:
```bash
curl -u "tskey-api-...:" https://api.tailscale.com/api/v2/device/<nodeId>/key \
  -d '{"keyExpiryDisabled": true}'
```

## Events are duplicated

**Symptom:** Same event processed twice (two Telegram messages, two files).

**Cause:** Watermark wasn't advanced before a crash, or two daemon instances running.

**Check:**
```bash
# Should be exactly one
pgrep -f kanban-events-daemon.py | wc -l
```

**Fix:** Kill duplicates. The file write is idempotent (checks `os.path.exists` before writing), so duplicates are harmless but noisy.

## Task stuck in "running" forever

**Symptom:** Task claimed by Muse but never completes. No `.done` marker.

**Cause:** Subagent crashed, timed out, or the VM restarted mid-task.

**Recovery:** Automatic. After 30 minutes with no `.done` marker, the watcher increments `consecutive_failures` and re-queues the task. After 3 failures, it's marked `blocked` and you get a Telegram notification.

**Manual fix:**
```bash
# Reset a stuck task
hermes kanban update <task_id> --status todo --clear-assignee
```

## Telegram notifications not arriving

**Symptom:** Tasks complete/block but no Telegram message.

**Check:**
```bash
# Is the bot token configured?
grep TELEGRAM_BOT_TOKEN ~/.hermes/profiles/*/env 2>/dev/null | head -1

# Check daemon log for send failures
grep -i "telegram" ~/.hermes/logs/kanban-events-daemon.log | tail -5
```

**Common causes:**
- Bot token wrong or revoked → regenerate via @BotFather
- Chat ID wrong → message the bot first, then check `getUpdates`
- Bot blocked by user → unblock it

## Listener returns 400 on POST

**Symptom:** Daemon log shows POST failing, but tunnel is up.

**Check the listener log:**
```bash
tail -20 ~/workspace/inbox/listener.log
```

**Common causes:**
- Missing required fields (`id`, `task_id`, `kind`) → check the payload format
- Invalid event ID (special characters) → IDs must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`

## High CPU from the daemon

**Symptom:** `kanban-events-daemon.py` using significant CPU.

**Cause:** Poll loop too tight, or DB query is slow on a large `task_events` table.

**Fix:** Increase `POLL_SECONDS` in the daemon (default: 2). For very large boards, consider archiving old events.

## Work-guard keeps restarting the listener

**Symptom:** `~/workspace/.work-guard.log` shows repeated RESTART entries for the listener.

**Cause:** The listener isn't heartbeating. The work-guard checks file mtime.

**Fix:** Ensure the heartbeat thread is running (it's built into `inbox-listener.py`). Check:
```bash
ls -la ~/workspace/.work-heartbeat-muse-inbox-listener
# mtime should be recent (within 5 minutes)
```
