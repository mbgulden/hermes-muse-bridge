"""Phase 2: /steer command handler for kanban-events-daemon.

Imported by the daemon. Handles Telegram getUpdates long-poll for /steer commands.
Security: allowlist checked BEFORE parsing. Non-allowlisted senders dropped silently.
"""
import os
import time
import traceback
import urllib.request
import urllib.parse
import json

# Set by daemon before calling steer_poll_loop
HERMES_HOME = None
STREAMOPS_ENV = None
_log = None
_send_telegram = None
_open_db = None


def init(hermes_home, streamops_env, log_fn, send_fn, open_db_fn):
    global HERMES_HOME, STREAMOPS_ENV, _log, _send_telegram, _open_db
    HERMES_HOME = hermes_home
    STREAMOPS_ENV = streamops_env
    _log = log_fn
    _send_telegram = send_fn
    _open_db = open_db_fn


STEER_UPDATE_OFFSET_FILE = None  # set in init
_JIMMY_STEER_TIMES = {}
JIMMY_STEER_MIN_INTERVAL = 300  # 5 minutes


def _offset_file():
    return os.path.join(HERMES_HOME, "steer-update-offset")


def read_update_offset():
    try:
        with open(_offset_file()) as f:
            return int(f.read().strip())
    except Exception:
        return 0


def write_update_offset(offset):
    try:
        with open(_offset_file(), "w") as f:
            f.write(str(offset))
    except Exception as e:
        _log("offset write failed: %s" % e)


def load_allowlist():
    try:
        with open(STREAMOPS_ENV) as f:
            for line in f:
                line = line.strip()
                if line.startswith("TELEGRAM_ALLOWED_USERS="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return set(x.strip() for x in val.split(",") if x.strip())
    except Exception as e:
        _log("allowlist load failed: %s" % e)
    return set()


def tg_api(bot_token, method, params=None, timeout=30):
    try:
        url = "https://api.telegram.org/bot%s/%s" % (bot_token, method)
        data = None
        if params:
            data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        _log("telegram api %s failed: %s" % (method, e))
        return None


def get_task_status(task_id):
    try:
        conn = _open_db()
        row = conn.execute(
            "SELECT status, assignee, title FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        conn.close()
        if row:
            return True, row["status"], row["assignee"], row["title"]
        return False, None, None, None
    except Exception as e:
        _log("task lookup failed: %s" % e)
        return False, None, None, None


def write_steer_comment(task_id, author, instruction):
    try:
        import sys
        sys.path.insert(0, "/home/ubuntu/work/hermes-agent-fork")
        from hermes_cli import kanban_db
        conn = kanban_db.connect()
        cid = kanban_db.add_comment(conn, task_id, author, "[steer] " + instruction)
        conn.close()
        return cid
    except Exception as e:
        _log("steer comment write failed: %s" % e)
        return None


def handle_steer(sender_id, chat_id, message_id, text, bot_token):
    parts = text.strip().split(None, 2)
    if len(parts) < 3:
        _send_telegram(bot_token, chat_id,
            "Usage: /steer <task-id> <instruction>")
        return
    _, task_id, instruction = parts
    instruction = instruction.strip()
    if not instruction:
        _send_telegram(bot_token, chat_id, "Steer instruction cannot be empty.")
        return

    exists, status, assignee, title = get_task_status(task_id)
    if not exists:
        _send_telegram(bot_token, chat_id, "Unknown task: %s" % task_id)
        return
    if status not in ("in_progress", "running"):
        _send_telegram(bot_token, chat_id,
            "Task %s is already %s -- steering is a no-op." % (task_id, status))
        return

    cid = write_steer_comment(task_id, "michael", instruction)
    if cid is None:
        _send_telegram(bot_token, chat_id, "Failed to write steer comment. Check logs.")
        return

    agent_tag = "@%s" % assignee if assignee else "worker"
    _send_telegram(bot_token, chat_id,
        "\u2709\ufe0f Steer sent to %s on %s" % (agent_tag, task_id))
    _log("steer: task=%s by michael comment_id=%d" % (task_id, cid))


def jimmy_steer(task_id, instruction):
    """Jimmy automation steer. Rate-limited: 1 per task per 5 min."""
    now = time.time()
    last = _JIMMY_STEER_TIMES.get(task_id, 0)
    if now - last < JIMMY_STEER_MIN_INTERVAL:
        _log("jimmy_steer rate-limited for %s (%.0fs since last)" % (task_id, now - last))
        return False
    exists, status, _, _ = get_task_status(task_id)
    if not exists or status not in ("in_progress", "running"):
        return False
    cid = write_steer_comment(task_id, "jimmy", instruction)
    if cid:
        _JIMMY_STEER_TIMES[task_id] = now
        _log("jimmy_steer: task=%s comment_id=%d" % (task_id, cid))
        return True
    return False


def steer_poll_loop(bot_token, ops_chat_id):
    """Long-poll getUpdates. Runs in daemon thread. Allowlist checked first."""
    _log("steer poll loop started")
    offset = read_update_offset()
    allowed = load_allowlist()
    last_refresh = time.time()
    while True:
        try:
            if time.time() - last_refresh > 300:
                allowed = load_allowlist()
                last_refresh = time.time()
            resp = tg_api(bot_token, "getUpdates",
                          {"offset": offset, "timeout": 25,
                           "allowed_updates": '["message"]'},
                          timeout=35)
            if not resp or not resp.get("ok"):
                time.sleep(5)
                continue
            for upd in resp.get("result", []):
                offset = max(offset, upd.get("update_id", 0) + 1)
                msg = upd.get("message", {})
                sender_id = str(msg.get("from", {}).get("id", ""))
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                # HARD REQUIREMENT: allowlist BEFORE parsing. Silent drop.
                if sender_id not in allowed:
                    continue
                if text.startswith("/steer"):
                    try:
                        handle_steer(sender_id, chat_id, msg.get("message_id"),
                                     text, bot_token)
                    except Exception:
                        _log("steer handler error:\n" + traceback.format_exc())
            write_update_offset(offset)
        except Exception:
            _log("steer poll error:\n" + traceback.format_exc())
            time.sleep(10)
