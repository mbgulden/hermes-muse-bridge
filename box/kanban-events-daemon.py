#!/usr/bin/env python3
"""kanban-events-daemon: unified kanban event processor for Hermes.

Tails task_events in kanban.db (2s poll, read-only) and for each new event:
  1. Writes JSON to ~/.hermes/jimmy-inbox/kanban-events/<event_id>.json (atomic)
  2. POSTs to Jimmy's listener via reverse tunnel (best-effort, non-blocking)
  3. Sends Telegram notification via Autobot bot IF kind in NOTIFY_KINDS

Replaces the two separate daemons (kanban-jimmy-push.py + kanban-telegram-notify.py)
with a single poll loop. One watermark, one PID file, one log.

WHY A DAEMON (not a gateway hook):
- Gateway hooks only emit message/agent lifecycle events. Zero hook emit()
  calls exist in any kanban code path (kanban_watchers.py and the CLI write
  SQLite directly). A gateway hook can NEVER fire on kanban task events.
- SQLite triggers have no file I/O, so can't write JSON or POST.

Telegram config (from autobot profile .env, never hardcoded):
- TELEGRAM_BOT_TOKEN, TELEGRAM_HOME_CHAT_ID from ~/.hermes/profiles/autobot/.env

Run at boot via cron @reboot. Single-instance via .pid file.
Logs to ~/.hermes/logs/kanban-events-daemon.log.
"""
import json
import os
import sqlite3
import sys
import time
import traceback
import urllib.request
import urllib.error

HERMES_HOME = os.path.expanduser("~/.hermes")
DB_PATH = os.path.join(HERMES_HOME, "kanban.db")
OUT_DIR = os.path.join(HERMES_HOME, "jimmy-inbox", "kanban-events")
WATERMARK_FILE = os.path.join(OUT_DIR, ".watermark")
PID_FILE = os.path.join(OUT_DIR, ".pid")
LOG_FILE = os.path.join(HERMES_HOME, "logs", "kanban-events-daemon.log")
AUTOBOT_ENV = os.path.join(HERMES_HOME, "profiles", "autobot", ".env")
POLL_SECONDS = 2

# Jimmy's listener via reverse SSH tunnel (port rotates; see jimmy-listener-port)
# The tunnel script publishes the live port here; fallback to pool default.
JIMMY_PORT_FILE = "/home/ubuntu/.hermes/jimmy-listener-port"
JIMMY_PORT_DEFAULT = 18991

def jimmy_listener_url():
    try:
        with open(JIMMY_PORT_FILE) as f:
            port = int(f.read().strip())
            return f"http://localhost:{port}/kanban-event"
    except Exception:
        return f"http://localhost:{JIMMY_PORT_DEFAULT}/kanban-event"

# Event kinds that trigger a Telegram notification
NOTIFY_KINDS = {"completed", "blocked", "crashed", "failed", "gave_up", "claimed"}
# Dedup: suppress repeat notifications for same (task_id, kind) within 10 minutes
NOTIFY_DEDUP_SECONDS = 600
_notify_cache = {}
KIND_EMOJI = {
    "completed": "✅",
    "blocked": "🚫",
    "crashed": "💥",
    "failed": "❌",
    "gave_up": "🏳️",
}


def log(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with open(LOG_FILE, "a") as f:
            f.write("%s %s\n" % (ts, msg))
    except Exception:
        pass


def read_watermark():
    try:
        with open(WATERMARK_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def write_watermark(event_id):
    tmp = WATERMARK_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(event_id))
    os.replace(tmp, WATERMARK_FILE)


def already_running():
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def open_db():
    conn = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def load_telegram_config():
    """Read bot token and chat ID from autobot profile .env. Never logs the token."""
    cfg = {}
    try:
        with open(AUTOBOT_ENV) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"').strip("'")
    except Exception as e:
        log("telegram env load failed: %s" % e)
    return cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_HOME_CHAT_ID", "")


def send_telegram(bot_token, chat_id, text):
    """Send a Telegram message. Returns True on success. Never logs the token."""
    try:
        url = "https://api.telegram.org/bot%s/sendMessage" % bot_token
        data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        log("telegram send failed: %s" % e)
        return False


def post_to_jimmy(payload):
    """POST event to Jimmy's listener via tunnel. Best-effort; file is primary."""
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            jimmy_listener_url(), data=data, method="POST",
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def process_event(event_id, task_id, kind, created_at, title, status, assignee,
                 bot_token, chat_id):
    """All three outputs for one event: file + POST + Telegram (if significant)."""
    payload = {
        "id": event_id,
        "task_id": task_id,
        "kind": kind,
        "title": title,
        "status": status,
        "assignee": assignee,
        "created_at": created_at,
    }

    # 1. Atomic file write (primary transport)
    path = os.path.join(OUT_DIR, "%d.json" % event_id)
    if not os.path.exists(path):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, path)

    # 2. POST to Jimmy's listener (secondary, best-effort)
    post_to_jimmy(payload)

    # 3. Telegram notification for significant kinds (with dedup)
    if kind in NOTIFY_KINDS and bot_token and chat_id:
        import time as _time
        _cache_key = (task_id, kind)
        _now = _time.time()
        _last = _notify_cache.get(_cache_key, 0)
        if _now - _last < NOTIFY_DEDUP_SECONDS:
            log("telegram dedup suppressed %s for %s (last sent %.0fs ago)" % (kind, task_id, _now - _last))
            return
        _notify_cache[_cache_key] = _now
        emoji = KIND_EMOJI.get(kind, "📋")
        who = " (@%s)" % assignee if assignee and assignee != "none" else ""
        text = "%s <b>Kanban: %s</b>%s\n%s\nTask: %s" % (emoji, kind.upper(), who, title or task_id, task_id)
        if send_telegram(bot_token, chat_id, text):
            log("telegram notified %s for %s" % (kind, task_id))
        else:
            log("telegram FAILED for %s (%s)" % (task_id, kind))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    if already_running():
        print("kanban-events-daemon already running, exiting", flush=True)
        return 0
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    log("started pid=%d (unified daemon)" % os.getpid())

    bot_token, chat_id = load_telegram_config()
    if bot_token and chat_id:
        log("telegram configured for chat_id=%s" % chat_id)
    else:
        log("WARNING: telegram not configured (missing token/chat_id), notifications disabled")

    wm = read_watermark()
    if wm is None:
        try:
            conn = open_db()
            wm = conn.execute("SELECT COALESCE(MAX(id), 0) FROM task_events").fetchone()[0]
            conn.close()
        except Exception as e:
            log("watermark init failed: %s" % e)
            wm = 0
        write_watermark(wm)
        log("watermark initialized at %d (pre-existing events not replayed)" % wm)

    while True:
        try:
            conn = open_db()
            events = conn.execute(
                "SELECT id, task_id, kind, created_at FROM task_events "
                "WHERE id > ? ORDER BY id ASC LIMIT 200", (wm,)
            ).fetchall()
            for ev in events:
                task = conn.execute(
                    "SELECT title, status, assignee FROM tasks WHERE id = ?",
                    (ev["task_id"],)
                ).fetchone()
                title = task["title"] if task else None
                status = task["status"] if task else None
                assignee = task["assignee"] if task else None
                wm = ev["id"]
                write_watermark(wm)
                process_event(
                    ev["id"], ev["task_id"], ev["kind"], ev["created_at"],
                    title, status, assignee, bot_token, chat_id,
                )
            conn.close()
        except Exception:
            log("poll error:\n" + traceback.format_exc())
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
