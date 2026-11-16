#!/usr/bin/env python3
"""inbox-listener.py — Self-healing HTTP listener on Muse VM.

Receives PUSHED kanban events and inbox messages from your Hermes box
over Tailscale (this VM's tailnet IP: <your-vm-tailnet-ip>).

Endpoints:
  POST /kanban-event  — JSON body with required fields (id, task_id, kind);
                        writes ~/workspace/inbox/kanban-push/<event_id>.json, 200 {"ok": true}
  POST /inbox          — JSON body with fields (id, body);
                        writes ~/workspace/inbox/from-hermes/<id>.md, 200 {"ok": true}
  GET  /health         — 200 {"status": "ok"}

Stdlib only. Logs to ~/workspace/inbox/listener.log.
Robust: every request is wrapped in exception handling; a bad request
never kills the server.
"""
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = "0.0.0.0"  # All interfaces — reachable via Tailscale (<your-vm-tailnet-ip>), not publicly exposed
PORT = 8899
VERSION = "1.2.0"

# For /health/detailed — track server start and last event
START_TIME = time.time()
LAST_EVENT_AT = {"ts": 0}
LAST_EVENT_LOCK = threading.Lock()

HOME = os.path.expanduser("~")
WORKSPACE = os.path.join(HOME, "workspace")
INBOX = os.path.join(WORKSPACE, "inbox")
KANBAN_PUSH_DIR = os.path.join(INBOX, "kanban-push")
FROM_HERMES_DIR = os.path.join(INBOX, "from-hermes")
LOG_FILE = os.path.join(INBOX, "listener.log")

# Heartbeat for the work-guard failsafe (see docs/WORK-GUARD.md) — keeps the failsafe from
# logging a bogus RESTART every 15 minutes. Guard only checks mtime.
HEARTBEAT_FILE = os.path.join(WORKSPACE, ".work-heartbeat-muse-inbox-listener")
HEARTBEAT_INTERVAL_S = 300

# Event IDs are used as filenames — reject anything that could escape the dir.
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# --- Option A completion verification (event-driven, Nov 15, 2026) ---------
# On a kanban "completed" event, run the Prismatic kanban adapter via
# kanban-verify.sh in a daemon thread (never blocks the HTTP response).
# Success = exit 0 and the last stdout line carries VERDICT. Three attempts,
# 20s apart; final failure writes an incident file into FROM_HERMES_DIR,
# which the inbox trigger surfaces in the main chat (fail-loud). Design:
# the Option A completion-verification hook plan (Nov 15, 2026).
VERIFY_SCRIPT = os.path.join(WORKSPACE, "bin", "kanban-verify.sh")
VERIFY_ATTEMPTS = 3
VERIFY_RETRY_DELAY_S = 20
VERIFY_TIMEOUT_S = 150
_VERIFY_INFLIGHT = set()
_VERIFY_LOCK = threading.Lock()


def _event_log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(LOG_FILE, "a") as f:
            f.write("[%s] VERIFY %s\n" % (ts, msg))
    except Exception:
        pass


def _verify_completion(task_id, event_id):
    """Run the kanban adapter for one completed task, with retries.

    Advisory only: the adapter posts its own verdict comment. Any failure of
    the checker itself ends in an incident file, never in silence.
    """
    try:
        last_out = ""
        for attempt in range(1, VERIFY_ATTEMPTS + 1):
            try:
                r = subprocess.run(
                    [VERIFY_SCRIPT, task_id],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=VERIFY_TIMEOUT_S,
                )
                lines = (r.stdout or "").strip().splitlines()
                last_out = ((r.stdout or "") + (r.stderr or "")).strip()
                if r.returncode == 0 and lines and "VERDICT" in lines[-1]:
                    _event_log(
                        "ok task=%s event=%s attempt=%d verdict=%s"
                        % (task_id, event_id, attempt, lines[-1][:120])
                    )
                    return
            except Exception as exc:
                last_out = "verify run error: %s" % exc
            _event_log(
                "fail task=%s event=%s attempt=%d" % (task_id, event_id, attempt)
            )
            if attempt < VERIFY_ATTEMPTS:
                time.sleep(VERIFY_RETRY_DELAY_S)
        incident = os.path.join(
            FROM_HERMES_DIR, "kanban-verify-failure-%s.md" % event_id
        )
        body = (
            "# Kanban verify: adapter failure\n\n"
            "The Option A completion check ran the kanban adapter %d times on a\n"
            "completed task and it failed every time. The verdict for this\n"
            "completion was NOT posted automatically. The 30-minute review\n"
            "sweep remains the backstop and runs the same check in review.\n\n"
            "- task_id: %s\n- event_id: %s\n- attempts: %d\n"
            "- last adapter output (tail):\n\n%s\n"
            % (
                VERIFY_ATTEMPTS,
                task_id,
                event_id,
                VERIFY_ATTEMPTS,
                "\n".join(last_out.splitlines()[-5:]),
            )
        )
        try:
            os.makedirs(FROM_HERMES_DIR, exist_ok=True)
            with open(incident, "w") as f:
                f.write(body)
            _event_log("incident filed task=%s event=%s" % (task_id, event_id))
        except Exception as exc:
            _event_log(
                "incident write FAILED task=%s event=%s err=%s"
                % (task_id, event_id, exc)
            )
    finally:
        with _VERIFY_LOCK:
            _VERIFY_INFLIGHT.discard(task_id)


def _spawn_verify(task_id, event_id):
    """Start a verify thread for a task unless one is already in flight."""
    with _VERIFY_LOCK:
        if task_id in _VERIFY_INFLIGHT:
            _event_log(
                "skip duplicate in-flight task=%s event=%s" % (task_id, event_id)
            )
            return
        _VERIFY_INFLIGHT.add(task_id)
    t = threading.Thread(
        target=_verify_completion,
        args=(task_id, event_id),
        name="verify-%s" % task_id,
        daemon=True,
    )
    t.start()


# --- Event-driven review trigger (Nov 15, 2026) -----------------------------
# Significant kanban events (the retired fleet-review-trigger hook's exact
# set) are captured at POST time into a durable pending list. A flusher
# thread writes ~/workspace/.flags/kanban-review-needed after a 45s quiet
# window; the hermes-inbox-trigger wakes an agent on the flag, and the
# agent runs one review pass per the procedure at _PROCEDURE_PATH,
# then deletes the flag. No polling anywhere in this path.
FLAGS_DIR = os.path.join(WORKSPACE, ".flags")
REVIEW_PENDING_FILE = os.path.join(INBOX, ".review-pending.json")
REVIEW_FLAG_FILE = os.path.join(FLAGS_DIR, "kanban-review-needed")
REVIEW_QUIET_S = 45
REVIEW_FLUSH_TICK_S = 5
SIGNIFICANT_KINDS = {
    "completed",
    "blocked",
    "crashed",
    "failed",
    "gave_up",
    "gave-up",
    "timed_out",
}
_PENDING_LOCK = threading.Lock()
_EVENT_LINE = re.compile(r"^event (\S+) task (\S+) kind (\S+) status (\S+)")
# Path to the review-pass procedure the woken agent must follow.
_PROCEDURE_PATH = "~/workspace/review-pass-procedure.md"


def _pending_load():
    try:
        with open(REVIEW_PENDING_FILE) as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("events"), list):
            return d
    except Exception:
        pass
    return {"events": [], "updated_at": 0}


def _pending_save(d):
    tmp = REVIEW_PENDING_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, REVIEW_PENDING_FILE)


def _review_capture(data, event_id):
    """Record a significant event for the next review-flag flush."""
    kind = str(data.get("kind", ""))
    status = str(data.get("status", ""))
    if kind not in SIGNIFICANT_KINDS and status != "review":
        return
    title = str(data.get("title", "")).replace("\n", " ")[:120]
    ev = {
        "id": event_id,
        "task_id": str(data.get("task_id", "")),
        "kind": kind,
        "status": status,
        "title": title,
    }
    with _PENDING_LOCK:
        d = _pending_load()
        if any(e.get("id") == event_id for e in d["events"]):
            return
        d["events"].append(ev)
        d["updated_at"] = int(time.time())
        _pending_save(d)
    _event_log(
        "REVIEW captured event=%s task=%s kind=%s" % (event_id, ev["task_id"], kind)
    )


def _flag_event_lines(events):
    return [
        "event %s task %s kind %s status %s - %s"
        % (e["id"], e["task_id"], e["kind"], e["status"], e["title"])
        for e in events
    ]


def _review_flush_once():
    """Write the review flag if the pending list has gone quiet."""
    with _PENDING_LOCK:
        d = _pending_load()
        if not d["events"]:
            return
        if int(time.time()) - int(d.get("updated_at", 0)) < REVIEW_QUIET_S:
            return
        events = d["events"]
        # Merge with an unconsumed flag: keep its events, append new ones.
        merged = []
        seen = set()
        try:
            with open(REVIEW_FLAG_FILE) as f:
                for line in f:
                    m = _EVENT_LINE.match(line.strip())
                    if m and m.group(1) not in seen:
                        seen.add(m.group(1))
                        merged.append(line.strip())
        except OSError:
            pass
        for line in _flag_event_lines(events):
            eid = line.split(" ")[1]
            if eid not in seen:
                seen.add(eid)
                merged.append(line)
        try:
            os.makedirs(FLAGS_DIR, exist_ok=True)
            body = (
                "KANBAN-REVIEW-NEEDED\n"
                "Run ONE event-triggered review pass for the tasks below, "
                "following %s exactly, then delete this file.\n\n%s\n"
                % (_PROCEDURE_PATH, "\n".join(merged))
            )
            tmp = REVIEW_FLAG_FILE + ".tmp"
            with open(tmp, "w") as f:
                f.write(body)
            os.replace(tmp, REVIEW_FLAG_FILE)
        except Exception as exc:
            _event_log("REVIEW flag write FAILED err=%s" % exc)
            return
        _pending_save({"events": [], "updated_at": 0})
        _event_log("REVIEW flag written events=%d" % len(events))


def _review_flusher():
    while True:
        try:
            _review_flush_once()
        except Exception as exc:
            _event_log("REVIEW flusher error err=%s" % exc)
        time.sleep(REVIEW_FLUSH_TICK_S)


def log(method, path, status):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = "[%s] %s %s -> %s\n" % (ts, method, path, status)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "MuseInbox/1.0"

    def _send_json(self, status_code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        log(self.command, self.path.split("?")[0], status_code)

    def _read_json_body(self):
        """Read and parse the request body as JSON. Returns (data, error_msg)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0 or length > 1_048_576:  # 1 MB cap
            return None, "empty or oversized body"
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            return None, "malformed JSON: %s" % exc
        if not isinstance(data, dict):
            return None, "JSON body must be an object"
        return data, None

    def do_GET(self):
        try:
            path = self.path.split("?")[0]
            if path == "/health":
                self._send_json(200, {"status": "ok"})
            elif path == "/health/detailed":
                self._handle_health_detailed()
            else:
                self._send_json(404, {"ok": False, "error": "not found"})
        except Exception:
            try:
                self._send_json(500, {"ok": False, "error": "internal error"})
            except Exception:
                pass

    def _handle_health_detailed(self):
        """Detailed health: tunnel status, daemon status, last event, uptime."""
        import subprocess

        result = {
            "status": "ok",
            "version": VERSION,
            "listener_uptime_secs": int(time.time() - START_TIME),
            "tunnel": "unknown",
            "push_daemon": "unknown",
            "last_event_at": 0,
        }

        # Last event timestamp (thread-safe read)
        with LAST_EVENT_LOCK:
            result["last_event_at"] = LAST_EVENT_AT["ts"]

        # Tunnel check: can we SSH to the box? (short timeout, don't block)
        try:
            r = subprocess.run(
                [os.path.join(WORKSPACE, "bin", "mwh-safe.sh"), "echo ok"],
                capture_output=True, text=True, timeout=15,
            )
            result["tunnel"] = "up" if r.returncode == 0 and "ok" in r.stdout else "down"
        except Exception:
            result["tunnel"] = "down"

        # Push daemon check: is it running on the box?
        try:
            r = subprocess.run(
                [os.path.join(WORKSPACE, "bin", "mwh-safe.sh"),
                 "pgrep -f kanban-events-daemon.py >/dev/null && echo running || echo stopped"],
                capture_output=True, text=True, timeout=15,
            )
            out = r.stdout.strip().split("\n")[-1] if r.stdout else ""
            result["push_daemon"] = "running" if "running" in out else "stopped"
        except Exception:
            result["push_daemon"] = "unknown"

        # Overall status degrades if components are down
        if result["tunnel"] == "down" or result["push_daemon"] == "stopped":
            result["status"] = "degraded"

        self._send_json(200, result)

    def do_POST(self):
        try:
            path = self.path.split("?")[0]
            if path == "/kanban-event":
                self._handle_kanban_event()
            elif path == "/inbox":
                self._handle_inbox()
            else:
                self._send_json(404, {"ok": False, "error": "not found"})
        except Exception:
            try:
                self._send_json(500, {"ok": False, "error": "internal error"})
            except Exception:
                pass

    def _handle_kanban_event(self):
        data, err = self._read_json_body()
        if err:
            self._send_json(400, {"ok": False, "error": err})
            return
        for field in ("id", "task_id", "kind"):
            if field not in data:
                self._send_json(400, {"ok": False, "error": "missing field: %s" % field})
                return
        event_id = str(data["id"])
        if not SAFE_ID.match(event_id):
            self._send_json(400, {"ok": False, "error": "invalid id"})
            return
        try:
            os.makedirs(KANBAN_PUSH_DIR, exist_ok=True)
            dest = os.path.join(KANBAN_PUSH_DIR, event_id + ".json")
            with open(dest, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            # Record for /health/detailed
            with LAST_EVENT_LOCK:
                LAST_EVENT_AT["ts"] = int(time.time())
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": "write failed: %s" % exc})
            return
        # Event-driven Option A check: a completed task gets its adapter
        # verdict without waiting for any poll. Never blocks the response.
        if str(data.get("kind", "")) == "completed" and data.get("task_id"):
            try:
                _spawn_verify(str(data["task_id"]), event_id)
            except Exception as exc:
                _event_log("spawn failed event=%s err=%s" % (event_id, exc))
        # Event-driven review trigger: capture significant events for the
        # debounced kanban-review-needed flag. Never blocks the response.
        try:
            _review_capture(data, event_id)
        except Exception as exc:
            _event_log("REVIEW capture failed event=%s err=%s" % (event_id, exc))
        self._send_json(200, {"ok": True})

    def _handle_inbox(self):
        data, err = self._read_json_body()
        if err:
            self._send_json(400, {"ok": False, "error": err})
            return
        if "id" not in data or "body" not in data:
            self._send_json(400, {"ok": False, "error": "missing field: id and body required"})
            return
        msg_id = str(data["id"])
        if not SAFE_ID.match(msg_id):
            self._send_json(400, {"ok": False, "error": "invalid id"})
            return
        try:
            os.makedirs(FROM_HERMES_DIR, exist_ok=True)
            dest = os.path.join(FROM_HERMES_DIR, msg_id + ".md")
            with open(dest, "w") as f:
                f.write(str(data["body"]))
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": "write failed: %s" % exc})
            return
        self._send_json(200, {"ok": True})

    def log_message(self, format, *args):
        # Suppress stderr noise; all logging goes to listener.log
        pass


def _heartbeat_loop():
    """Touch the work-guard heartbeat file so the failsafe knows we're alive."""
    while True:
        try:
            with open(HEARTBEAT_FILE, "w") as f:
                f.write(str(time.time()))
        except OSError:
            pass
        time.sleep(HEARTBEAT_INTERVAL_S)


def main():
    os.makedirs(INBOX, exist_ok=True)
    os.makedirs(KANBAN_PUSH_DIR, exist_ok=True)
    os.makedirs(FROM_HERMES_DIR, exist_ok=True)
    # Self-heartbeat: daemon thread, never blocks shutdown.
    t = threading.Thread(target=_heartbeat_loop, name="work-guard-heartbeat", daemon=True)
    t.start()
    # Review-flag flusher: daemon thread, flushes the pending review list
    # to ~/workspace/.flags/kanban-review-needed after a quiet window.
    os.makedirs(FLAGS_DIR, exist_ok=True)
    rt = threading.Thread(target=_review_flusher, name="review-flusher", daemon=True)
    rt.start()
    server = HTTPServer((HOST, PORT), Handler)
    log("START", "listener", "listening on %s:%d" % (HOST, PORT))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
