"""Kanban Stream Plugin for Hermes Agent — v1.0.3 (public release).

Streams kanban worker activity (tool calls, thinking phases) to a
Telegram ops channel. One message per task, edited in place. Final
messages are pinned.

Phase 1a: skeleton + hooks (done). Phase 1b: Telegram forwarding (done).
Phase 1c: formatting hardening — rolling window, elapsed timer,
edit counting, final-state pin, total output tokens from post_api_request usage.
Phase 2: steering via kanban task comments (write-side: the optional
bin/steer.py companion daemon posts /steer commands as [steer] comments
from Telegram through the public `hermes kanban comment` CLI; delivery
to the running worker is core's job — core hands every new non-self
task comment to the worker. Read-side, the plugin only watches for
[steer] comments to mirror a "Steer received" line into the stream).

Credentials are resolved through Hermes' secret scope
(agent.secret_scope.get_secret) per catalog Rule 11 — no .env files or
hardcoded absolute paths are read directly. All paths derive from
HERMES_HOME / HERMES_KANBAN_DB.
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

# Credential env var names (namespaced so this plugin never collides with
# the built-in Telegram gateway platform's own TELEGRAM_BOT_TOKEN /
# TELEGRAM_ALLOWED_USERS, which belong to the profile's chat bot).
_BOT_TOKEN_VAR = "KANBAN_STREAM_BOT_TOKEN"
_ALLOWED_USERS_VAR = "KANBAN_STREAM_ALLOWED_USERS"
_OPS_CHAT_VAR = "KANBAN_STREAM_OPS_CHAT_ID"

_EDIT_THROTTLE_SECONDS = 5    # max 1 edit / 5s per task
_THINK_THROTTLE_SECONDS = 30  # max 1 thinking indicator / 30s
_TOOL_HISTORY_MAX = 5         # rolling window of tool calls
_TG_TIMEOUT = 5.0             # Telegram API timeout

_warned: set = set()


def _hermes_home() -> str:
    """Hermes home root: HERMES_HOME (worker-injected) or ~/.hermes.

    A single-profile deployment may set HERMES_HOME to a profile home
    (~/.hermes/profiles/<name>); in that case the kanban root lives one
    level up. The kanban.db lookup below tolerates both.
    """
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def _kanban_db_path() -> str:
    """Kanban DB path: HERMES_KANBAN_DB (injected into workers) or
    <root>/kanban.db, trying both the profile home and the shared root."""
    pinned = os.environ.get("HERMES_KANBAN_DB")
    if pinned:
        return pinned
    root = _hermes_home()
    candidates = [
        os.path.join(root, "kanban.db"),
    ]
    # If HERMES_HOME points at a profile home, the shared board DB is
    # usually in the parent directory.
    if os.path.basename(root) in ("home",) or "/profiles/" in root:
        parent = os.path.dirname(root)
        candidates.append(os.path.join(parent, "kanban.db"))
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


def _secret(name: str) -> str:
    """Resolve a credential through the active profile's secret scope.

    agent.secret_scope.get_secret always exists at the supported Hermes
    floor, so there is deliberately no os.environ fallback: a scope
    error must surface (it is what stops cross-profile reads), never be
    swallowed into a silent env read. Call this only on a hook's own
    thread — a daemon thread does not carry the profile's secret scope.
    """
    from agent import secret_scope
    val = secret_scope.get_secret(name, "") or ""
    return val.strip()


def _bot_token() -> str:
    return _secret(_BOT_TOKEN_VAR)


def _chat_id() -> str:
    """Destination chat: explicit ops chat id, or the first allowlisted
    user (1:1 streaming mode)."""
    return _secret(_OPS_CHAT_VAR) or _secret(_ALLOWED_USERS_VAR).split(",")[0].strip()


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------

def _is_kanban_worker() -> bool:
    return os.environ.get("HERMES_SESSION_SOURCE") == "kanban"


def _task_id() -> str:
    return os.environ.get("HERMES_KANBAN_TASK", "")


# Public surface only: register() stores ctx.profile_name here; the
# plugin never imports the private hermes_cli.profiles module.
_profile_name = "hermes"


def _profile() -> str:
    return _profile_name


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
        conn = sqlite3.connect(f"file:{_kanban_db_path()}?mode=ro", uri=True, timeout=5)
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

def _tg_request(method: str, params: Dict[str, Any],
                token: str) -> Optional[Dict[str, Any]]:
    """Synchronous Telegram Bot API call. Returns parsed JSON or None.

    The token is resolved by the caller on a hook's own thread (the
    profile's secret scope does not travel to daemon threads) and
    handed in — no secret resolution happens inside this call.
    """
    if not token:
        if "no-token" not in _warned:
            _warned.add("no-token")
            log.warning(
                "kanban-stream: %s is not set in the active profile scope — "
                "no streaming until it is (see .env.example)", _BOT_TOKEN_VAR)
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


def _tg_async(method: str, params: Dict[str, Any], token: str,
              on_result: Optional[Any] = None) -> None:
    """Fire-and-forget Telegram call in a daemon thread.

    Token and chat id are resolved before this is called (on the hook's
    own thread) and travel with the params/token arguments — a daemon
    thread does not carry the profile's secret scope.
    """
    def _worker():
        try:
            result = _tg_request(method, params, token)
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
        self._token: Optional[str] = None   # resolved lazily, hook thread
        self._chat: Optional[str] = None    # resolved lazily, hook thread
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

    def _creds(self, resolve: bool = True):
        """(token, chat_id) pair for Telegram calls.

        Resolution goes through the profile's secret scope, which only
        exists on hook threads — so resolve-and-cache happens on the
        calling (hook) thread and the cached pair is what gets handed
        to _tg_async. Daemon-thread callbacks pass resolve=False and
        reuse the cache; they never touch the secret scope.
        """
        if resolve:
            if self._token is None:
                self._token = _bot_token()
            if self._chat is None:
                self._chat = _chat_id()
        return self._token, self._chat

    def add_completion_tokens(self, n: int):
        """Accumulate output tokens from a post_api_request hook."""
        if n <= 0:
            return
        with self._lock:
            self.completion_tokens += n

    def _token_suffix(self) -> str:
        """State-line suffix: total output tokens, or "" when none yet.

        (Replaces the old tok/s figure — cumulative tokens ÷ wall-clock
        elapsed, which read as a generation rate but mostly measured
        waiting. A true rate is not computable from per-request token
        counts alone, so the display shows the total instead.)
        """
        with self._lock:
            toks = self.completion_tokens
        if toks <= 0:
            return ""
        return f" · {toks:,} out tok"

    def _format(self, final: bool = False, blocked: bool = False,
                summary: str = "") -> str:
        elapsed_s = int(time.time() - self.start_time)
        if elapsed_s < 60:
            elapsed = f"{elapsed_s}s"
        else:
            elapsed = f"{elapsed_s // 60}m {elapsed_s % 60}s"
        tok_suffix = self._token_suffix()

        if final:
            icon = "🚫" if blocked else "✅"
            state_line = f"⏱️ {elapsed} total{tok_suffix}"
        else:
            icon = "🔧"
            state_line = f"⏱️ {elapsed} elapsed{tok_suffix}"

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

    def _do_send_initial(self, final: bool = False, blocked: bool = False,
                         summary: str = ""):
        token, chat = self._creds()
        if not chat:
            return
        with self._lock:
            if self._send_in_flight or self.message_id is not None:
                return  # already sending or sent
            self._send_in_flight = True
        text = self._format(final=final, blocked=blocked, summary=summary)
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
        _tg_async("sendMessage", {"chat_id": chat, "text": text}, token,
                  on_result=_on_result)

    def _do_edit(self, final: bool = False, blocked: bool = False,
                 summary: str = ""):
        token, chat = self._creds()
        if not chat:
            return
        with self._lock:
            mid = self.message_id
        text = self._format(final=final, blocked=blocked, summary=summary)
        if mid:
            def _on_result(result):
                # If the message is gone (deleted / too old to edit),
                # forget its id and post a fresh one carrying the same
                # state. Other failures (flood control, "message is not
                # modified") must NOT trigger a repost — that would
                # duplicate the stream message.
                if not result.get("ok"):
                    desc = str(result.get("description", "")).lower()
                    if ("message to edit not found" in desc
                            or "message can't be edited" in desc
                            or "message_id_invalid" in desc):
                        with self._lock:
                            self.message_id = None
                        self._do_send_initial(final=final, blocked=blocked,
                                              summary=summary)
            with self._lock:
                self.edit_count += 1
            _tg_async("editMessageText",
                      {"chat_id": chat, "message_id": mid, "text": text},
                      token, on_result=_on_result)
        else:
            self._do_send_initial(final=final, blocked=blocked,
                                  summary=summary)

    def _do_pin(self):
        """Pin the final message. Fail-open; bots can pin their own 1:1 messages.

        Runs on a delayed daemon thread: uses the cached credentials
        only (resolve=False) — the secret scope is never touched here.
        """
        token, chat = self._creds(resolve=False)
        with self._lock:
            mid = self.message_id
        if not chat or not mid or not token:
            return
        _tg_async("pinChatMessage",
                  {"chat_id": chat, "message_id": mid,
                   "disable_notification": True}, token)

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
            with self._lock:
                toks = self.completion_tokens
            log.warning("kanban-stream: task %s finished, %d edits total, %s",
                        self.task_id, self.edit_count,
                        f"{toks:,} out tok" if toks > 0
                        else "no output tokens")
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
            # Key-based signals only: a substring scan over a dict result
            # false-positives on tool output that merely mentions errors
            # (a file read, a log tail, a search hit).
            if result.get("error") or result.get("success") is False:
                return True
            status = str(result.get("status", "")).lower()
            return status in ("error", "failed", "failure")
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



# --- Phase 2: steer watch (display only — core delivers steers) ---
_steer_watermark: Dict[str, int] = {}    # {task_id: last_seen_comment_id}
_steer_last_poll: Dict[str, float] = {}  # {task_id: monotonic time}
_STEER_POLL_SECONDS = 6.0


def _watch_steer_comments(task_id) -> int:
    """Count new [steer] comments since the watermark (0 when none).

    Display-only: Hermes core already delivers every new non-self task
    comment to the running worker, so the plugin never injects steer
    text into the LLM turn (that double-delivered every steer). This
    watch exists solely to mirror a "Steer received" line into the
    stream. Seeds the watermark on first call (returns 0)."""
    import sqlite3 as _sql
    now = time.monotonic()
    last = _steer_last_poll.get(task_id, 0)
    if (now - last) < _STEER_POLL_SECONDS:
        return 0
    _steer_last_poll[task_id] = now
    seen = _steer_watermark.get(task_id)
    try:
        db_path = _kanban_db_path()
        conn = _sql.connect("file:%s?mode=ro" % db_path, uri=True, timeout=3)
        try:
            if seen is None:
                row = conn.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM task_comments WHERE task_id = ?",
                    (task_id,)).fetchone()
                _steer_watermark[task_id] = row[0]
                return 0
            rows = conn.execute(
                "SELECT id, author, body FROM task_comments "
                "WHERE task_id = ? AND id > ? ORDER BY id ASC",
                (task_id, seen)).fetchall()
            if rows:
                _steer_watermark[task_id] = max(r[0] for r in rows)
            count = 0
            own = (os.environ.get("HERMES_PROFILE") or "").strip()
            for cid, author, body in rows:
                body = (body or "").strip()
                if not body.startswith("[steer]"):
                    continue
                if (author or "").strip() == own:
                    continue
                count += 1
            return count
        finally:
            conn.close()
    except Exception as e:
        log.warning("kanban-stream steer watch failed: %s", e)
        return 0


def on_pre_llm_call(*args: Any, **kwargs: Any) -> Any:
    try:
        if not _is_kanban_worker():
            return None
        s = _stream()
        if s is None:
            return None
        # Steering delivery belongs to core — Hermes hands every new
        # non-self task comment to the running worker itself. The plugin
        # never injects steer text here (each steer used to arrive
        # twice); the watch only mirrors the stream line.
        if _watch_steer_comments(_task_id()) and not s.no_stream:
            s.push_error("\u2709\ufe0f Steer received")
        if s.no_stream:
            return None
        with _streams_lock:
            has_message = s.message_id is not None or len(s.tool_history) > 0
        if has_message:
            s.push_think()
        return None
    except Exception as e:
        log.warning("kanban-stream on_pre_llm_call failed: %s", e)
        return None


def on_post_api_request(task_id: Optional[str] = None,
                       usage: Optional[Dict[str, Any]] = None,
                       **kwargs: Any) -> None:
    """Accumulate per-request output tokens for the total-token display. Fail-open.

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
    global _profile_name
    _profile_name = getattr(ctx, "profile_name", None) or "hermes"
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("kanban_task_completed", on_task_done)
    ctx.register_hook("kanban_task_blocked", on_task_done)
