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
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = "0.0.0.0"  # All interfaces — reachable via Tailscale (<your-vm-tailnet-ip>), not publicly exposed
PORT = 8899
VERSION = "1.0.0"

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
HEARTBEAT_FILE = os.path.join(WORKSPACE, ".work-heartbeat-inbox-listener")
HEARTBEAT_INTERVAL_S = 300

# Event IDs are used as filenames — reject anything that could escape the dir.
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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
