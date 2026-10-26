#!/usr/bin/env python3
"""kanban-stream companion: /steer daemon.

Long-polls the Telegram Bot API for ``/steer <task-id> <instruction>``
messages from allowlisted users and posts them as ``[steer] ...``
comments on the task through the public ``hermes kanban comment`` CLI.
Hermes core delivers new task comments to the running worker, so
steering closes the loop without any API coupling between the two.

Security model (same as the production daemon):
  - allowlist is checked BEFORE any parsing; non-allowlisted senders
    are dropped silently (no response, no log of content)
  - update offset is persisted so a restart does not re-process
    (and a bot token rotation does not replay) old updates
  - the daemon never writes the kanban DB directly — comments go
    through the public CLI (core's locking, task check, and
    ``commented`` event apply); its only DB access is a read-only
    task-status lookup

Credentials come from the environment (never from files, never
hardcoded):
  KANBAN_STREAM_BOT_TOKEN      Telegram bot token (required)
  KANBAN_STREAM_ALLOWED_USERS  comma-separated Telegram user IDs (required)
  KANBAN_STREAM_OPS_CHAT_ID    (optional) reply target; defaults to the
                               first allowlisted user
  HERMES_HOME                  Hermes root (default: ~/.hermes); the
                               kanban DB is expected at
                               $HERMES_HOME/kanban.db or, if
                               HERMES_HOME is a profile home, one level up.
  HERMES_KANBAN_DB             (optional) explicit kanban DB path
  KANBAN_STREAM_STEER_AUTHOR   (optional) author label for posted comments
                               (default: "operator")

Usage:
  python3 steer.py            # run in the foreground (logs to stdout)
  python3 steer.py --check    # verify config + DB reachability, then exit

Run under a process supervisor (systemd unit, tmux, etc.). One daemon
per bot token; running two daemons on the same token splits updates
between them (each claims an update exactly once via the offset).
"""

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

BOT_TOKEN_VAR = "KANBAN_STREAM_BOT_TOKEN"
ALLOWED_VAR = "KANBAN_STREAM_ALLOWED_USERS"
OPS_CHAT_VAR = "KANBAN_STREAM_OPS_CHAT_ID"
DB_PIN_VAR = "HERMES_KANBAN_DB"
STEER_AUTHOR_VAR = "KANBAN_STREAM_STEER_AUTHOR"
TG_TIMEOUT = 35
POLL_TIMEOUT = 25          # getUpdates long-poll seconds
OFFSET_REFRESH = 300       # re-read allowlist / retry on silent failure
STARTUP_SLEEP = 5          # backoff after a failed API call


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------------------
# Config / paths
# ---------------------------------------------------------------------------

def hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def kanban_db_path() -> str:
    pinned = os.environ.get(DB_PIN_VAR)
    if pinned:
        return pinned
    root = hermes_home()
    candidates = [os.path.join(root, "kanban.db")]
    # If HERMES_HOME points at a profile home, the shared board DB is
    # usually in the parent directory.
    if "/profiles/" in root or os.path.basename(root) == "home":
        candidates.append(os.path.join(os.path.dirname(root), "kanban.db"))
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


def load_allowlist() -> set:
    raw = (os.environ.get(ALLOWED_VAR) or "").strip()
    return {x.strip() for x in raw.split(",") if x.strip()}


def ops_chat_id() -> str:
    return (os.environ.get(OPS_CHAT_VAR)
            or (os.environ.get(ALLOWED_VAR) or "").split(",")[0].strip())


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def tg_api(token: str, method: str, params: dict = None,
           timeout: int = TG_TIMEOUT):
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    data = None
    if params:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log("telegram %s failed: %s" % (method, e))
        return None


def send_text(token: str, chat_id, text: str):
    tg_api(token, "sendMessage", {"chat_id": chat_id, "text": text})


# ---------------------------------------------------------------------------
# Update offset persistence
# ---------------------------------------------------------------------------

def offset_file() -> str:
    return os.path.join(hermes_home(), "plugin-data", "kanban-stream",
                        "steer-offset")


def read_offset() -> int:
    try:
        with open(offset_file()) as f:
            return int(f.read().strip())
    except Exception:
        return 0


def write_offset(offset: int) -> None:
    try:
        os.makedirs(os.path.dirname(offset_file()), exist_ok=True)
        with open(offset_file(), "w") as f:
            f.write(str(offset))
    except Exception as e:
        log("offset write failed: %s" % e)


# ---------------------------------------------------------------------------
# Kanban
# ---------------------------------------------------------------------------

def open_db() -> sqlite3.Connection:
    return sqlite3.connect(kanban_db_path(), timeout=5)


def get_task_status(task_id: str):
    """(exists, status, assignee, title) or (False, None, None, None)."""
    try:
        conn = open_db()
        try:
            row = conn.execute(
                "SELECT status, assignee, title FROM tasks WHERE id = ?",
                (task_id,)).fetchone()
        finally:
            conn.close()
        if row:
            return True, row[0], row[1], row[2]
        return False, None, None, None
    except Exception as e:
        log("task lookup failed: %s" % e)
        return False, None, None, None


def write_steer_comment(task_id: str, instruction: str) -> bool:
    """Post a [steer] comment through the public Hermes CLI:

        hermes kanban comment <task_id> --author <label> "[steer] ..."

    Going through the CLI means core's locking, task validation, and
    the ``commented`` event all apply — the daemon never writes
    kanban.db with raw SQL. (Its remaining DB use, the task-status
    check in get_task_status, is read-only.)
    """
    author = (os.environ.get(STEER_AUTHOR_VAR) or "operator").strip()
    try:
        r = subprocess.run(
            ["hermes", "kanban", "comment", task_id,
             "--author", author, "[steer] " + instruction],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            log("steer comment failed: %s"
                % (r.stderr.strip() or r.stdout.strip() or r.returncode))
            return False
        return True
    except Exception as e:
        log("steer comment write failed: %s" % e)
        return False


# ---------------------------------------------------------------------------
# /steer handling
# ---------------------------------------------------------------------------

def handle_steer(sender_id: str, chat_id, text: str, token: str) -> None:
    parts = text.strip().split(None, 2)
    if len(parts) < 3:
        send_text(token, chat_id, "Usage: /steer <task-id> <instruction>")
        return
    _, task_id, instruction = parts
    instruction = instruction.strip()
    if not instruction:
        send_text(token, chat_id, "Steer instruction cannot be empty.")
        return

    exists, status, assignee, title = get_task_status(task_id)
    if not exists:
        send_text(token, chat_id, "Unknown task: %s" % task_id)
        return
    if status not in ("in_progress", "running"):
        send_text(token, chat_id,
                  "Task %s is already %s -- steering is a no-op."
                  % (task_id, status))
        return

    if not write_steer_comment(task_id, instruction):
        send_text(token, chat_id,
                  "Failed to write steer comment. Check daemon logs.")
        return

    agent_tag = "@%s" % assignee if assignee else "worker"
    send_text(token, chat_id,
              "\u2709\ufe0f Steer sent to %s on %s (%s)"
              % (agent_tag, task_id, (title or "")[:60]))
    log("steer: task=%s author=%s comment posted" % (task_id, sender_id))


def poll_loop(token: str) -> None:
    allowed = load_allowlist()
    if not allowed:
        log("FATAL: %s is empty — refusing to run (would drop all /steer "
            "traffic; fix the env before starting)" % ALLOWED_VAR)
        sys.exit(2)
    offset = read_offset()
    last_refresh = time.time()
    log("steer daemon started (allowlist size=%d, offset=%d, db=%s)"
        % (len(allowed), offset, kanban_db_path()))
    while True:
        try:
            if time.time() - last_refresh > OFFSET_REFRESH:
                allowed = load_allowlist()
                last_refresh = time.time()
            resp = tg_api(token, "getUpdates",
                          {"offset": offset, "timeout": POLL_TIMEOUT,
                           "allowed_updates": '["message"]'})
            if not resp or not resp.get("ok"):
                time.sleep(STARTUP_SLEEP)
                continue
            for upd in resp.get("result", []):
                offset = max(offset, upd.get("update_id", 0) + 1)
                msg = upd.get("message", {})
                sender_id = str(msg.get("from", {}).get("id", ""))
                # HARD REQUIREMENT: allowlist BEFORE parsing. Silent drop.
                if sender_id not in allowed:
                    continue
                text = msg.get("text", "") or ""
                if not text.startswith("/steer"):
                    continue
                try:
                    handle_steer(sender_id, msg.get("chat", {}).get("id"),
                                 text, token)
                except Exception:
                    log("steer handler error:\n" + traceback.format_exc())
            write_offset(offset)
        except KeyboardInterrupt:
            write_offset(offset)
            log("steer daemon stopping (clean)")
            return
        except Exception:
            log("steer poll error:\n" + traceback.format_exc())
            time.sleep(STARTUP_SLEEP)


def self_check() -> int:
    """--check: verify config and DB reachability without network calls."""
    ok = True
    token = os.environ.get(BOT_TOKEN_VAR, "")
    print("KANBAN_STREAM_BOT_TOKEN: %s" %
          ("set (len=%d)" % len(token) if token else "MISSING"))
    ok &= bool(token)
    allowed = load_allowlist()
    print("KANBAN_STREAM_ALLOWED_USERS: %s"
          % ("%d id(s)" % len(allowed) if allowed else "MISSING"))
    ok &= bool(allowed)
    print("KANBAN_STREAM_OPS_CHAT_ID: %s"
          % (ops_chat_id() or "(falls back to first allowlisted user)"))
    db = kanban_db_path()
    try:
        conn = open_db()
        n = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        conn.close()
        print("kanban DB: %s (reachable, %d tasks)" % (db, n))
    except Exception as e:
        print("kanban DB: UNREACHABLE (%s: %s)" % (db, e))
        ok = False
    print("offset file: %s" % offset_file())
    print("SELF-CHECK: %s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="verify config and DB reachability, then exit")
    args = ap.parse_args()
    if args.check:
        return self_check()
    token = os.environ.get(BOT_TOKEN_VAR, "")
    if not token:
        log("FATAL: %s is not set" % BOT_TOKEN_VAR)
        return 2
    try:
        signal.signal(signal.SIGTERM, lambda *a: (_ for _ in ()).throw(
            KeyboardInterrupt()))
    except Exception:
        pass
    try:
        poll_loop(token)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
