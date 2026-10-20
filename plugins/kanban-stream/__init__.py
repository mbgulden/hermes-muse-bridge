"""Kanban Stream Plugin for Hermes Agent — Phase 1c: formatting hardening.

Streams kanban worker activity (tool calls, thinking phases) to Michael's
1:1 chat with @StreamOpsB0tbot. One message per task, edited in place.

Phase 1a: skeleton + hooks (done). Phase 1b: Telegram forwarding (done).
Phase 1c: formatting hardening (this) — rolling window, elapsed timer,
edit counting, final-state pin, tokens/sec from post_api_request usage.
Phase 2: steering.
"""

import json
import logging
import os
import sqlite3
import threading
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

log = logging.getLogger("kanban-stream")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_STREAMOPS_ENV = "/home/ubuntu/.hermes/profiles/streamops/.env"
_KANBAN_DB = "/home/ubuntu/.hermes/kanban.db"

_EDIT_THROTTLE_SECONDS = 5    # max 1 edit / 5s per task
_THINK_THROTTLE_SECONDS = 30  # max 1 thinking indicator / 30s
_TOOL_HISTORY_MAX = 5         # rolling window of tool calls
_TG_TIMEOUT = 5.0             # Telegram API timeout


def _load_streamops_env() -> Dict[str, str]:
    """Read streamops .env (token, chat id). Never log values."""
    cfg: Dict[str, str] = {}
    try:
        with open(_STREAMOPS_ENV) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    except Exception as e:
        log.warning("kanban-stream: cannot read streamops .env: %s", e)
    return cfg


def _bot_token() -> str:
    return _load_streamops_env().get("TELEGRAM_BOT_TOKEN", "")


def _chat_id() -> str:
    cfg = _load_streamops_env()
    # 1:1 mode: send directly to Michael's user ID
    return cfg.get("TELEGRAM_OPS_CHAT_ID") or cfg.get("TELEGRAM_ALLOWED_USERS", "").split(",")[0].strip()


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------

def _is_kanban_worker() -> bool:
    return os.environ.get("HERMES_SESSION_SOURCE") == "kanban"


def _task_id() -> str:
    return os.environ.get("HERMES_KANBAN_TASK", "")


def _profile() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name
        p = get_active_profile_name() or "hermes"
        return "fred" if p == "orchestrator" else p
    except Exception:
        return "hermes"


# ---------------------------------------------------------------------------
# Task metadata (title + [no-stream] opt-out) from kanban DB, read-only
# ---------------------------------------------------------------------------

_task_meta_cache: Dict[str, Dict[str, Any]] = {}


def _task_meta(task_id: str) -> Dict[str, Any]:
    """Fetch task title/body from DB. Cached. Fail-open."""
    if task_id in _task_meta_cache:
        return _task_meta_cache[task_id]
    meta = {"title": task_id, "no_stream": False}
    try:
        conn = sqlite3.connect(f"file:{_KANBAN_DB}?mode=ro", uri=True, timeout=5)
        try:
            cur = conn.cursor()
            cur.execute("SELECT title, body FROM tasks WHERE id = ?", (task_id,))
            row = cur.fetchone()
            if row:
                meta["title"] = row[0] or task_id
                body = row[1] or ""
                meta["no_stream"] = "[no-stream]" in body.lower()
        finally:
            conn.close()
    except Exception as e:
        log.warning("kanban-stream: task meta lookup failed: %s", e)
    _task_meta_cache[task_id] = meta
    return meta


# ---------------------------------------------------------------------------
# Telegram client (fire-and-forget, fail-open)
# ---------------------------------------------------------------------------

def _tg_request(method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Synchronous Telegram Bot API call. Returns parsed JSON or None."""
    token = _bot_token()
    if not token:
        return None
    try:
        url = f"https://api.telegram.org/bot{token}/{method}"
        data = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TG_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning("kanban-stream: telegram %s failed: %s", method, e)
        return None


def _tg_async(method: str, params: Dict[str, Any],
              on_result: Optional[Any] = None) -> None:
    """Fire-and-forget Telegram call in a daemon thread."""
    def _worker():
        try:
            result = _tg_request(method, params)
            if on_result and result:
                try:
                    on_result(result)
                except Exception:
                    pass
        except Exception:
            pass  # fail open, never disrupt agent loop

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Per-task stream state
# ---------------------------------------------------------------------------

class _TaskStream:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.profile = _profile()
        meta = _task_meta(task_id)
        self.title = meta["title"]
        self.no_stream = meta["no_stream"]
        self.message_id: Optional[int] = None
        self.tool_history: List[str] = []
        self.start_time = time.time()
        self.last_edit = 0.0
        self.last_think = 0.0
        self.current_line = "💭 Thinking…"
        self.done = False
        self.edit_count = 0
        self.completion_tokens = 0
        self._lock = threading.Lock()
        self._pending_edit = False
        self._send_in_flight = False

    def add_completion_tokens(self, n: int):
        """Accumulate output tokens from a post_api_request hook."""
        if n <= 0:
            return
        with self._lock:
            self.completion_tokens += n

    def _tok_per_sec(self) -> str:
        elapsed_s = time.time() - self.start_time
        with self._lock:
            toks = self.completion_tokens
        if elapsed_s < 1 or toks <= 0:
            return ""
        return f"{toks / elapsed_s:.0f} tok/s"

    def _format(self, final: bool = False, blocked: bool = False,
                summary: str = "") -> str:
        elapsed_s = int(time.time() - self.start_time)
        if elapsed_s < 60:
            elapsed = f"{elapsed_s}s"
        else:
            elapsed = f"{elapsed_s // 60}m {elapsed_s % 60}s"
        tps = self._tok_per_sec()
        tps_suffix = f" · {tps}" if tps else ""

        if final:
            icon = "🚫" if blocked else "✅"
            state_line = f"⏱️ {elapsed} total{tps_suffix}"
        else:
            icon = "🔧"
            state_line = f"⏱️ {elapsed} elapsed{tps_suffix}"

        lines = [
            f"{icon} @{self.profile} — {self.task_id}",
            f"📋 {self.title}",
            state_line,
            "---",
        ]
        if final:
            if blocked:
                lines.append(f"🚫 Blocked: {summary[:200] if summary else 'no reason given'}")
            else:
                lines.append(f"Done. {summary[:200] if summary else ''}".strip())
        else:
            lines.extend(self.tool_history[-_TOOL_HISTORY_MAX:])
            lines.append(self.current_line)
        text = "\n".join(lines)
        return text[:4000]  # stay under Telegram's 4096 limit

    def _do_send_initial(self):
        chat = _chat_id()
        if not chat:
            return
        with self._lock:
            if self._send_in_flight or self.message_id is not None:
                return  # already sending or sent
            self._send_in_flight = True
        text = self._format()
        def _on_result(result):
            try:
                mid = result.get("result", {}).get("message_id")
                with self._lock:
                    self._send_in_flight = False
                    if mid:
                        self.message_id = mid
            except Exception:
                with self._lock:
                    self._send_in_flight = False
        _tg_async("sendMessage", {"chat_id": chat, "text": text},
                  on_result=_on_result)

    def _do_edit(self, final: bool = False, blocked: bool = False,
                 summary: str = ""):
        chat = _chat_id()
        if not chat:
            return
        with self._lock:
            mid = self.message_id
        text = self._format(final=final, blocked=blocked, summary=summary)
        if mid:
            def _on_result(result):
                # If edit failed (message gone), post fresh
                if not result.get("ok"):
                    self._do_send_initial()
            with self._lock:
                self.edit_count += 1
            _tg_async("editMessageText",
                      {"chat_id": chat, "message_id": mid, "text": text},
                      on_result=_on_result)
        else:
            self._do_send_initial()

    def _do_pin(self):
        """Pin the final message. Fail-open; bots can pin their own 1:1 messages."""
        chat = _chat_id()
        with self._lock:
            mid = self.message_id
        if not chat or not mid:
            return
        _tg_async("pinChatMessage",
                  {"chat_id": chat, "message_id": mid,
                   "disable_notification": True})

    def schedule_edit(self, force: bool = False):
        """Throttled edit: max 1 per _EDIT_THROTTLE_SECONDS."""
        if self.done or self.no_stream:
            return
        now = time.time()
        with self._lock:
            if not force and (now - self.last_edit) < _EDIT_THROTTLE_SECONDS:
                if not self._pending_edit:
                    self._pending_edit = True
                    delay = _EDIT_THROTTLE_SECONDS - (now - self.last_edit)
                    def _delayed():
                        time.sleep(delay)
                        with self._lock:
                            self._pending_edit = False
                        self.schedule_edit(force=True)
                    threading.Thread(target=_delayed, daemon=True).start()
                return
            self.last_edit = now
        self._do_edit()

    def push_tool(self, line: str):
        with self._lock:
            is_first = not self.tool_history and self.message_id is None and not self._send_in_flight
            self.tool_history.append(line)
            self.current_line = line
        if is_first:
            self._do_send_initial()
        else:
            # If initial send is in flight, the edit will happen on next call
            # after message_id is set; schedule anyway to catch up
            self.schedule_edit()

    def push_think(self):
        now = time.time()
        with self._lock:
            if (now - self.last_think) < _THINK_THROTTLE_SECONDS:
                return
            self.last_think = now
            self.current_line = "💭 Thinking…"
        self.schedule_edit()

    def push_error(self, line: str):
        with self._lock:
            self.tool_history.append(line)
            self.current_line = line
        self.schedule_edit()

    def finish(self, blocked: bool = False, summary: str = ""):
        with self._lock:
            if self.done:
                return
            self.done = True
        # Final edit bypasses throttle
        self._do_edit(final=True, blocked=blocked, summary=summary)
        # Pin the final message (fire-and-forget, after edit posts)
        def _delayed_pin():
            time.sleep(5)
            self._do_pin()
            log.warning("kanban-stream: task %s finished, %d edits total, %s",
                        self.task_id, self.edit_count,
                        self._tok_per_sec() or "no tok/s")
        threading.Thread(target=_delayed_pin, daemon=True).start()


_streams: Dict[str, _TaskStream] = {}
_streams_lock = threading.Lock()


def _stream() -> Optional[_TaskStream]:
    tid = _task_id()
    if not tid:
        return None
    with _streams_lock:
        s = _streams.get(tid)
        if s is None:
            s = _TaskStream(tid)
            _streams[tid] = s
        return s


# ---------------------------------------------------------------------------
# Arg summary (same extraction as prismatic_telemetry)
# ---------------------------------------------------------------------------

def _arg_summary(args: Optional[Dict[str, Any]]) -> str:
    if not args:
        return ""
    try:
        if "command" in args:
            return "command='%s'" % str(args["command"])[:80]
        elif "path" in args or "target_file" in args:
            return "path='%s'" % (args.get("path") or args.get("target_file"))
        else:
            return str(args)[:80]
    except Exception:
        return ""


def _is_error_result(result: Any) -> bool:
    if result is None:
        return False
    try:
        if isinstance(result, dict):
            if result.get("error") or result.get("success") is False:
                return True
            s = str(result).lower()
            return "error" in s and "no error" not in s
        s = str(result).lower()
        return ("error" in s or "failed" in s or "traceback" in s) and len(s) < 500
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Hook handlers
# ---------------------------------------------------------------------------

def on_pre_tool_call(tool_name: str, args: Optional[Dict[str, Any]] = None,
                     **kwargs: Any) -> None:
    try:
        if not _is_kanban_worker():
            return
        s = _stream()
        if s is None or s.no_stream:
            return
        summary = _arg_summary(args)
        line = f"🔍 {tool_name} {summary}".strip()
        s.push_tool(line)
    except Exception as e:
        log.warning("kanban-stream on_pre_tool_call failed: %s", e)


def on_post_tool_call(tool_name: str, result: Any = None,
                      **kwargs: Any) -> None:
    try:
        if not _is_kanban_worker():
            return
        if not _is_error_result(result):
            return  # success implied by next tool call
        s = _stream()
        if s is None or s.no_stream:
            return
        s.push_error(f"❌ {tool_name} failed")
    except Exception as e:
        log.warning("kanban-stream on_post_tool_call failed: %s", e)


def on_pre_llm_call(*args: Any, **kwargs: Any) -> None:
    try:
        if not _is_kanban_worker():
            return
        s = _stream()
        if s is None or s.no_stream:
            return
        # Don't create the message on thinking alone — wait for first tool call
        with _streams_lock:
            has_message = s.message_id is not None or len(s.tool_history) > 0
        if not has_message:
            return
        s.push_think()
    except Exception as e:
        log.warning("kanban-stream on_pre_llm_call failed: %s", e)


def on_post_api_request(task_id: Optional[str] = None,
                       usage: Optional[Dict[str, Any]] = None,
                       **kwargs: Any) -> None:
    """Accumulate per-request output tokens for tok/s display. Fail-open.

    NOTE: the hook's task_id kwarg is the SESSION id, not the kanban task
    id — always key the stream by the HERMES_KANBAN_TASK env var.
    """
    try:
        if not _is_kanban_worker():
            return
        if not usage or not isinstance(usage, dict):
            return
        toks = usage.get("output_tokens", 0)
        try:
            toks = int(toks)
        except (TypeError, ValueError):
            return
        if toks <= 0:
            return
        # Hook's task_id kwarg is the session id — key by kanban task env var.
        # Create the stream on demand: the first API request fires before
        # the first tool call, so the stream may not exist yet.
        s = _stream()
        if s is None or s.no_stream:
            return
        s.add_completion_tokens(toks)
    except Exception as e:
        log.warning("kanban-stream on_post_api_request failed: %s", e)


def on_task_done(task_id: Optional[str] = None,
                 summary: Optional[str] = None,
                 reason: Optional[str] = None, **kwargs: Any) -> None:
    try:
        if not _is_kanban_worker():
            return
        tid = task_id or _task_id()
        with _streams_lock:
            s = _streams.get(tid)
        if s is None or s.no_stream:
            return
        blocked = summary is None
        text = reason or summary or ""
        s.finish(blocked=blocked, summary=text)
        # Cleanup after final edit fires
        def _cleanup():
            time.sleep(60)
            with _streams_lock:
                _streams.pop(tid, None)
        threading.Thread(target=_cleanup, daemon=True).start()
    except Exception as e:
        log.warning("kanban-stream on_task_done failed: %s", e)


def register(ctx: Any) -> None:
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("kanban_task_completed", on_task_done)
    ctx.register_hook("kanban_task_blocked", on_task_done)
