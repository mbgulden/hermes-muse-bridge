# kanban-stream

[![tests](https://github.com/mbgulden/hermes-kanban-stream/actions/workflows/tests.yml/badge.svg)](https://github.com/mbgulden/hermes-kanban-stream/actions/workflows/tests.yml)

Live-stream your Hermes kanban workers to Telegram. One message per task,
**edited in place** as the worker goes — rolling tool-call history, elapsed
timer, total output tokens, a thinking indicator, and a pinned final state
(✅ done / 🚫 blocked). Mid-task, you can **steer** a running worker from
Telegram without interrupting it.

```
🔧 @ned — t_77a5d343
📋 Prepare kanban-stream plugin for public release (repo + marketplace)
⏱️ 12m 04s elapsed · 9,412 out tok
---
🔍 terminal command='bash /tmp/catalog_probe.sh'
🔍 read_file path='/tmp/hupstream/plugin-catalog/README.md'
🔍 write_file path='/home/.../release/kanban-stream/plugin.yaml'
💭 Thinking…
```

```
✅ @ned — t_77a5d343
📋 Prepare kanban-stream plugin for public release (repo + marketplace)
⏱️ 31m 20s total · 24,860 out tok
---
Done. Release package prepared; handed to muse for review.
```

The message stays pinned so the final state survives the scroll.

## What it does / doesn't

**Does**

- Hooks into the kanban worker lifecycle: `pre_tool_call`,
  `post_tool_call`, `pre_llm_call`, `post_api_request`,
  `kanban_task_completed`, `kanban_task_blocked`.
- Edits one Telegram message per task (throttled to ≤1 edit / 5 s,
  thinking indicator ≤1 / 30 s) instead of flooding the chat.
- Shows per-request completion tokens from `post_api_request` usage as
  a running total of output tokens over the task's lifetime.
- **Steering (Phase 2):** a `[steer] …` comment on the task is
  delivered to the running worker by Hermes core itself (core hands
  every new non-self task comment to the worker), and the plugin
  mirrors a "✉️ Steer received" line into the stream when one lands
  (watch polls the comment thread ≤1 / 6 s, display only — the plugin
  never injects steer text). Post steers from the board, or from
  Telegram through the companion daemon.
- `[no-stream]` opt-out: add the marker to a task's body to suppress
  streaming (steering still works — core delivers it).

**Doesn't**

- Doesn't touch the worker's tools, model, or approvals — it only
  observes hooks and appends one comment per steer.
- Doesn't send anything for non-kanban sessions (CLI, cron, gateway
  chats are untouched).
- Never logs or transmits credential values.

## How it works (architecture)

```
                 ┌──────────────────────────── kanban worker (profile P) ───────────────────────────┐
                 │  pre_tool_call ──► push_tool ─┐                                                  │
                 │  post_tool_call ─► push_error ─┤   ┌──────────────────┐   ┌─────────────────┐    │
                 │  pre_llm_call ───► push_think ─┼─► │  _TaskStream     │   │ kanban.db       │    │
                 │  post_api_request ─► tokens ───┤   │ (1 per task,     │◄──│ task_comments   │    │
                 │  task done/blocked ─► finish ──┘   │  edit-in-place)  │   │ [steer] polling │    │
                 └───────────────────────────────────│──────────────────│   └─────────────────┘    │
                                                     └────────┬─────────┘                            │
                                                              │ fire-and-forget (daemon threads,     │
                                                              │ fail-open, 5 s timeout)              │
                                                              ▼                                      │
                                                    Telegram Bot API                                  │
                                                    (sendMessage / editMessageText / pinChatMessage)  │
                                                                                                       │
                 ┌──────────────────────────────┐    ▲                                                  │
                 │ companion daemon bin/steer.py │───┘ (allowlisted /steer from Telegram →              │
                 │ long-poll getUpdates         │    [steer] comment via hermes CLI)                    │
                 └──────────────────────────────┘                                                        │
```

- **One message per task, edited in place.** The first tool call sends
  `sendMessage`; everything after is `editMessageText` on the same
  message id. Edits are throttled (max 1 / 5 s) and a delayed "catch-up"
  edit is scheduled so the final frame is never dropped.
- **Tokens/sec** accumulates `usage.output_tokens` from
  `post_api_request` and divides by elapsed task time.
- **Fire-and-forget, fail-open.** All Telegram calls run in daemon
  threads; any network error is logged and swallowed. A Telegram outage
  can never stall, crash, or alter a kanban worker.
- **Steering round-trip is comment-based.** The write side (companion
  daemon, or any human/agent with board access) posts a comment
  `[steer] <instruction>` — the daemon via the public
  `hermes kanban comment` CLI, never raw SQL — and Hermes core
  delivers the comment to the running worker. The plugin's read side
  only watches the thread to show "✉️ Steer received" in the stream.
  No private APIs, no fork-only surface — public CLI + public hooks.
- **Per-task state** lives in a module-level dict, cleaned up 60 s after
  the final edit. Multiple workers in one gateway process stream
  independently (keyed by `HERMES_KANBAN_TASK`).

## Install

Requires Hermes with the kanban system. Works on upstream Hermes
(`requires_hermes: >=0.20.5`) — verified against the v0.20.x line and
current `main`.

```bash
# from the standalone repo (recommended)
hermes plugins install https://github.com/mbgulden/hermes-kanban-stream

# the plugin ships disabled by default (network-calling) — enable it
hermes plugins enable kanban-stream
```

From a local checkout, copy the package into your plugins dir instead
(the installer treats its argument as a repo reference):

```bash
cp -r kanban-stream ~/.hermes/plugins/
hermes plugins enable kanban-stream
```

The install lands in `~/.hermes/plugins/kanban-stream/` (or your
profile's plugins dir). No Python dependencies beyond the standard
library.

### Which profiles does it stream for?

The plugin is **active only inside kanban workers**
(`HERMES_SESSION_SOURCE=kanban`) and reads credentials through the
**active profile's secret scope**. That means each streaming profile
must have the `KANBAN_STREAM_*` vars set (see below) — a worker under
profile `kai` streams with the creds in `kai`'s scope, not someone
else's. To build a single ops dashboard, point every streaming
profile at the *same* dedicated bot (same token + same
`KANBAN_STREAM_OPS_CHAT_ID`); that is the intended multi-agent setup.

## Setup walkthrough (~20 min)

### 1. Create a dedicated Telegram bot (5 min)

1. In Telegram, message **@BotFather** → `/newbot`.
2. Pick a name and a username (e.g. `MyOpsStreamBot`). Copy the **bot
   token** (`123456…:AA…`).
3. **Disable privacy mode** so the bot can see your messages in groups
   (irrelevant for pure 1:1 streaming, required if you put the bot in a
   group and use `/steer` there): `/setprivacy` → your bot →
   **Disable**.
4. Open a chat with the bot and send any message — this authorizes it.
5. Get your numeric Telegram user id: message **@userinfobot**.

> Use a *dedicated* bot, not your profile's chat bot. The plugin
> deliberately uses namespaced `KANBAN_STREAM_*` env vars precisely so
> it never collides with the built-in Telegram platform's
> `TELEGRAM_BOT_TOKEN`.

### 2. Put credentials in the profile `.env` (2 min)

For **each profile that runs streaming kanban workers**, add to
`$HERMES_HOME/profiles/<profile>/.env` (or your profile's `.env`):

```env
KANBAN_STREAM_BOT_TOKEN=123456789:AA...your-bot-token...
KANBAN_STREAM_ALLOWED_USERS=111111111
KANBAN_STREAM_OPS_CHAT_ID=          # leave empty for 1:1 with you
```

- `KANBAN_STREAM_ALLOWED_USERS` — comma-separated numeric ids. **First
  id** = the 1:1 stream destination when `KANBAN_STREAM_OPS_CHAT_ID`
  is empty. Also the allowlist for `/steer` (companion daemon).
- `KANBAN_STREAM_OPS_CHAT_ID` — for a group dashboard: the bot's group
  chat id (negative, e.g. `-1001234567890`). The bot must be a group
  member; privacy mode off (step 1.3).
- A full commented template lives in `.env.example`.

Hermes resolves these via `agent.secret_scope.get_secret()` — the
correct route under profile multiplexing. (When the secret-scope
machinery is unavailable — older Hermes or non-gateway runs — the
plugin falls back to reading the same names from the process
environment.) Set them, then restart the gateway/worker so the env
is picked up.

### 3. Enable the plugin and watch a task (2 min)

```bash
hermes plugins enable kanban-stream
hermes plugins doctor kanban-stream   # optional: validate install
```

Create (or wait for) a kanban task assigned to a streaming profile.
Within seconds of the worker's first tool call, the message appears.
Final state is pinned 5 s after completion.

### 4. (Optional) Mid-task steering (3 min)

Steering works two ways:

- **From the board** (always available): comment
  `[steer] try the other approach` on the running task — from any
  Hermes session, the kanban dashboard, or an API call. The worker
  picks it up within ~6 s.
- **From Telegram** (companion daemon): run `bin/steer.py` with the
  same env (see below), then in the bot chat send
  `/steer t_77a5d343 try the other approach`. The daemon verifies the
  sender against the allowlist **before parsing**, checks the task is
  running, posts the `[steer]` comment, and confirms.

```bash
export KANBAN_STREAM_BOT_TOKEN=... KANBAN_STREAM_ALLOWED_USERS=...
python3 /path/to/plugins/kanban-stream/bin/steer.py --check  # verify config
python3 /path/to/plugins/kanban-stream/bin/steer.py          # run (foreground)
```

Run the daemon under systemd/tmux/supervisor — it's a long-poll
process. One daemon per bot token. Note: if the worker is about to
finish, a late steer is a no-op (steering applies to the next LLM turn
of a *running* task).

## Configuration reference

| Env var | Required | Meaning |
|---|---|---|
| `KANBAN_STREAM_BOT_TOKEN` | yes | Ops bot token (BotFather). |
| `KANBAN_STREAM_ALLOWED_USERS` | yes | Comma-separated Telegram user ids; first = 1:1 destination when no ops chat set; allowlist for `/steer`. |
| `KANBAN_STREAM_OPS_CHAT_ID` | no | Explicit destination chat (group id or user id). |
| `KANBAN_STREAM_STEER_AUTHOR` | no | Author label on daemon-posted `[steer]` comments (default `operator`). |
| `HERMES_KANBAN_DB` | no | Explicit kanban DB path (auto-derived otherwise). |

Task-level opt-out: `[no-stream]` anywhere in the task body.

## Troubleshooting

**Nothing shows up, and logs say `KANBAN_STREAM_BOT_TOKEN is not set`.**
The var isn't in the *active profile's* secret scope. Check the `.env`
of the profile the worker runs under, and that the gateway/worker was
restarted after the change. Under multiplexed gateways, an unset
credential is deliberately never borrowed from another profile.

**Every send 401s.** The token in `KANBAN_STREAM_BOT_TOKEN` is wrong or
the bot was renamed/revoked. `curl https://api.telegram.org/bot<TOKEN>/getMe`
(then delete it from history) should return the bot. Note: don't reuse
your chat bot's `TELEGRAM_BOT_TOKEN` here by mistake — that's a
different var, but a copy-paste slip is the usual cause.

**Stream works but `/steer` does nothing.**
1. Daemon not running, or running with a *different* token than the
   bot you're texting.
2. Your id isn't in `KANBAN_STREAM_ALLOWED_USERS` — non-allowlisted
   senders are dropped **silently by design** (no reply, no log of
   content). Add your id, restart the daemon (or wait ≤5 min for the
   allowlist refresh).
3. The task isn't `running`/`in_progress` — steering a finished task is
   a no-op (the daemon says so).
4. The daemon's `--check` output shows which DB it writes to; a steer
   you can't see in the board means the daemon and the board are using
   different `kanban.db` files (set `HERMES_KANBAN_DB` on both).

**Stale behaviour after a plugin update.** Delete the plugin's
`__pycache__` (`~/.hermes/plugins/kanban-stream/__pycache__/`) and
restart the worker — Python won't reload a cached `.pyc` if mtimes
collide, and it's the classic "I changed it but it behaves like before"
cause.

**Message appears twice / edits race.** Two daemons or two workers with
the *same* bot token and the same task id. One stream per
`(profile, task)` is expected; a duplicated stream means the task id
leaked into another session — check `HERMES_KANBAN_TASK` in the worker
env.

**Telegram 429s (flood control).** Lower your concurrency, not your
throttle — the 5 s edit throttle is already conservative. A burst of
429s from many tasks at once is normal in a big swarm; the plugin
fail-opens and the next edit retries.

## Privacy & disclosure (please read)

- **Network calls:** outbound HTTPS to `api.telegram.org` only
  (sendMessage / editMessageText / pinChatMessage / getUpdates for the
  companion daemon). No other hosts.
- **Data sent to Telegram:** task title, task id, profile name, tool
  names, *first 80 chars* of a tool argument (command/path/JSON
  summary), elapsed/tok stats, and the final summary text (first 200
  chars). **Steer instructions flow both ways** (Telegram → task
  comment → worker, delivered by core). If your tool arguments contain secrets,
  remember the summary line goes to the ops chat.
- **Reads:** the kanban SQLite DB (read-only: task title/body,
  comments, task status) and the profile secret scope for the three
  `KANBAN_STREAM_*` vars. Nothing else on disk.
- **No telemetry, no analytics, no updates check.** The plugin never
  phones home except to your bot.
- **The companion daemon** long-polls `getUpdates` with the same token
  (25 s timeout) and stores one integer (the update offset) at
  `$HERMES_HOME/plugin-data/kanban-stream/steer-offset`.

## Support & expectations

This plugin is maintained by one person, in the open, alongside the
fleet it was built for. Issues are triaged in batches rather than
daily; a good bug report (use the issue templates — the relevant log
lines, and `bin/steer.py --check` output if steering is involved) gets
a faster answer than a vague one. There is no SLA and no paid support
tier. Fixes ship as tagged releases, and the smoke suite in `tests/`
runs in CI on every push, so the badge at the top of this README is
the current truth about the code.

## License

MIT — see `LICENSE`.

## Distribution

- **Standalone (today):** `hermes plugins install
  https://github.com/mbgulden/hermes-kanban-stream`
- **Catalog (when listed):** `hermes plugins install kanban-stream`
  after the entry is merged into the Hermes plugin catalog
  (`NousResearch/hermes-agent` → `plugin-catalog/kanban-stream.yaml`).
