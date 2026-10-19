#!/usr/bin/env python3
"""kanban-events-daemon: unified kanban event processor for Hermes.

Tails task_events in kanban.db (configurable poll, read-only) and for each
new event:
  1. Writes JSON to <hermes_home>/muse-inbox/kanban-events/<event_id>.json (atomic)
  2. POSTs to Muse's listener via reverse tunnel (best-effort, non-blocking)
  3. Sends Telegram notification IF kind in notify_kinds (configurable)

Configuration: ~/.hermes/bridge-config.yaml (see config.example.yaml in the repo).
Falls back to sensible defaults if no config file exists.

WHY A DAEMON (not a gateway hook):
- Gateway hooks only emit message/agent lifecycle events. Zero hook emit()
  calls exist in any kanban code path. A gateway hook can NEVER fire on
  kanban task events.
- SQLite triggers have no file I/O, so can't write JSON or POST.

Run at boot via cron @reboot. Single-instance via .pid file.
Logs to <hermes_home>/logs/kanban-events-daemon.log.

Usage:
    python3 kanban-events-daemon.py [--help] [--config PATH] [--dry-run]

    --dry-run: process one poll cycle and exit (for testing install.sh)
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import traceback
import urllib.request
import urllib.error

# ── Defaults (overridden by config file) ──
DEFAULTS = {
    "hermes_home": os.path.expanduser("~/.hermes"),
    "poll_seconds": 2,
    "listener_url": "http://localhost:18989/kanban-event",
    "telegram_env_file": os.path.expanduser("~/.hermes/profiles/notifications/.env"),
    "notify_kinds": ["completed", "blocked", "crashed", "failed", "gave_up"],
}

KIND_EMOJI = {
    "completed": "✅",
    "blocked": "🚫",
    "crashed": "💥",
    "failed": "❌",
    "gave_up": "🏳️",
}


def load_config(path):
    """Load YAML config. Minimal parser — handles the config.example.yaml shape."""
    cfg = dict(DEFAULTS)
    if not os.path.exists(path):
        return cfg
    try:
        import yaml  # optional
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except ImportError:
        data = _parse_simple_yaml(path)
    # Flatten nested config into our flat defaults
    box = data.get("box", {})
    if box.get("hermes_home"):
        cfg["hermes_home"] = os.path.expanduser(box["hermes_home"])
    muse_vm = data.get("muse_vm", {})
    if muse_vm.get("tunnel_remote_port"):
        cfg["listener_url"] = "http://localhost:%d/kanban-event" % muse_vm["tunnel_remote_port"]
    tg = data.get("telegram", {})
    if tg.get("env_file"):
        cfg["telegram_env_file"] = os.path.expanduser(tg["env_file"])
    if tg.get("notify_kinds"):
        cfg["notify_kinds"] = list(tg["notify_kinds"])
    return cfg


def _parse_simple_yaml(path):
    """Fallback YAML parser for flat key: value and simple lists. Good enough for our config."""
    data = {}
    stack = [data]
    current_list_key = None
    with open(path) as f:
        for raw_line in f:
            line = raw_line.rstrip()
            if not line or line.strip().startswith("#"):
                continue
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if stripped.startswith("- "):
                # List item
                if current_list_key and stack:
                    val = stripped[2:].strip().strip('"').strip("'")
                    parent = stack[-1]
                    if isinstance(parent.get(current_list_key), list):
                        parent[current_list_key].append(val)
                continue
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                # Adjust stack depth (2-space indent)
                depth = indent // 2
                while len(stack) > depth + 1:
                    stack.pop()
                parent = stack[-1]
                if val == "":
                    # Nested dict or upcoming list
                    parent[key] = {}
                    stack.append(parent[key])
                    current_list_key = key
                    # Peek: if next non-empty line is a list item, convert to list
                    parent[key] = []  # will be replaced by dict if nested keys appear
                    # We handle this lazily: keep as list, convert on nested key
                    stack[-1] = parent  # stay at parent level
                    # Actually track: store marker
                    parent["_pending_list_key"] = key
                else:
                    # If parent had a pending list key that got a scalar, it was a dict
                    if "_pending_list_key" in parent and parent["_pending_list_key"] == key:
                        del parent["_pending_list_key"]
                    # Convert pending list to dict if we now see a nested key
                    pk = parent.pop("_pending_list_key", None)
                    if pk and isinstance(parent.get(pk), list) and not parent[pk]:
                        parent[pk] = {}
                    elif pk and isinstance(parent.get(pk), list):
                        pass  # it was a real list, keep it
                    parent[key] = _coerce(val)
                    current_list_key = None
    # Clean up markers
    def _clean(d):
        if isinstance(d, dict):
            d.pop("_pending_list_key", None)
            for v in d.values():
                _clean(v)
    _clean(data)
    return data


def _coerce(val):
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


class Daemon:
    def __init__(self, cfg):
        self.cfg = cfg
        h = cfg["hermes_home"]
        self.db_path = os.path.join(h, "kanban.db")
        self.out_dir = os.path.join(h, "muse-inbox", "kanban-events")
        self.watermark_file = os.path.join(self.out_dir, ".watermark")
        self.pid_file = os.path.join(self.out_dir, ".pid")
        self.log_file = os.path.join(h, "logs", "kanban-events-daemon.log")
        self.notify_kinds = set(cfg["notify_kinds"])

    def log(self, msg):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            with open(self.log_file, "a") as f:
                f.write("%s %s\n" % (ts, msg))
        except Exception:
            pass

    def already_running(self):
        try:
            with open(self.pid_file) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def open_db(self):
        conn = sqlite3.connect("file:%s?mode=ro" % self.db_path, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def load_telegram(self):
        cfg = {}
        try:
            with open(self.cfg["telegram_env_file"]) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        cfg[k.strip()] = v.strip().strip('"').strip("'")
        except Exception as e:
            self.log("telegram env load failed: %s" % e)
        return cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", "")

    def send_telegram(self, bot_token, chat_id, text):
        try:
            url = "https://api.telegram.org/bot%s/sendMessage" % bot_token
            data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
            req = urllib.request.Request(url, data=data, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status == 200
        except Exception as e:
            self.log("telegram send failed: %s" % e)
            return False

    def post_to_muse(self, payload):
        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                self.cfg["listener_url"], data=data, method="POST",
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status == 200
        except Exception:
            return False

    def process_event(self, event_id, task_id, kind, created_at, title, status,
                      assignee, bot_token, chat_id):
        payload = {
            "id": event_id, "task_id": task_id, "kind": kind,
            "title": title, "status": status, "assignee": assignee,
            "created_at": created_at,
        }
        # 1. Atomic file write (primary transport)
        path = os.path.join(self.out_dir, "%d.json" % event_id)
        if not os.path.exists(path):
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f)
            os.replace(tmp, path)
        # 2. POST to Muse (secondary, best-effort)
        self.post_to_muse(payload)
        # 3. Telegram for significant kinds
        if kind in self.notify_kinds and bot_token and chat_id:
            emoji = KIND_EMOJI.get(kind, "📋")
            text = "%s <b>Kanban: %s</b>\n%s\nTask: %s" % (
                emoji, kind.upper(), title or task_id, task_id)
            if self.send_telegram(bot_token, chat_id, text):
                self.log("telegram notified %s for %s" % (kind, task_id))
            else:
                self.log("telegram FAILED for %s (%s)" % (task_id, kind))

    def poll_once(self, wm, bot_token, chat_id):
        """Process one poll cycle. Returns new watermark."""
        conn = self.open_db()
        try:
            events = conn.execute(
                "SELECT id, task_id, kind, created_at FROM task_events "
                "WHERE id > ? ORDER BY id ASC LIMIT 200", (wm,)
            ).fetchall()
            for ev in events:
                task = conn.execute(
                    "SELECT title, status, assignee FROM tasks WHERE id = ?",
                    (ev["task_id"],)
                ).fetchone()
                self.process_event(
                    ev["id"], ev["task_id"], ev["kind"], ev["created_at"],
                    task["title"] if task else None,
                    task["status"] if task else None,
                    task["assignee"] if task else None,
                    bot_token, chat_id,
                )
                wm = ev["id"]
                # Advance watermark per event (crash-safe)
                tmp = self.watermark_file + ".tmp"
                with open(tmp, "w") as f:
                    f.write(str(wm))
                os.replace(tmp, self.watermark_file)
        finally:
            conn.close()
        return wm

    def run(self, dry_run=False):
        os.makedirs(self.out_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        if not dry_run and self.already_running():
            print("kanban-events-daemon already running, exiting", flush=True)
            return 0
        if not dry_run:
            with open(self.pid_file, "w") as f:
                f.write(str(os.getpid()))
        self.log("started pid=%d" % os.getpid())

        bot_token, chat_id = self.load_telegram()
        if bot_token and chat_id:
            self.log("telegram configured")
        else:
            self.log("telegram not configured, notifications disabled")

        # Watermark init (don't replay history)
        try:
            with open(self.watermark_file) as f:
                wm = int(f.read().strip())
        except Exception:
            wm = None
        if wm is None:
            try:
                conn = self.open_db()
                wm = conn.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM task_events").fetchone()[0]
                conn.close()
            except Exception as e:
                self.log("watermark init failed: %s" % e)
                wm = 0
            with open(self.watermark_file, "w") as f:
                f.write(str(wm))
            self.log("watermark initialized at %d" % wm)

        if dry_run:
            wm = self.poll_once(wm, bot_token, chat_id)
            print("dry-run complete, watermark=%d" % wm)
            return 0

        while True:
            try:
                wm = self.poll_once(wm, bot_token, chat_id)
            except Exception:
                self.log("poll error:\n" + traceback.format_exc())
            time.sleep(self.cfg["poll_seconds"])


def main():
    ap = argparse.ArgumentParser(description="Unified kanban event daemon for Hermes.")
    ap.add_argument("--config", default=os.path.expanduser("~/.hermes/bridge-config.yaml"),
                    help="Path to bridge config YAML")
    ap.add_argument("--dry-run", action="store_true",
                    help="Process one poll cycle and exit (for testing)")
    args = ap.parse_args()
    cfg = load_config(args.config)
    d = Daemon(cfg)
    return d.run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
