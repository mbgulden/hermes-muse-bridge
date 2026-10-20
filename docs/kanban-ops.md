# Kanban Ops — Streaming & Steering Operator Guide

## Overview

The kanban-stream system gives you live visibility into Hermes background workers
and the ability to steer them mid-task, all from Telegram.

- **Streaming:** every kanban worker streams its activity (tool calls, thinking,
  errors) to your 1:1 chat with `@StreamOpsB0tbot`, one message per task, edited live.
- **Steering:** send `/steer <task-id> <instruction>` to redirect a running worker.
  No restart needed.

## Setup

### 1. Bot
`@StreamOpsB0tbot` is the dedicated bot for this system. All streaming and steering
goes through it — never the other bots.

### 2. Configuration
`/home/ubuntu/.hermes/profiles/streamops/.env` (600 permissions):

```
TELEGRAM_BOT_TOKEN=<bot token from @BotFather>
TELEGRAM_ALLOWED_USERS=<your Telegram user ID>
```

- `TELEGRAM_BOT_TOKEN` — from @BotFather. Never committed, never logged.
- `TELEGRAM_ALLOWED_USERS` — comma-separated Telegram user IDs. **Only these users
  can interact with the bot.** Everyone else is silently ignored (no reply, no error).

To find your Telegram user ID: message @userinfobot on Telegram.

### 3. BotFather privacy mode (manual step)
In @BotFather:
```
/setprivacy → @StreamOpsB0tbot → Enable
```
This restricts the bot to receiving only commands and mentions in groups,
reducing attack surface. (In 1:1 chats this has no effect, but enable it anyway.)

### 4. Start the bot
Message `/start` to `@StreamOpsB0tbot` on Telegram. This opens the 1:1 chat
where streams and steering happen.

## Streaming

When a kanban worker starts a task, you'll see:

```
🔧 @fred — t_8f3a2c1d
📋 Task title here
⏱️ 2m elapsed
---
🔍 exec command='ls /tmp'
💭 Thinking…
```

The message updates live (max 1 edit / 5 seconds). On completion:

```
✅ @fred — t_8f3a2c1d
📋 Task title here
⏱️ 4m total
---
Done. <summary>
```

Blocked tasks show `🚫 Blocked: <reason>`.

### Opt-out
Add `[no-stream]` anywhere in a task's description to disable streaming for that task.
Useful for sensitive work. Steering still works on `[no-stream]` tasks.

## Steering

### From Telegram (Michael)
```
/steer t_8f3a2c1d use the $75 flat rate, not $100
```

- `<task-id>` — the kanban task ID (from the stream message header).
- `<instruction>` — plain-language guidance. Be specific.

You'll get a confirmation: `✉️ Steer sent to @fred on t_8f3a2c1d`

The worker picks it up within 1–2 LLM turns and adjusts. You'll see the pivot
in the stream.

**Rules:**
- Only works on running tasks. Steering a completed/blocked task replies
  "already done" and does nothing.
- Only `TELEGRAM_ALLOWED_USERS` can steer. Others are silently ignored.
- Conflicting steers: last one wins (delivered in chronological order).

### From CLI (Jimmy / agents / automation)
```bash
HERMES_HOME=/home/ubuntu/.hermes hermes kanban comment <task-id> "[steer] <instruction>"
```

The `[steer]` prefix is required — regular comments are not injected as steering.

**Rate limit:** max 1 steer per task per 5 minutes for automated steering.
This prevents runaway loops. Michael's manual `/steer` commands are not rate-limited.

### What steering can and cannot do

**Can:**
- Redirect approach ("use X instead of Y")
- Add constraints ("also check the error case")
- Correct mistakes ("that file path is wrong, use /srv/data")

**Cannot:**
- Pause or resume a worker (injection happens at the next LLM turn)
- Change the task's core objective (block and requeue for pivots)
- Reach a crashed worker (steer waits; respawned workers don't see pre-crash steers)

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────┐
│  Telegram   │────▶│ kanban-events-   │────▶│  kanban.db   │
│ @StreamOps  │◀────│ daemon.py        │     │ task_comments│
│   B0tbot    │     │ (+ steer thread) │     └──────┬───────┘
└─────────────┘     └──────────────────┘            │
       ▲                                           │ poll
       │ stream                               ┌────▼───────┐
       │ (edit-in-place)                      │  Worker    │
       └──────────────────────────────────────│  plugin    │
                                              │ pre_llm_call│
                                              └────────────┘
```

1. Worker runs task → plugin streams to Telegram (Phase 1).
2. Michael sends `/steer` → daemon writes `[steer]` comment to DB.
3. Plugin polls comments on each LLM call → injects steer as context.
4. Worker adjusts approach mid-task.

## Troubleshooting

**No stream messages appearing:**
- Check the task doesn't have `[no-stream]` in its description.
- Check the daemon is running: `ps aux | grep kanban-events-daemon`
- Check the plugin loaded: look for `kanban-stream` in worker logs.

**Steer not taking effect:**
- Confirm the task is still `running` (not done/blocked).
- Wait 1–2 LLM turns (up to ~60s for slow workers).
- Check the comment landed: `hermes kanban show <task-id>` should show `[steer]`.

**Bot not responding to /steer:**
- Confirm your user ID is in `TELEGRAM_ALLOWED_USERS`.
- Check the daemon's steer thread is running (log: "steer poll loop started").
- Non-allowlisted users get silence by design — this is not a bug.

## Files

| Path | Purpose |
|------|---------|
| `~/.hermes/plugins/kanban-stream/` | Streaming + steering plugin (runtime) |
| `~/.hermes/bin/kanban-events-daemon.py` | Event daemon + /steer handler (runtime) |
| `~/.hermes/bin/steer_handler.py` | /steer command logic (runtime) |
| `~/.hermes/profiles/streamops/.env` | Bot token + allowlist (never committed) |
| `~/workspace/hermes-muse-bridge/` | Repo mirror (source of truth) |
